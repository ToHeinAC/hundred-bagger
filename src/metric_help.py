"""Human-readable explanations for every metric shown in the dashboard.

UI content only — no logic. Each string is markdown (Streamlit renders it in
tooltips and the glossary expander) and follows the same shape:
**definition · ideal direction / bands · why it matters for the screen**.

Numbers are kept in sync with the thresholds in ``src/config.py`` and the prose
in ``docs/scoring.md`` — change them here when the bands there change.
"""

from __future__ import annotations

# Keyed by the DB column name used in QUANT/ROIC/MOAT_FIELDS and the Watchlist
# column_config, so both pages look up the same text.
METRIC_HELP: dict[str, str] = {
    # --- Stage 2: quant (0–14) ----------------------------------------------
    "revenue_cagr_3y": (
        "**3-yr revenue CAGR** — compound annual revenue growth over the last "
        "three years. Higher is better: ≥20% earns full points (3), ≥10% is the "
        "floor for any points; negative growth is auto-excluded (`REVENUE_DECLINE`). "
        "Sustained top-line growth is the engine of a 100-bagger."
    ),
    "gross_margin": (
        "**Gross margin** — gross profit ÷ revenue. Higher is better: ≥50% → 2 pts, "
        "≥35% → 1 pt. A high, stable gross margin is the first sign of pricing power "
        "and a product the market can't easily substitute."
    ),
    "operating_margin": (
        "**Operating margin** — operating income ÷ revenue. Higher is better: "
        "≥15% → 2 pts, ≥5% → 1 pt. Shows the business turns sales into profit after "
        "running costs — proof the growth actually earns money, not just revenue."
    ),
    "fcf_margin": (
        "**Free-cash-flow margin** — free cash flow ÷ revenue. Higher is better: "
        "≥10% → 2 pts, ≥0% → 1 pt; negative FCF scores 0. Cash — not accounting "
        "earnings — funds reinvestment and compounding without diluting shareholders."
    ),
    "debt_to_equity": (
        "**Debt / equity** — total debt ÷ shareholder equity (a ratio, so 0.30 = 30%). "
        "**Lower is better**: ≤0.30 → 2 pts, ≤0.75 → 1 pt; above 3.0 is auto-excluded "
        "(`EXCESSIVE_LEVERAGE`). Low leverage lets a company survive downturns and "
        "compound for a decade instead of being run for its creditors."
    ),
    "share_change_pct": (
        "**Share-count change (CAGR)** — annual growth in shares outstanding. "
        "**Lower is better**: ≤0% (flat or buying back) → 2 pts, ≤2% → 1 pt; >5%/yr "
        "is auto-excluded (`CHRONIC_DILUTER`). Dilution silently transfers your "
        "future gains to new shareholders — the enemy of a long hold."
    ),
    "insider_pct": (
        "**Insider ownership** — % of shares held by insiders. Higher is better: "
        "≥10% → 1 pt. Meaningful insider skin-in-the-game aligns management with "
        "long-term owners rather than short-term option grants."
    ),
    # --- Stage 3: ROIC + avoidance (0–10) -----------------------------------
    "roic_3y_median": (
        "**ROIC (3-yr median)** — return on invested capital, "
        "`EBIT × (1 − tax) ÷ (equity + debt − cash)`, median of the last 3 fiscal "
        "years. Higher is better: ≥20% → 5 pts, scaling down to ≥7% → 1 pt. The "
        "headline number of the whole funnel — a company that earns far above its "
        "cost of capital and can reinvest at that rate is what compounds 100×."
    ),
    "piotroski_f": (
        "**Piotroski F-score** — 0–9 checklist of profitability, leverage and "
        "efficiency signals (ROA/CFO positive and rising, deleveraging, no dilution, "
        "margins and turnover improving). Higher is better: ≥7 → 3 pts, ≥5 → 2, "
        "≥4 → 1. The accounting-quality confirm — high ROIC backed by clean, "
        "improving fundamentals rather than one lucky year."
    ),
    "altman_z": (
        "**Altman Z-score** — bankruptcy-distance score "
        "(`1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + 1.0·Sales/TA`). "
        "Higher is better: ≥3.0 → 2 pts (safe), ≥1.8 → 1 pt; below 1.8 is the "
        "distress zone and an auto-exclusion (`DISTRESS_ZONE`). A solvency floor — "
        "a company that may not exist in three years can't compound for ten."
    ),
    "asset_cagr": (
        "**Asset CAGR** — annual growth in total assets. Not scored on its own; "
        "compared against EBITDA CAGR. When assets outrun earnings by >10pp the "
        "ticker is auto-excluded (`ASSET_BLOAT`) — growth bought with the balance "
        "sheet rather than earned."
    ),
    "ebitda_cagr": (
        "**EBITDA CAGR** — annual growth in EBITDA (EBIT + D&A). Not scored on its "
        "own; it's the yardstick for the asset-bloat check — earnings should grow at "
        "least as fast as the asset base funding them."
    ),
    # --- Stage 4: moat (0–10) -----------------------------------------------
    "moat_distribution": (
        "**Distribution moat (0–3)** — advantage from reach, scale or channel lock-in "
        "that rivals can't cheaply replicate. Higher is better. Judged by Claude from "
        "the 10-K/20-F business section."
    ),
    "moat_brand": (
        "**Brand moat (0–3)** — pricing power and loyalty from a brand customers seek "
        "out and pay up for. Higher is better."
    ),
    "moat_network": (
        "**Network-effect moat (0–3)** — value that rises with each additional user, "
        "making the leader progressively harder to displace. Higher is better."
    ),
    "moat_regulatory": (
        "**Regulatory moat (0–3)** — licences, approvals or patents that legally raise "
        "the barrier to entry. Higher is better."
    ),
    "moat_switching": (
        "**Switching-cost moat (0–3)** — friction (data, integration, retraining) that "
        "keeps customers even when a cheaper option appears. Higher is better."
    ),
    "moat_cost": (
        "**Cost-advantage moat (0–3)** — a structural ability to produce cheaper than "
        "rivals (scale, process, location). Higher is better."
    ),
    "moat_durability": (
        "**Moat durability (0–5)** — how long the advantage is expected to hold up. "
        "Higher is better, and it's weighted heavily: a wide but eroding moat is worth "
        "less over a ten-year hold than a narrow durable one."
    ),
    "founder_led": (
        "**Founder-led** — whether a founder still runs the company. Founders tend to "
        "manage for decades and own meaningful stakes — a qualitative plus for a long "
        "hold, not a scored input."
    ),
    "reinvest_runway": (
        "**Reinvestment runway** — how much room the business has to redeploy profits "
        "at high returns (narrow / medium / wide). Wide is best: a long runway is what "
        "lets high ROIC keep compounding instead of stalling."
    ),
    # --- Scores, stage, status ----------------------------------------------
    "total_score": (
        "**Total score (0–34)** — quant (0–14) + ROIC (0–10) + moat (0–10). Higher is "
        "better. A stage that hasn't run contributes 0, so always read it next to "
        "**Stage** — a 15 on a Stage-2-only ticker says nothing about its ROIC or moat."
    ),
    "quant_score": (
        "**Quant score (0–14)** — Stage 2 fundamentals subscore. Higher is better; "
        "≥8 clears the gate into Stage 3."
    ),
    "roic_score": (
        "**ROIC score (0–10)** — Stage 3 capital-efficiency subscore (ROIC + Piotroski "
        "+ Altman). Higher is better; ≥6 clears the gate into Stage 4. No ticker "
        "passes on ROIC alone."
    ),
    "moat_score": (
        "**Moat score (0–10)** — Stage 4 competitive-advantage subscore, "
        "`round(6·total/18 + 4·durability/5)`. Higher is better; durability carries "
        "40% of the weight."
    ),
    "stage": (
        "**Stage** — furthest funnel stage the ticker has reached (1 Universe → "
        "2 Quant → 3 ROIC → 4 Moat). A high-water mark: it only ever rises, so a "
        "ticker can be Stage 4 *and* excluded."
    ),
    "status": (
        "**Status** — `active`, `excluded`, or `watchlist`. Orthogonal to Stage: it "
        "tracks whether the ticker is still in play, was disqualified, or cleared the "
        "Stage 4 gate onto the watchlist. Exclusions are reversible."
    ),
    "sector": (
        "**Sector** — Yahoo sector. Only compounding-friendly sectors are in the "
        "universe; balance-sheet-driven and commodity sectors (Financials, Utilities, "
        "Real Estate, Energy, Materials) are excluded up front."
    ),
    "market_cap": (
        "**Market cap** — total equity value. The universe targets $75M–$2B: big "
        "enough to be investable, small enough to still have 100× headroom."
    ),
    "data_warnings": (
        "**Data warnings** — metrics yfinance couldn't supply. Each missing metric "
        "scored 0, so a warned ticker's score is a **floor, not a verdict** — treat a "
        "low score here as unmeasured, not bad."
    ),
}
