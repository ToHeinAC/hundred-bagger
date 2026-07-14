"""Entry signals for Watchlist B — when does a qualified candidate become buyable?

Stages 1-4 answer *what* to buy. This answers *when*, and it is deliberately a
different question: a great company at a silly price is not a buy, and neither is
a cheap one nobody inside is willing to touch with their own money.

Three independent tests, combined into one strength:

1. **Insider cluster buy** — several insiders buying on the open market at the
   same time. Only transaction code `P` counts. A grant (`A`) or an option
   exercise (`M`) is compensation, not conviction, and treating it as a signal is
   the classic way to fool yourself with Form 4 data.
2. **Valuation gates** — three yes/no ratio tests, not a score.
3. **Price zone** — where the price sits in its own 52-week range.

A ratio we could not compute is an *ungated* test, never a passed one (PRD §2.4).
"""

from __future__ import annotations

import argparse
import datetime as dt

import yfinance as yf

from src import config, db, filings

# The only Form 4 code that means "an insider bought on the open market".
OPEN_MARKET_BUY = "P"


# --- insider cluster --------------------------------------------------------


def in_window(buy: dict, start: dt.date) -> bool:
    return start <= buy["transaction_date"] < start + dt.timedelta(
        days=config.CLUSTER_WINDOW_DAYS
    )


def cluster(buys: list[dict]) -> dict | None:
    """The strongest CLUSTER_WINDOW_DAYS window that clears both bars, or None.

    Every buy is tried as a window start, so a cluster is found wherever it sits
    in the lookback rather than only in the most recent N days. "Distinct
    insiders" counts *people*, not filings — one director filing four times in a
    week is one insider, and counting filings would manufacture a cluster out of
    a single person's conviction.

    The window is defined by its dates, so membership is a date test the caller
    can re-apply (`in_window`) rather than an identity the caller has to carry.
    """
    candidates = []
    for buy in buys:
        start = buy["transaction_date"]
        rows = [b for b in buys if in_window(b, start)]
        insiders = {r["insider_name"] for r in rows}
        value = sum(r["value"] or 0.0 for r in rows)
        if len(insiders) >= config.CLUSTER_MIN_INSIDERS and value >= config.CLUSTER_MIN_VALUE:
            dates = [r["transaction_date"] for r in rows]
            candidates.append({
                "start": start,
                "insiders": len(insiders),
                "value": value,
                "days": (max(dates) - min(dates)).days,
            })
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c["insiders"], c["value"]))


# --- valuation gates --------------------------------------------------------


def _ratio(num: float | None, den: float | None) -> float | None:
    """None on a non-positive denominator: a negative P/FCF is not a cheap stock."""
    if num is None or den is None or den <= 0:
        return None
    return num / den


def valuation(info: dict) -> dict:
    """The three ratios, from yfinance. Any of them may be None."""
    peg = info.get("trailingPegRatio")
    return {
        "p_fcf": _ratio(info.get("marketCap"), info.get("freeCashflow")),
        "ev_ebitda": _ratio(info.get("enterpriseValue"), info.get("ebitda")),
        "peg": float(peg) if peg else None,
    }


def gates(v: dict) -> dict[str, bool | None]:
    """Pass / fail / unknown per ratio. None stays None — it never becomes a pass."""
    limits = {"p_fcf": config.MAX_P_FCF, "ev_ebitda": config.MAX_EV_EBITDA, "peg": config.MAX_PEG}
    return {k: None if v[k] is None else v[k] <= limits[k] for k in limits}


def price_zone(info: dict) -> float | None:
    """0.0 at the 52-week low, 1.0 at the high."""
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    low, high = info.get("fiftyTwoWeekLow"), info.get("fiftyTwoWeekHigh")
    if price is None or low is None or high is None or high <= low:
        return None
    return (price - low) / (high - low)


# --- strength ---------------------------------------------------------------


def strength(has_cluster: bool, gate_results: dict, zone: float | None) -> str | None:
    """HIGH | MEDIUM | LOW | None.

    Valuation is "ok" when nothing we could measure failed and at least one test
    was measurable — an all-unknown valuation is not a pass, and one failed gate
    sinks it. The cluster buy is what separates HIGH from the rest: price alone
    never earns a HIGH, because cheapness is not a catalyst.
    """
    results = [r for r in gate_results.values() if r is not None]
    valuation_ok = bool(results) and all(results)
    in_buy_zone = zone is not None and zone <= config.BUY_ZONE_MAX

    if has_cluster and valuation_ok:
        return "HIGH"
    if has_cluster or (valuation_ok and in_buy_zone):
        return "MEDIUM"
    if valuation_ok:
        return "LOW"
    return None


def message(c: dict | None, v: dict, zone: float | None) -> str:
    parts = []
    if c:
        parts.append(
            f"cluster buy ({c['insiders']} insiders, ${c['value']:,.0f}, {c['days']} days)"
        )
    if v["p_fcf"] is not None:
        parts.append(f"P/FCF {v['p_fcf']:.1f}")
    if v["ev_ebitda"] is not None:
        parts.append(f"EV/EBITDA {v['ev_ebitda']:.1f}")
    if zone is not None:
        parts.append(f"{zone:.0%} of 52w range")
    return " + ".join(parts) or "no measurable signal"


# --- one ticker -------------------------------------------------------------


def check_ticker(con, ticker: str) -> dict:
    """Form 4 + valuation → insider_events, and a buy alert when it is worth one."""
    buys = [
        e for e in filings.insider_transactions(ticker, config.INSIDER_LOOKBACK_DAYS)
        if e["transaction_type"] == OPEN_MARKET_BUY
    ]
    c = cluster(buys)

    info = yf.Ticker(ticker).info or {}
    v = valuation(info)
    gate_results = gates(v)
    zone = price_zone(info)
    level = strength(bool(c), gate_results, zone)

    db.replace_insider_events(con, ticker, [
        {
            **b,
            "is_cluster_buy": bool(c) and in_window(b, c["start"]),
            "signal_strength": level,
        }
        for b in buys
    ])

    text = message(c, v, zone)
    # LOW is "nothing broke", not news. Alerting on it would train the user to
    # ignore the alert feed, which is the only way this feature fails.
    alerted = level in ("HIGH", "MEDIUM") and db.add_alert(con, ticker, "buy", level, text)
    return {
        "ticker": ticker, "strength": level, "message": text, "alerted": alerted,
        "buys": len(buys), "cluster": c is not None, "gates": gate_results, "zone": zone,
    }


# --- CLI --------------------------------------------------------------------


def _run(args) -> None:
    filings.identity()  # Form 4 comes from EDGAR; fail loudly, not per-ticker
    with db.connect() as con:
        if args.ticker:
            tickers = [args.ticker]
        else:
            tickers = db.get_universe(con, status="watchlist")["ticker"].tolist()
        if not tickers:
            print("No tickers on the watchlist. Run /hunt-moat first — Stage 4 survivors "
                  "are Watchlist B, and entry signals only make sense for names that "
                  "already passed the screen.")
            return

        results, failed = [], []
        for i, t in enumerate(tickers, 1):
            try:
                r = check_ticker(con, t)
                results.append(r)
                icon = {"HIGH": "[HIGH]", "MEDIUM": "[MED ]", "LOW": "[LOW ]"}.get(
                    r["strength"], "[   -]"
                )
                print(f"[{i}/{len(tickers)}] {icon} {t:<6} {r['message']}")
            except Exception as e:  # a dead ticker must not abort the pass
                failed.append(t)
                print(f"[{i}/{len(tickers)}] {t:<6} FETCH FAILED: {type(e).__name__}: {e}")

    by = lambda s: len([r for r in results if r["strength"] == s])  # noqa: E731
    print(f"\nChecked {len(results)}  |  fetch failures {len(failed)}")
    print(f"HIGH {by('HIGH')}  |  MEDIUM {by('MEDIUM')}  |  LOW {by('LOW')}  |  "
          f"no signal {by(None)}")
    print(f"Alerts raised: {len([r for r in results if r['alerted']])}")


def _main() -> None:
    ap = argparse.ArgumentParser(prog="src.signals")
    ap.add_argument("--check", action="store_true", help="check the whole watchlist")
    ap.add_argument("--ticker", help="check a single ticker")
    args = ap.parse_args()
    if not args.check and not args.ticker:
        ap.error("pass --check or --ticker")
    _run(args)


if __name__ == "__main__":
    _main()
