# docs/scoring.md — rubrics, exclusions, gates

Source of truth: `src/config.py` (thresholds) and `src/scorer.py` (evaluation). Column definitions, units and NULL semantics are **not** here — see [schema.md](schema.md).

Every threshold below is a code constant, not a magic number in a query: a change to the screen shows up in a diff of `config.py`.

## 1. Total score: 0–34

| Component | Range | Stage | Written by | Status |
|-----------|-------|-------|-----------|--------|
| `quant_score` | 0–14 | 2 | `src/scorer.py` (yfinance) | implemented |
| `roic_score` | 0–10 | 3 | `src/roic.py` (SEC XBRL) | **not implemented (Phase 2)** |
| `moat_score` | 0–10 | 4 | Claude Code via `/hunt-moat` | **not implemented (Phase 2)** |
| `total_score` | **0–34** | — | recomputed on every score write | implemented |

`total_score = coalesce(quant_score,0) + coalesce(roic_score,0) + coalesce(moat_score,0)`. Unscored stages contribute 0, so a Phase-1 `total_score` never exceeds 14 and says nothing about ROIC or moat. See [schema.md §3](schema.md#3-scores).

## 2. Stage 2 quantitative rubric (0–14) — implemented

Seven metrics, max points summing to exactly **14**. Bands are evaluated **best-first: the first band the value clears wins** (`scorer._band`). For *lower-is-better* metrics the comparison is inverted (`value <= threshold`) and the bands are ordered tightest-first, so the same "first match wins" rule holds.

| # | Metric | Direction | Bands (first match wins) | Max |
|---|--------|-----------|--------------------------|-----|
| 1 | `revenue_cagr_3y` | higher | ≥ 20% → **3** · ≥ 15% → **2** · ≥ 10% → **1** · else 0 | 3 |
| 2 | `gross_margin` | higher | ≥ 50% → **2** · ≥ 35% → **1** · else 0 | 2 |
| 3 | `operating_margin` | higher | ≥ 15% → **2** · ≥ 5% → **1** · else 0 | 2 |
| 4 | `fcf_margin` | higher | ≥ 10% → **2** · ≥ 0% → **1** · else 0 (i.e. negative FCF margin scores 0) | 2 |
| 5 | `debt_to_equity` | **lower** | ≤ 0.30 → **2** · ≤ 0.75 → **1** · else 0 | 2 |
| 6 | `share_change_pct` | **lower** | ≤ 0% (flat or buying back) → **2** · ≤ 2% → **1** · else 0 | 2 |
| 7 | `insider_pct` | higher | ≥ 10% → **1** · else 0 | 1 |
| | | | **Total** | **14** |

Constants: `REVENUE_CAGR_BANDS`, `GROSS_MARGIN_BANDS`, `OPERATING_MARGIN_BANDS`, `FCF_MARGIN_BANDS`, `DEBT_TO_EQUITY_BANDS`, `SHARE_CHANGE_BANDS`, `INSIDER_OWNERSHIP_BANDS`, `QUANT_MAX_SCORE = 14`.

**A missing metric scores 0 points.** `_band(None, …)` returns 0 — it does not skip the metric, and there is no renormalisation. Every missing metric is recorded in `scores.data_warnings`, so a warned ticker's `quant_score` is a **floor, not a measurement**. Do not read a low score on a warned ticker as a bad company; read it as an unmeasured one. (Detail in [schema.md §3.1](schema.md#31-data_warnings).)

Two edge cases worth knowing before re-deriving them:

- `debt_to_equity` is stored as a ratio, not Yahoo's percentage (`scorer.metrics` divides by 100). A debt-free company scores the full 2 points.
- `share_change_pct` is a CAGR, so buybacks are negative and clear the ≤ 0% band.

## 3. Auto-exclusions (Stage 2) — implemented

`scorer.exclusions_for()`. All four are **strict** comparisons and all four are evaluated independently — a ticker can collect several codes in one run.

| Code | Trigger | Exact threshold | Constant |
|------|---------|-----------------|----------|
| `CHRONIC_DILUTER` | `share_change_pct` > 5% | `CHRONIC_DILUTER_PCT = 0.05` | strictly greater |
| `CASH_BURNER` | FCF **and** operating cash flow both < 0 | — (sign test, no constant) | both must be negative |
| `EXCESSIVE_LEVERAGE` | `debt_to_equity` > 3.0 | `EXCESSIVE_LEVERAGE_DE = 3.0` | ratio, not percent |
| `REVENUE_DECLINE` | `revenue_cagr_3y` < 0 | `REVENUE_DECLINE_CAGR = 0.0` | strictly less |

**An exclusion NEVER fires on a missing (None) metric.** Every rule is guarded on `is not None` before comparing. Absent data is a warning, never a disqualification — the pipeline flags rather than deletes (PRD §2.4). `CASH_BURNER` additionally requires *both* cash-flow figures to be present.

Each firing writes a row to `exclusions` with a `detail` string carrying the actual number, sets `universe.status = 'excluded'`, and — because the ticker is excluded — **suppresses the Stage 2 stage advance even if `quant_score` cleared the gate** (`scorer.score_ticker`: `if not excl and score >= STAGE_2_GATE`). Exclusions are reversible; see [schema.md §4](schema.md#4-exclusions).

## 4. Stage gates

| Gate | Condition | Constant | Status |
|------|-----------|----------|--------|
| Stage 2 | `quant_score >= 8` of 14 **and no exclusion fired** | `STAGE_2_GATE = 8` | implemented |
| Stage 3 | `roic_score >= 6` of 10 | `STAGE_3_GATE = 6` | gate defined, scorer not implemented |
| Stage 4 | `moat_total >= 6` (of 18) **AND** `moat_durability >= 3` (of 5) | `MOAT_TOTAL_GATE = 6`, `MOAT_DURABILITY_GATE = 3` | gate defined, rubric not implemented |

Clearing a gate calls `db.set_stage()`, which only ever raises the high-water mark. A ticker that later fails a re-score is not demoted — it is excluded. See [schema.md §1](schema.md#1-two-orthogonal-axes-stage-and-status).

## 5. Stage 3 — ROIC + avoidance (0–10) — NOT YET IMPLEMENTED (Phase 2)

`roic_score` is NULL for every ticker today; `src/roic.py` does not exist. Only the gate constant (`STAGE_3_GATE = 6`) and the `scores` columns are in place.

Intended shape, from PRD §5 / §7 / §12 — **the band cutoffs below are deliberately absent because they do not exist in code yet. Do not invent them; add them to `config.py` first.**

- Source: SEC EDGAR XBRL `companyfacts` — a primary source, chosen precisely because it cross-checks the yfinance figures used in Stage 2. No API key; requires a `SEC_USER_AGENT` email header and a ≤ 10 req/s cap enforced in code.
- Inputs to compute, matching the existing columns: `roic_3y_median` (the headline number of the whole funnel), `piotroski_f` (0–9), `altman_z`, plus `asset_cagr` and `ebitda_cagr` as an asset-bloat check.
- Composition of the 0–10 `roic_score` from those inputs: **undefined in code.**
- Avoidance flag named in the PRD: `ASSET_BLOAT`, where asset CAGR outruns EBITDA CAGR. Its exclusion threshold is not yet defined, and it is not yet in the exclusion table in §3.
- Known accepted gap: XBRL tag coverage is uneven for small filers. The PRD's success criterion is ROIC for ≥ 80% of Stage 2 survivors, with the remainder **flagged** (`XBRL_INCOMPLETE`) for manual review, not excluded.

## 6. Stage 4 — moat (0–10) — NOT YET IMPLEMENTED (Phase 2)

`moat_score` is NULL for every ticker today; `src/moat.py` and `.claude/skills/hunt-moat/` do not exist. Only the gate constants and the `scores` columns are in place.

Intended shape, from PRD §6 / §7:

- **The rubric will live in `SKILL.md`, not in Python.** This is a load-bearing constraint, not a preference: it is what keeps the project free of an LLM SDK. Claude Code applies the rubric; Python only fetches text and validates the JSON coming back (fetch → judge → save).
- Six dimensions scored **0–3 each** → `moat_total` 0–18: distribution, brand, network effects, regulatory, switching costs, cost structure.
- Plus `moat_durability` 0–5, `founder_led` (bool), `reinvest_runway` (`narrow` \| `medium` \| `wide`), `moat_notes`, `key_risks`.
- Advancement gate (already in `config.py`): `moat_total >= 6 AND moat_durability >= 3`.
- **The 0–18 `moat_total` is not what feeds `total_score`** — the derived 0–10 `moat_score` is. That derivation (how 18 + 5 collapses to 10) is **not defined anywhere in code**. It must be written down in `config.py` before the first moat score is persisted, or `total_score` will silently be wrong.

## 7. Sell triggers (Phase 3) — NOT YET IMPLEMENTED

The sell-trigger table that `/hunt-monitor` will evaluate against open positions (mechanical triggers in Python; 8-K red-flag reading by Claude) is not defined in code. `monitoring_log.flags` is the column that will hold the fired codes. The only trigger named so far in the PRD is `ROIC_DETERIORATION`. Nothing further is specified — do not assume a table exists.
