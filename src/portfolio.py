"""Phase 4 — the portfolio: what is held, how far it has come, what to do next.

Two of those three are arithmetic and live here. The third — whether the thesis
still holds — is deliberately not ours: `triggers.py` decides it from the XBRL
record and `monitoring_log` stores it, so `recommend` *reads* that verdict rather
than re-deriving one from price. Price knows when a position is down; only the
filings know whether that matters. A position with no monitor verdict is
unjudged, never cleared — the same missing-data invariant the triggers keep.

The rules are hold-biased on purpose; the reasoning sits with the thresholds in
`config.py`. Nothing here is investment advice — it counts the picks and their
progress toward `config.MOONSHOT_MULTIPLE`.

`fundamentals.py` is to `roic.py` what this is to the Portfolio page: the
arithmetic, with no I/O. The one exception is `fetch_prices`, which is the only
network call, and the CLI at the bottom; all SQL belongs to `db.py`.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
from pathlib import Path

import pandas as pd
import yfinance as yf

from src import config, db

# `portfolio_actions.action` (schema.sql), not `triggers.ACTIONS` — that table is
# uppercase and has no `add`, because the monitor answers a different question.
HOLD, ADD, TRIM, SELL, REVIEW = "hold", "add", "trim", "sell", "review"

# The monitor's verdict, mapped onto this vocabulary. HOLD is absent on purpose:
# a clean monitor pass is not a reason to act, so it falls through to the
# position rules below.
_FROM_MONITOR = {"SELL": SELL, "REVIEW": REVIEW, "TRIM": TRIM}

# --- CSV ---------------------------------------------------------------------
# The portfolio is the one table a user maintains by hand, so it gets an import.
# `quantity`/`buy_price` are accepted as aliases: they are what the tracker this
# feature moved from wrote, and an old export should still import.

REQUIRED_COLUMNS = ("ticker", "shares", "entry_price")
_ALIASES = {"quantity": "shares", "buy_price": "entry_price"}

# A book can hold a Frankfurt listing next to a Nasdaq one, and yfinance quotes
# each in its exchange's own currency. `currency` says which one a row is in;
# `convert` restates the book in a single one so the totals are arithmetic
# rather than nonsense. Two are supported because two are what EUR=X spans.
SUPPORTED_CURRENCIES = ("USD", "EUR")
DEFAULT_CURRENCY = "USD"

CSV_TEMPLATE = (
    "ticker,shares,entry_price,currency,entry_date,thesis\n"
    "MELI,10,150.00,USD,2024-01-15,Latam commerce + fintech flywheel\n"
    "ITRN,25,32.50,USD,2024-03-02,Aftermarket telematics, founder-led\n"
)


def parse_csv(text: str) -> list[dict]:
    """An uploaded CSV -> rows ready for `db.add_position`.

    Headers are matched case-insensitively and trimmed. Raises ValueError naming
    the offending row rather than importing a portfolio that is quietly wrong.
    """
    reader = csv.DictReader(io.StringIO(text))
    headers = {}
    for h in reader.fieldnames or []:
        key = (h or "").strip().lower()
        headers[_ALIASES.get(key, key)] = h

    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise ValueError(f"CSV is missing required column(s): {', '.join(missing)}")

    def cell(row: dict, name: str) -> str:
        return (row.get(headers[name], "") or "").strip() if name in headers else ""

    rows: list[dict] = []
    for i, row in enumerate(reader, start=2):  # row 1 is the header
        ticker = cell(row, "ticker").upper()
        if not ticker:
            continue  # blank line
        try:
            shares = float(cell(row, "shares"))
            entry_price = float(cell(row, "entry_price"))
        except ValueError:
            raise ValueError(f"Row {i} ({ticker}): shares and entry_price must be numbers")
        rows.append({
            "ticker": ticker,
            "shares": shares,
            "entry_price": entry_price,
            "currency": _currency(cell(row, "currency"), i, ticker),
            "entry_date": _date(cell(row, "entry_date"), i, ticker),
            "thesis": cell(row, "thesis") or None,
        })
    if not rows:
        raise ValueError("CSV contained no positions")
    return rows


def _currency(value: str, row: int, ticker: str) -> str:
    """The column is optional and defaults to USD, but a *wrong* one is fatal:
    silently pricing a EUR position as USD is the error this feature exists to
    prevent."""
    if not value:
        return DEFAULT_CURRENCY
    code = value.upper()
    if code not in SUPPORTED_CURRENCIES:
        raise ValueError(
            f"Row {row} ({ticker}): currency must be one of "
            f"{', '.join(SUPPORTED_CURRENCIES)}, got {value!r}"
        )
    return code


def _date(value: str, row: int, ticker: str) -> dt.date | None:
    if not value:
        return None  # db.add_position defaults to today
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Row {row} ({ticker}): entry_date must be YYYY-MM-DD, got {value!r}")


# --- currency ----------------------------------------------------------------


def _factor(native: str, display: str, rate: float) -> float:
    """`rate` is EUR per USD, the way Yahoo quotes `EUR=X`."""
    if native == display:
        return 1.0
    return rate if display == "EUR" else 1.0 / rate


def needs_fx(holdings: pd.DataFrame, display: str) -> bool:
    """Whether the book holds anything not already in `display`.

    False means no rate is needed, so neither the page nor the CLI pays for a
    quote to convert a single-currency book into its own currency.
    """
    if holdings.empty or "currency" not in holdings.columns:
        return False
    codes = holdings["currency"].dropna().astype("object").str.strip().str.upper()
    return bool(set(codes) - {display})


def convert(
    holdings: pd.DataFrame,
    prices: dict[str, float | None],
    display: str,
    rate: float | None,
) -> tuple[pd.DataFrame, dict[str, float | None]]:
    """Restate a mixed-currency book in `display`. Returns (holdings, prices).

    Entry price and quote are scaled by the *same* per-ticker factor, so
    `multiple` and `gain_pct` come out of `value` unchanged — a currency is a
    unit, not a return. Only levels move.

    No rate (EUR=X unquotable) leaves both inputs untouched: the book then reads
    in its native currencies, which is honest, where a book converted at an
    assumed 1.0 would be quietly wrong. `native_currency` is always set, so the
    caller can say which currency a row really is in.
    """
    df = holdings.copy()
    native = (
        df["currency"] if "currency" in df.columns
        else pd.Series(DEFAULT_CURRENCY, index=df.index, dtype="object")
    ).fillna(DEFAULT_CURRENCY).astype("object").str.strip().str.upper()
    # An unrecognised code is treated as the default rather than dropping the
    # row: a position missing from the table is worse than one priced as USD.
    native = native.where(native.isin(SUPPORTED_CURRENCIES), DEFAULT_CURRENCY)
    df["native_currency"] = native

    if not rate or rate <= 0:
        return df, dict(prices)

    factors = native.map(lambda c: _factor(c, display, rate))
    df["entry_price"] = df["entry_price"] * factors
    df["currency"] = display
    by_ticker = dict(zip(df["ticker"], factors))
    return df, {
        t: (p * by_ticker[t] if p is not None and t in by_ticker else p)
        for t, p in prices.items()
    }


# --- valuation ---------------------------------------------------------------


def recommend(
    gain_pct: float | None, weight: float | None, monitor_action: str | None
) -> str:
    """One position's action. Precedence, hold-biased.

    The monitor's evidenced verdict outranks the arithmetic: a broken thesis is a
    fact about the business, while weight and drawdown are facts about the book.
    Below that, concentration outranks a dip, so a position that is both too big
    and cheap is trimmed rather than topped up.
    """
    if monitor_action in _FROM_MONITOR:
        return _FROM_MONITOR[monitor_action]
    if weight is not None and weight > config.CONCENTRATION_CAP:
        return TRIM
    if gain_pct is not None and gain_pct <= config.ADD_DIP_PCT:
        return ADD
    return HOLD  # incl. a big winner, and a position with no price


def value(
    holdings: pd.DataFrame,
    prices: dict[str, float | None],
    monitor_actions: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Mark positions to market and derive each one's action.

    `prices` maps ticker -> price or None. An unpriced position keeps its cost
    basis and reads `hold`; it is never dropped, because a position missing from
    the table is worse than one missing a number. Weights are taken against the
    *priced* value, so a partially priced book still yields sensible ones.
    """
    df = holdings.copy()
    if df.empty:
        return df.assign(**{c: [] for c in (
            "price", "cost_basis", "market_value", "gain", "gain_pct",
            "multiple", "weight", "action",
        )})

    monitor_actions = monitor_actions or {}
    # float64/NaN rather than the Float64/pd.NA extension dtype: a missing price
    # then reads as NaN everywhere, which sum() skips and to_string() renders.
    df["price"] = df["ticker"].map(lambda t: prices.get(t)).astype("float64")
    df["cost_basis"] = df["shares"] * df["entry_price"]
    df["market_value"] = df["shares"] * df["price"]
    df["gain"] = df["market_value"] - df["cost_basis"]
    df["gain_pct"] = (df["gain"] / df["cost_basis"]).where(df["cost_basis"] != 0)
    df["multiple"] = (df["price"] / df["entry_price"]).where(df["entry_price"] != 0)

    total_value = df["market_value"].sum()  # skips NaN
    df["weight"] = (df["market_value"] / total_value) if total_value else float("nan")
    df["action"] = [
        recommend(_opt(r.gain_pct), _opt(r.weight), monitor_actions.get(r.ticker))
        for r in df.itertuples()
    ]
    return df


def totals(valued: pd.DataFrame) -> dict:
    """Book-level cost, value and gain. `value` is None until something is priced."""
    if valued.empty:
        return {"cost": 0.0, "value": None, "gain": None, "gain_pct": None, "priced": False}
    cost = float(valued["cost_basis"].sum())
    priced = bool(valued["market_value"].notna().any())
    if not priced:
        return {"cost": cost, "value": None, "gain": None, "gain_pct": None, "priced": False}
    market = float(valued["market_value"].sum())
    gain = market - cost
    return {
        "cost": cost,
        "value": market,
        "gain": gain,
        "gain_pct": (gain / cost) if cost else None,
        "priced": True,
    }


def _opt(v) -> float | None:
    """A pandas cell -> a plain float or None, so the rules never see NA."""
    return None if pd.isna(v) else float(v)


def snapshot_csv(valued: pd.DataFrame) -> str:
    """The valued book as CSV. Round-trips: the first columns re-import as-is."""
    cols = ["ticker", "shares", "entry_price", "currency", "entry_date", "thesis",
            "price", "market_value", "gain_pct", "multiple", "weight", "action"]
    out = valued.reindex(columns=cols)
    return out.to_csv(index=False, float_format="%.4f")


# --- prices and FX (the network calls) ---------------------------------------

FX_TICKER = "EUR=X"  # Yahoo quotes it as EUR per 1 USD


def _last_price(ticker: str) -> float | None:
    """Last traded price, or None on any failure — an unknown price is a blank
    cell and a manual entry, never a crash mid-refresh."""
    try:
        price = yf.Ticker(ticker).fast_info["last_price"]
    except Exception:  # no network, unknown ticker, API shape change
        return None
    return float(price) if price else None


def fetch_prices(tickers: list[str]) -> dict[str, float | None]:
    """Ticker -> current price, or None where unavailable.

    `fast_info` rather than `.info` (which `monitor.py` and `signals.py` use):
    this is a whole book at once, and the quote is all that is wanted.
    """
    return {t: _last_price(t) for t in dict.fromkeys(s.upper() for s in tickers)}


def fetch_fx_rate() -> float | None:
    """EUR per 1 USD, or None if it cannot be quoted — same degradation as a
    missing price, and `convert` reads None as "leave the book native"."""
    return _last_price(FX_TICKER)


def fetch_profile(ticker: str) -> dict:
    """Yahoo's company summary for one ticker, or `{}` if it cannot be had.

    The heavier `.info` rather than `fast_info`, because this is one company on
    demand — the opposite trade from `fetch_prices`, which prices a whole book.
    """
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}


def fetch_history(ticker: str) -> pd.DataFrame | None:
    """The full listed history, or None. `period="max"`: the point of a
    100-bagger chart is the whole run, not the last year of it."""
    try:
        history = yf.Ticker(ticker).history(period="max")
    except Exception:
        return None
    return None if history.empty else history


# --- the company summary block -----------------------------------------------
# Yahoo's own labels and its own `--` for a field it has no value for, so the
# panel reads as the quote page it is taken from. Everything here is in the
# ticker's *own* currency: `convert` restates the book, but a market cap or an
# EPS is a fact about the company, and a chart spanning years cannot be honestly
# rescaled by today's spot rate.

NA = "--"


def _amount(value, digits: int = 2) -> str:
    return NA if value is None or pd.isna(value) else f"{value:,.{digits}f}"


def _compact(value, code: str) -> str:
    if value is None or pd.isna(value):
        return NA
    scaled, unit = (value / 1e9, "B") if value >= 1e9 else (value / 1e6, "M")
    return f"{scaled:,.2f}{unit} {code}".strip()


def _epoch_date(ts) -> str:
    """A Yahoo unix timestamp -> YYYY-MM-DD, the date format the CSV already uses."""
    if not ts or pd.isna(ts):
        return NA
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%d")


def profile_rows(info: dict) -> list[tuple[str, str]]:
    """Yahoo's summary block as (label, value) pairs, in Yahoo's order."""
    code = info.get("currency") or ""
    div, yld = info.get("dividendRate"), info.get("dividendYield")
    dividend = NA if div is None else (
        f"{div:,.2f}" + (f" ({yld:.2f}%)" if yld is not None else "")
    )
    return [
        ("Market cap (intraday)", _compact(info.get("marketCap"), code)),
        ("Beta (5Y monthly)", _amount(info.get("beta"))),
        ("PE ratio (TTM)", _amount(info.get("trailingPE"))),
        ("EPS (TTM)", _amount(info.get("trailingEps"), 4)),
        # `...Start` is the next scheduled date; `earningsTimestamp` is the last
        # reported one, and only stands in when no next date is published.
        ("Earnings date (est.)", _epoch_date(
            info.get("earningsTimestampStart") or info.get("earningsTimestamp"))),
        ("Forward dividend & yield", dividend),
        ("Ex-dividend date", _epoch_date(info.get("exDividendDate"))),
        ("1-year target est.", _amount(info.get("targetMeanPrice"))),
    ]


# --- CLI ---------------------------------------------------------------------


def import_csv(con, text: str, replace: bool = False) -> int:
    """Persist a parsed CSV. `replace` empties the table first, so a corrected
    file is a re-import rather than a merge with what it was meant to fix."""
    rows = parse_csv(text)
    if replace:
        db.delete_positions(con)
    for r in rows:
        db.add_position(con, **r)
    return len(rows)


def _run_import(args) -> None:
    with db.connect() as con:
        n = import_csv(con, Path(args.csv).read_text(), replace=args.replace)
    print(f"Imported {n} position(s) from {args.csv}")


def _run_list(args) -> None:
    with db.connect(read_only=True) as con:
        held = db.open_positions(con)
        if held.empty:
            print("No open positions. Import a CSV, or add them on the Portfolio page.")
            return
        prices = fetch_prices(list(held["ticker"])) if args.prices else {}
        # Only a book that is not already in the display currency needs a rate,
        # so a single-currency `list` stays the network-free command it was.
        mixed = needs_fx(held, args.currency)
        rate = fetch_fx_rate() if mixed else None
        held, prices = convert(held, prices, args.currency, rate)
        valued = value(held, prices, db.latest_monitor_action(con))
    cols = ["ticker", "shares", "entry_price", "price", "native_currency",
            "multiple", "gain_pct", "action"]
    print(valued.reindex(columns=cols).to_string(index=False, na_rep="—"))
    t = totals(valued)
    unit = args.currency if rate or not mixed else "native — no EUR=X quote"
    print(f"\ncost {t['cost']:,.2f}" + (
        f"  value {t['value']:,.2f}  gain {t['gain']:+,.2f} ({t['gain_pct']:+.1%})"
        if t["priced"] else "  (no prices; pass --prices)") + f"  [{unit}]")


def _main() -> None:
    ap = argparse.ArgumentParser(prog="src.portfolio")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("import", help="load positions from a CSV")
    i.add_argument("--csv", required=True, help="path to the CSV")
    i.add_argument("--replace", action="store_true", help="empty the table first")
    i.set_defaults(func=_run_import)

    ls = sub.add_parser("list", help="show open positions")
    ls.add_argument("--prices", action="store_true", help="fetch live quotes (network)")
    ls.add_argument("--currency", choices=SUPPORTED_CURRENCIES, default="EUR",
                    help="state the book in this currency (default: EUR)")
    ls.set_defaults(func=_run_list)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    _main()
