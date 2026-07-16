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

CSV_TEMPLATE = (
    "ticker,shares,entry_price,entry_date,thesis\n"
    "MELI,10,150.00,2024-01-15,Latam commerce + fintech flywheel\n"
    "ITRN,25,32.50,2024-03-02,Aftermarket telematics, founder-led\n"
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
            "entry_date": _date(cell(row, "entry_date"), i, ticker),
            "thesis": cell(row, "thesis") or None,
        })
    if not rows:
        raise ValueError("CSV contained no positions")
    return rows


def _date(value: str, row: int, ticker: str) -> dt.date | None:
    if not value:
        return None  # db.add_position defaults to today
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Row {row} ({ticker}): entry_date must be YYYY-MM-DD, got {value!r}")


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
    cols = ["ticker", "shares", "entry_price", "entry_date", "thesis",
            "price", "market_value", "gain_pct", "multiple", "weight", "action"]
    out = valued.reindex(columns=cols)
    return out.to_csv(index=False, float_format="%.4f")


# --- prices (the one network call) -------------------------------------------


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
        valued = value(held, fetch_prices(list(held["ticker"])) if args.prices else {},
                       db.latest_monitor_action(con))
    cols = ["ticker", "shares", "entry_price", "price", "multiple", "gain_pct", "action"]
    print(valued.reindex(columns=cols).to_string(index=False, na_rep="—"))
    t = totals(valued)
    print(f"\ncost {t['cost']:,.2f}" + (
        f"  value {t['value']:,.2f}  gain {t['gain']:+,.2f} ({t['gain_pct']:+.1%})"
        if t["priced"] else "  (no prices; pass --prices)"))


def _main() -> None:
    ap = argparse.ArgumentParser(prog="src.portfolio")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("import", help="load positions from a CSV")
    i.add_argument("--csv", required=True, help="path to the CSV")
    i.add_argument("--replace", action="store_true", help="empty the table first")
    i.set_defaults(func=_run_import)

    ls = sub.add_parser("list", help="show open positions")
    ls.add_argument("--prices", action="store_true", help="fetch live quotes (network)")
    ls.set_defaults(func=_run_list)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    _main()
