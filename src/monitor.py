"""Position monitoring — has the thesis broken?

The check splits exactly where the project splits:

- **Mechanical triggers** are arithmetic over SEC XBRL. The table lives in
  `src/triggers.py`; they are the same primary-source numbers Stage 3 screened on,
  so a position is judged against the standard it was admitted on.
- **Red flags** are read out of recent 8-K text by Claude Code — fetch → judge →
  save, the pattern `/hunt-moat` established. A restatement or a going-concern
  paragraph is not a number, and no regex will find it honestly.

`check` writes the mechanical verdict and drops the 8-K text on disk; `save` merges
Claude's red flags into that same row and re-derives the action. A check that is
never judged still leaves a complete, honest record — it just does not know what
the filings said.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import pandas as pd
import yfinance as yf

from src import config, db, filings, roic, triggers, xbrl


def _table(ticker: str) -> dict:
    """The year-keyed XBRL fact table, exactly as Stage 3 builds it."""
    facts = xbrl.company_facts(ticker)
    return {name: xbrl.annual(facts, tags) for name, tags in roic.CHAINS.items()}


# --- check: mechanical triggers + the 8-K text Claude will read -------------


def _snapshot(con, ticker: str, position) -> None:
    """Mark an open position to market. Silently skipped when yfinance has no price."""
    info = yf.Ticker(ticker).info or {}
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if price is None:
        return
    price, entry, shares = float(price), float(position.entry_price), float(position.shares)
    db.upsert_snapshot(
        con, ticker,
        price=price,
        value=price * shares,
        unrealized_return_pct=(price / entry - 1) if entry else None,
    )


def check_ticker(con, ticker: str, market_cap: float | None, position=None) -> dict:
    """Mechanical triggers → monitoring_log + alerts; recent 8-K text → disk."""
    fired = triggers.fired(_table(ticker), market_cap)
    codes = [code for code, _ in fired]
    action = triggers.recommend(codes, [])
    notes = "; ".join(f"{code}: {detail}" for code, detail in fired)

    db.add_monitoring_log(con, ticker, codes, action, notes)
    for code, detail in fired:
        db.add_alert(con, ticker, "sell", "MEDIUM", f"{code} — {detail}")
    if position is not None:
        _snapshot(con, ticker, position)

    path = filings.write_recent_8k(ticker, config.MONITOR_INPUT_DIR)
    return {"ticker": ticker, "flags": codes, "action": action, "notes": notes, "eightk": path}


# --- save: Claude's 8-K red flags -------------------------------------------


def validate(payload: dict) -> tuple[list[str], str]:
    """Claude's JSON → (red-flag codes, notes). Raises ValueError on anything else.

    The vocabulary is closed. An invented code would land in `monitoring_log.flags`
    and silently never match anything the user greps for, which is worse than a
    loud rejection here.
    """
    raw = payload.get("red_flags", [])
    if not isinstance(raw, list):
        raise ValueError(f"red_flags: expected a list, got {raw!r}")
    unknown = [c for c in raw if c not in config.RED_FLAGS]
    if unknown:
        raise ValueError(f"red_flags: unknown code(s) {unknown}. Valid: {list(config.RED_FLAGS)}")

    notes = payload.get("notes", "")
    if not isinstance(notes, str):
        raise ValueError(f"notes: expected a string, got {notes!r}")
    return list(dict.fromkeys(raw)), notes.strip()


def _todays_check(con, ticker: str) -> pd.Series:
    log = db.monitoring_log(con, ticker)
    # DuckDB hands DATE back through pandas, which may type it as datetime64 or as
    # object. Normalise before comparing rather than trusting either.
    today = log[pd.to_datetime(log["check_date"]).dt.date == dt.date.today()] if not log.empty \
        else log
    if today.empty:
        raise ValueError(
            f"No monitoring check for {ticker} today. Run `check` before saving red flags — "
            "the mechanical triggers are half the verdict."
        )
    return today.iloc[0]


def save_ticker(con, ticker: str, payload: dict) -> dict:
    """Merge Claude's red flags into today's log row and re-derive the action.

    The mechanical flags are re-read from the row rather than recomputed, so a
    second save is idempotent instead of doubling up on the codes it already wrote.
    """
    red_flags, notes = validate(payload)
    row = _todays_check(con, ticker)

    mechanical = [c for c in json.loads(row["flags"] or "[]") if c not in config.RED_FLAGS]
    action = triggers.recommend(mechanical, red_flags)
    combined = "; ".join(p for p in [row["notes"] or "", notes] if p)

    db.add_monitoring_log(con, ticker, mechanical + red_flags, action, combined)
    for code in red_flags:
        db.add_alert(con, ticker, "red_flag", "HIGH", f"{code} — {notes or 'see 8-K'}")
    return {"ticker": ticker, "flags": mechanical + red_flags,
            "red_flags": red_flags, "action": action}


# --- CLI --------------------------------------------------------------------


def _targets(con, ticker: str | None) -> list[tuple]:
    """Open positions by default; any ticker on request.

    The portfolio table is Phase 4's, so until then `--ticker` is how this runs —
    and it stays useful afterwards, for monitoring a watchlist name not yet bought.
    """
    universe = db.get_universe(con)
    caps = dict(zip(universe["ticker"], universe["market_cap"]))
    if ticker:
        return [(ticker, caps.get(ticker), None)]
    return [(p.ticker, caps.get(p.ticker), p) for p in db.open_positions(con).itertuples()]


def _run_check(args) -> None:
    filings.identity()
    config.MONITOR_INPUT_DIR.mkdir(parents=True, exist_ok=True)

    with db.connect() as con:
        targets = _targets(con, args.ticker)
        if not targets:
            print("No open positions. Pass --ticker to monitor a name you do not hold yet;\n"
                  "the portfolio table is populated in Phase 4.")
            return

        for i, (ticker, cap, position) in enumerate(targets, 1):
            try:
                r = check_ticker(con, ticker, cap, position)
                print(f"[{i}/{len(targets)}] {ticker:<6} {r['action']:<6} "
                      f"{','.join(r['flags']) or 'clean'}")
                if r["notes"]:
                    print(f"           {r['notes']}")
            except Exception as e:  # one dead ticker must not abort the pass
                print(f"[{i}/{len(targets)}] {ticker:<6} CHECK FAILED: {type(e).__name__}: {e}")

    print(f"\n8-K text is in {config.MONITOR_INPUT_DIR}/ — read each file for red flags, "
          "then save the judgement per ticker.")


def _run_save(args) -> None:
    raw = Path(args.json_file).read_text() if args.json_file else args.json
    with db.connect() as con:
        r = save_ticker(con, args.ticker, json.loads(raw))
    print(f"{r['ticker']}  red flags: {','.join(r['red_flags']) or 'none'}  ->  {r['action']}")


def _main() -> None:
    ap = argparse.ArgumentParser(prog="src.monitor")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="mechanical sell triggers + 8-K text -> disk")
    c.add_argument("--ticker", help="a single ticker (default: every open position)")
    c.set_defaults(func=_run_check)

    s = sub.add_parser("save", help="persist Claude's 8-K red flags")
    s.add_argument("--ticker", required=True)
    source = s.add_mutually_exclusive_group(required=True)
    source.add_argument("--json", help="the red-flag JSON as a string")
    source.add_argument("--json-file", help="path to a file holding the JSON")
    s.set_defaults(func=_run_save)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    _main()
