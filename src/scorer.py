"""Stage 2 — quantitative scoring (0-14) from yfinance.

yfinance is unreliable on microcaps. Every missing field becomes a warning code
on the score row; missing data never excludes a ticker on its own (PRD §2.4).
Auto-exclusions fire only on a metric we actually have.
"""

from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd
import yfinance as yf

from src import config, db

# --- metric extraction ------------------------------------------------------


def _row(frame: pd.DataFrame | None, *names: str) -> pd.Series | None:
    """First matching row of a yfinance statement frame (columns = periods)."""
    if frame is None or frame.empty:
        return None
    for n in names:
        if n in frame.index:
            series = frame.loc[n].dropna()
            if not series.empty:
                return series
    return None


def _cagr(series: pd.Series) -> float | None:
    """CAGR across a yfinance series (newest period first). Needs >= 2 periods."""
    if series is None or len(series) < 2:
        return None
    newest, oldest = float(series.iloc[0]), float(series.iloc[-1])
    years = len(series) - 1
    if oldest <= 0 or newest <= 0:
        return None
    return (newest / oldest) ** (1 / years) - 1


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def _latest(series: pd.Series | None) -> float | None:
    return None if series is None or series.empty else float(series.iloc[0])


def metrics(ticker: str) -> tuple[dict, list[str]]:
    """Pull raw metrics for one ticker. Returns (metrics, warning codes)."""
    tk = yf.Ticker(ticker)
    info = tk.info or {}
    income, cash, balance = tk.income_stmt, tk.cashflow, tk.balance_sheet

    revenue = _row(income, "Total Revenue", "Operating Revenue")
    rev_latest = _latest(revenue)
    gross = _latest(_row(income, "Gross Profit"))
    operating = _latest(_row(income, "Operating Income", "EBIT"))
    ocf = _latest(_row(cash, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities"))
    capex = _latest(_row(cash, "Capital Expenditure"))
    shares = _row(balance, "Ordinary Shares Number", "Share Issued")

    fcf = None if ocf is None else ocf + (capex or 0.0)  # capex is reported negative
    d_to_e = info.get("debtToEquity")

    m = {
        "revenue_cagr_3y": _cagr(revenue),
        "gross_margin": _ratio(gross, rev_latest),
        "operating_margin": _ratio(operating, rev_latest),
        "fcf_margin": _ratio(fcf, rev_latest),
        "debt_to_equity": None if d_to_e is None else float(d_to_e) / 100.0,
        "share_change_pct": _cagr(shares),
        "insider_pct": info.get("heldPercentInsiders"),
    }
    warnings = [k.upper() for k, v in m.items() if v is None]
    return {**m, "_ocf": ocf, "_fcf": fcf}, warnings


# --- scoring ----------------------------------------------------------------


def band(value: float | None, bands: tuple, lower_is_better: bool = False) -> int:
    """Award the points of the first band the value clears. None scores 0."""
    if value is None:
        return 0
    for threshold, points in bands:
        if (value <= threshold) if lower_is_better else (value >= threshold):
            return points
    return 0


def quant_score(m: dict) -> int:
    """0-14. See docs/scoring.md."""
    return (
        band(m["revenue_cagr_3y"], config.REVENUE_CAGR_BANDS)
        + band(m["gross_margin"], config.GROSS_MARGIN_BANDS)
        + band(m["operating_margin"], config.OPERATING_MARGIN_BANDS)
        + band(m["fcf_margin"], config.FCF_MARGIN_BANDS)
        + band(m["debt_to_equity"], config.DEBT_TO_EQUITY_BANDS, lower_is_better=True)
        + band(m["share_change_pct"], config.SHARE_CHANGE_BANDS, lower_is_better=True)
        + band(m["insider_pct"], config.INSIDER_OWNERSHIP_BANDS)
    )


def exclusions_for(m: dict) -> list[tuple[str, str]]:
    """Auto-exclusion rules. Only fires on metrics that are actually present."""
    out = []
    dilution = m["share_change_pct"]
    if dilution is not None and dilution > config.CHRONIC_DILUTER_PCT:
        out.append(("CHRONIC_DILUTER", f"{dilution:.1%} annual share growth"))

    if m["_fcf"] is not None and m["_ocf"] is not None and m["_fcf"] < 0 and m["_ocf"] < 0:
        out.append(("CASH_BURNER", f"FCF {m['_fcf']:,.0f} and OCF {m['_ocf']:,.0f} both negative"))

    d_to_e = m["debt_to_equity"]
    if d_to_e is not None and d_to_e > config.EXCESSIVE_LEVERAGE_DE:
        out.append(("EXCESSIVE_LEVERAGE", f"debt/equity {d_to_e:.2f}"))

    cagr = m["revenue_cagr_3y"]
    if cagr is not None and cagr < config.REVENUE_DECLINE_CAGR:
        out.append(("REVENUE_DECLINE", f"{cagr:.1%} 3y revenue CAGR"))
    return out


def score_ticker(con, ticker: str, score_date: dt.date | None = None) -> dict:
    """Fetch → score → persist one ticker. Idempotent for a given score_date."""
    m, warnings = metrics(ticker)
    score = quant_score(m)
    excl = exclusions_for(m)

    persisted = {k: v for k, v in m.items() if not k.startswith("_")}
    db.upsert_score(con, ticker, score_date, quant_score=score,
                    data_warnings=",".join(warnings) or None, **persisted)
    for reason, detail in excl:
        db.add_exclusion(con, ticker, reason, detail, stage=2)
    if not excl and score >= config.STAGE_2_GATE:
        db.set_stage(con, [ticker], 2)
    return {"ticker": ticker, "score": score, "warnings": warnings,
            "exclusions": [r for r, _ in excl]}


# --- CLI --------------------------------------------------------------------


def _histogram(scores: list[int]) -> None:
    print("\nScore histogram (0-14):")
    for s in range(config.QUANT_MAX_SCORE + 1):
        n = scores.count(s)
        if n:
            marker = " <- gate" if s == config.STAGE_2_GATE else ""
            print(f"  {s:>2} | {'#' * min(n, 50):<50} {n:>4}{marker}")


def _main() -> None:
    ap = argparse.ArgumentParser(prog="src.scorer")
    ap.add_argument("--batch", action="store_true", help="score the Stage 1 universe")
    ap.add_argument("--ticker", help="score a single ticker")
    ap.add_argument("--limit", type=int, help="cap the batch size (for a smoke run)")
    args = ap.parse_args()

    with db.connect() as con:
        if args.ticker:
            print(score_ticker(con, args.ticker))
            return
        if not args.batch:
            ap.error("pass --batch or --ticker")

        tickers = db.get_universe(con, status="active")["ticker"].tolist()
        if args.limit:
            tickers = tickers[: args.limit]

        results, failed = [], []
        for i, t in enumerate(tickers, 1):
            try:
                r = score_ticker(con, t)
                results.append(r)
                flag = f"  EXCLUDED: {','.join(r['exclusions'])}" if r["exclusions"] else ""
                print(f"[{i}/{len(tickers)}] {t:<6} {r['score']:>2}/14{flag}")
            except Exception as e:  # yfinance breaks in many ways; never abort the batch
                failed.append(t)
                print(f"[{i}/{len(tickers)}] {t:<6} FETCH FAILED: {type(e).__name__}")

        _histogram([r["score"] for r in results])
        advanced = [r for r in results if not r["exclusions"] and r["score"] >= config.STAGE_2_GATE]
        excluded = [r for r in results if r["exclusions"]]
        degraded = [r for r in results if r["warnings"]]
        print(f"\nScored {len(results)}  |  fetch failures {len(failed)}")
        print(f"Excluded {len(excluded)}  |  incomplete data {len(degraded)}")
        print(f"Advanced to Stage 2 (>= {config.STAGE_2_GATE}/14): {len(advanced)}")


if __name__ == "__main__":
    _main()
