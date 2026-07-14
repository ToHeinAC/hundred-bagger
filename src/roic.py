"""Stage 3 — ROIC + avoidance screening (0-10) from SEC EDGAR XBRL.

The highest-signal stage in the funnel, and the reason it uses a primary source:
Stage 2's numbers come from a scraper, these come from the filing. Where they
disagree, these win.

Orchestration only — `src/xbrl.py` talks to EDGAR, `src/fundamentals.py` does the
arithmetic, this scores it and persists it.

Tag coverage is genuinely uneven for small filers, so a company we cannot compute
ROIC for is flagged `XBRL_INCOMPLETE` and left in place — never excluded (PRD §14).
The success criterion is 80% coverage, not 100%.
"""

from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd

from src import config, db, fundamentals, scorer, xbrl

# Every tag chain we pull, from a single companyfacts call per company.
CHAINS = {
    "revenue": xbrl.REVENUE,
    "ebit": xbrl.EBIT,
    "pretax": xbrl.PRETAX_INCOME,
    "tax": xbrl.TAX_EXPENSE,
    "net_income": xbrl.NET_INCOME,
    "gross_profit": xbrl.GROSS_PROFIT,
    "cfo": xbrl.CFO,
    "depreciation": xbrl.DEPRECIATION,
    "assets": xbrl.ASSETS,
    "assets_current": xbrl.ASSETS_CURRENT,
    "liabilities": xbrl.LIABILITIES,
    "liabilities_current": xbrl.LIABILITIES_CURRENT,
    "equity": xbrl.EQUITY,
    "retained_earnings": xbrl.RETAINED_EARNINGS,
    "cash": xbrl.CASH,
    "long_term_debt": xbrl.LONG_TERM_DEBT,
    "short_term_debt": xbrl.SHORT_TERM_DEBT,
    "shares": xbrl.SHARES,
}


# --- metrics ----------------------------------------------------------------


def metrics(ticker: str, market_cap: float | None) -> tuple[dict, list[str]]:
    """One companyfacts call, every Stage 3 metric. Returns (metrics, warnings)."""
    facts = xbrl.company_facts(ticker)
    table = {name: xbrl.annual(facts, tags) for name, tags in CHAINS.items()}

    years = sorted(table.get("assets", {}))
    latest = years[-1] if years else None
    m = {
        "roic_3y_median": fundamentals.roic_median(table),
        "piotroski_f": None if latest is None else fundamentals.piotroski_f(table, latest),
        "altman_z": None if latest is None else fundamentals.altman_z(table, latest, market_cap),
        "asset_cagr": fundamentals.cagr(table.get("assets", {})),
        "ebitda_cagr": fundamentals.cagr(fundamentals.ebitda(table)),
    }
    # ROIC is the point of this stage. Without it the row is a coverage gap, not a
    # verdict — flagged for manual review, never excluded (PRD §2.4, §14).
    warnings = ["XBRL_INCOMPLETE"] if m["roic_3y_median"] is None else []
    return m, warnings


# --- scoring ----------------------------------------------------------------


def roic_score(m: dict) -> int:
    """0-10 = ROIC (0-5) + Piotroski F (0-3) + Altman Z (0-2). See docs/scoring.md."""
    return (
        scorer.band(m["roic_3y_median"], config.ROIC_BANDS)
        + scorer.band(m["piotroski_f"], config.PIOTROSKI_BANDS)
        + scorer.band(m["altman_z"], config.ALTMAN_Z_BANDS)
    )


def exclusions_for(m: dict) -> list[tuple[str, str]]:
    """Stage 3 avoidance rules. Like Stage 2, never fires on a missing metric."""
    out = []
    assets, ebitda = m["asset_cagr"], m["ebitda_cagr"]
    if assets is not None and ebitda is not None and assets - ebitda > config.ASSET_BLOAT_GAP:
        out.append(("ASSET_BLOAT", f"assets {assets:+.1%}/yr vs EBITDA {ebitda:+.1%}/yr"))

    z = m["altman_z"]
    if z is not None and z < config.ALTMAN_Z_DISTRESS:
        out.append(("DISTRESS_ZONE", f"Altman Z {z:.2f}"))
    return out


def score_ticker(
    con, ticker: str, market_cap: float | None, score_date: dt.date | None = None
) -> dict:
    """Fetch → score → persist one ticker. Idempotent for a given score_date.

    `roic_score` is written even when XBRL coverage failed and it is 0. A NULL
    roic_score means "Stage 3 never ran"; a 0 with XBRL_INCOMPLETE means "it ran
    and found nothing". The dashboard relies on telling those apart.
    """
    m, warnings = metrics(ticker, market_cap)
    score = roic_score(m)
    excl = exclusions_for(m)

    db.upsert_score(con, ticker, score_date, roic_score=score, **m)
    if warnings:
        db.merge_warnings(con, ticker, warnings, score_date)
    for reason, detail in excl:
        db.add_exclusion(con, ticker, reason, detail, stage=3)
    if not excl and score >= config.STAGE_3_GATE:
        db.set_stage(con, [ticker], 3)
    return {"ticker": ticker, "score": score, "roic": m["roic_3y_median"],
            "warnings": warnings, "exclusions": [r for r, _ in excl]}


# --- CLI --------------------------------------------------------------------


def _histogram(scores: list[int]) -> None:
    print("\nROIC score histogram (0-10):")
    for s in range(config.ROIC_MAX_SCORE + 1):
        n = scores.count(s)
        if n:
            marker = " <- gate" if s == config.STAGE_3_GATE else ""
            print(f"  {s:>2} | {'#' * min(n, 50):<50} {n:>4}{marker}")


def _run_batch(con, candidates: pd.DataFrame) -> None:
    results, failed = [], []
    for i, row in enumerate(candidates.itertuples(), 1):
        try:
            r = score_ticker(con, row.ticker, row.market_cap)
            results.append(r)
            note = f"  EXCLUDED: {','.join(r['exclusions'])}" if r["exclusions"] else ""
            note += "  XBRL_INCOMPLETE" if r["warnings"] else ""
            roic_pct = "  n/a" if r["roic"] is None else f"{r['roic']:>5.1%}"
            print(f"[{i}/{len(candidates)}] {row.ticker:<6} {r['score']:>2}/10  ROIC {roic_pct}{note}")
        except Exception as e:  # EDGAR breaks per-ticker; never abort the batch
            failed.append(row.ticker)
            print(f"[{i}/{len(candidates)}] {row.ticker:<6} FETCH FAILED: {type(e).__name__}: {e}")

    _histogram([r["score"] for r in results])
    covered = [r for r in results if not r["warnings"]]
    advanced = [r for r in results if not r["exclusions"] and r["score"] >= config.STAGE_3_GATE]
    print(f"\nScored {len(results)}  |  fetch failures {len(failed)}")
    print(f"Excluded {len([r for r in results if r['exclusions']])}"
          f"  |  XBRL incomplete {len(results) - len(covered)}")
    if results:
        print(f"ROIC coverage: {len(covered)}/{len(results)} = {len(covered) / len(results):.0%}"
              f"  (target >= 80%)")
    print(f"Advanced to Stage 3 (>= {config.STAGE_3_GATE}/10): {len(advanced)}")


def _main() -> None:
    ap = argparse.ArgumentParser(prog="src.roic")
    ap.add_argument("--batch", action="store_true", help="score the Stage 2 survivors")
    ap.add_argument("--ticker", help="score a single ticker")
    ap.add_argument("--limit", type=int, help="cap the batch size (for a smoke run)")
    args = ap.parse_args()

    with db.connect() as con:
        candidates = db.get_universe(con, stage=2, status="active")
        if args.ticker:
            row = candidates[candidates["ticker"] == args.ticker]
            cap = float(row["market_cap"].iloc[0]) if not row.empty else None
            print(score_ticker(con, args.ticker, cap))
            return
        if not args.batch:
            ap.error("pass --batch or --ticker")
        _run_batch(con, candidates.head(args.limit) if args.limit else candidates)


if __name__ == "__main__":
    _main()
