# docs/scoring.md — rubrics, exclusions, gates

Source of truth: `src/config.py` (thresholds) and `src/scorer.py` (evaluation). Column definitions, units and NULL semantics are **not** here — see [schema.md](schema.md).

Every threshold below is a code constant, not a magic number in a query: a change to the screen shows up in a diff of `config.py`.

## 1. Total score: 0–34

| Component | Range | Stage | Written by | Status |
|-----------|-------|-------|-----------|--------|
| `quant_score` | 0–14 | 2 | `src/scorer.py` (yfinance) | implemented |
| `roic_score` | 0–10 | 3 | `src/roic.py` (SEC XBRL) | implemented |
| `moat_score` | 0–10 | 4 | Claude Code via `/hunt-moat` | implemented |
| `total_score` | **0–34** | — | recomputed on every score write | implemented |

`total_score = coalesce(quant_score,0) + coalesce(roic_score,0) + coalesce(moat_score,0)`. All three subscores now exist, so the full 0–34 range is reachable — but **a stage that has not run still contributes 0**, so a `total_score` of 11 on a ticker that has only been through Stage 2 says nothing about its ROIC or moat. Read `total_score` next to `stage`, never alone. See [schema.md §3](schema.md#3-scores).

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

## 3. Auto-exclusions — implemented

`scorer.exclusions_for()` (Stage 2) and `roic.exclusions_for()` (Stage 3). All are **strict** comparisons and all are evaluated independently — a ticker can collect several codes in one run.

### Stage 2

| Code | Trigger | Exact threshold | Constant |
|------|---------|-----------------|----------|
| `CHRONIC_DILUTER` | `share_change_pct` > 5% | `CHRONIC_DILUTER_PCT = 0.05` | strictly greater |
| `CASH_BURNER` | FCF **and** operating cash flow both < 0 | — (sign test, no constant) | both must be negative |
| `EXCESSIVE_LEVERAGE` | `debt_to_equity` > 3.0 | `EXCESSIVE_LEVERAGE_DE = 3.0` | ratio, not percent |
| `REVENUE_DECLINE` | `revenue_cagr_3y` < 0 | `REVENUE_DECLINE_CAGR = 0.0` | strictly less |

### Stage 3

| Code | Trigger | Exact threshold | Constant |
|------|---------|-----------------|----------|
| `ASSET_BLOAT` | `asset_cagr` − `ebitda_cagr` > 10pp | `ASSET_BLOAT_GAP = 0.10` | strictly greater |
| `DISTRESS_ZONE` | `altman_z` < 1.8 | `ALTMAN_Z_DISTRESS = 1.8` | strictly less |

`ASSET_BLOAT` is growth bought with the balance sheet: the asset base compounding materially faster than the earnings it is supposed to produce. `DISTRESS_ZONE` is the classic Altman bankruptcy band, and it is deliberately *also* a hard exclusion on top of the 0–2 scoring band in §5 — a company that might not exist in three years has no business in a ten-year compounding screen.

### The invariant, in both stages

**An exclusion NEVER fires on a missing (None) metric.** Every rule is guarded on `is not None` before comparing. Absent data is a warning, never a disqualification — the pipeline flags rather than deletes (PRD §2.4). `CASH_BURNER` additionally requires *both* cash-flow figures to be present; `ASSET_BLOAT` requires both CAGRs.

Each firing writes a row to `exclusions` with a `detail` string carrying the actual number, sets `universe.status = 'excluded'`, and — because the ticker is excluded — **suppresses the stage advance even if the subscore cleared the gate** (`scorer.score_ticker` and `roic.score_ticker` both guard `if not excl and score >= GATE`). Exclusions are reversible; see [schema.md §4](schema.md#4-exclusions).

## 4. Stage gates

| Gate | Condition | Constant | Status |
|------|-----------|----------|--------|
| Stage 2 | `quant_score >= 8` of 14 **and no exclusion fired** | `STAGE_2_GATE = 8` | implemented |
| Stage 3 | `roic_score >= 6` of 10 **and no exclusion fired** | `STAGE_3_GATE = 6` | implemented |
| Stage 4 | `moat_total >= 6` (of 18) **AND** `moat_durability >= 3` (of 5) | `MOAT_TOTAL_GATE = 6`, `MOAT_DURABILITY_GATE = 3` | implemented |

Clearing a gate calls `db.set_stage()`, which only ever raises the high-water mark. A ticker that later fails a re-score is not demoted — it is excluded. See [schema.md §1](schema.md#1-two-orthogonal-axes-stage-and-status).

The Stage 4 gate is the only one that also sets `status`: clearing it calls `db.set_status(..., 'watchlist')`, because **Stage 4 survivors are Watchlist B — the funnel's output.**

## 5. Stage 3 — ROIC + avoidance (0–10) — implemented

`src/roic.py`, from SEC EDGAR XBRL `companyfacts` — a **primary source**, chosen precisely because it cross-checks the yfinance figures Stage 2 relies on. Where the two disagree, this stage wins. EDGAR's contract, its rate limit, and its tag-coverage traps are in [data-sources.md](data-sources.md).

Three metrics, max points summing to exactly **10**, evaluated by the same first-band-wins primitive as Stage 2 — literally the same function: `scorer.band()` was renamed from `_band` so Stage 3 could reuse it rather than duplicate it.

| # | Metric | Direction | Bands (first match wins) | Max |
|---|--------|-----------|--------------------------|-----|
| 1 | `roic_3y_median` | higher | ≥ 20% → **5** · ≥ 15% → **4** · ≥ 12% → **3** · ≥ 10% → **2** · ≥ 7% → **1** · else 0 | 5 |
| 2 | `piotroski_f` | higher | ≥ 7 → **3** · ≥ 5 → **2** · ≥ 4 → **1** · else 0 | 3 |
| 3 | `altman_z` | higher | ≥ 3.0 → **2** · ≥ 1.8 → **1** · else 0 | 2 |
| | | | **Total** | **10** |

Constants: `ROIC_BANDS`, `PIOTROSKI_BANDS`, `ALTMAN_Z_BANDS`, `ROIC_MAX_SCORE = 10`.

ROIC carries half the weight because it is the headline number of the whole funnel; Piotroski is the accounting-quality confirm; Altman is a solvency floor. The 6/10 gate means **no ticker passes on ROIC alone.**

### The arithmetic, exactly

**ROIC** (`roic.roic`, per fiscal year):

```
ROIC = EBIT × (1 − effective tax rate) / (equity + total debt − cash)
```

- The **effective tax rate** is `IncomeTaxExpenseBenefit / pretax income`, but falls back to `DEFAULT_TAX_RATE = 0.21` whenever it is absent or lands outside 0–50% — a one-off tax credit otherwise produces a negative rate and a nonsense NOPAT.
- **Absent debt or cash tags are read as zero, not as unknown.** For those two, absence overwhelmingly means the company has none, and treating it as unknown would strip every debt-free company of a score — precisely the companies this screen is looking for.
- **Non-positive invested capital yields `None`, not a stellar ratio.** A negative capital base makes the ratio meaningless, and a naive division would rank such a company top of the funnel.

`roic_3y_median` is the median over the last `ROIC_MEDIAN_YEARS = 3` fiscal years it could be computed for — a median, not a mean, so one exceptional year cannot carry a mediocre company.

**Piotroski F** (`roic.piotroski_f`) is the standard 9 signals — ROA positive, CFO positive, ROA rising, CFO > net income (accrual quality), deleveraging, current ratio rising, no share issuance, gross margin rising, asset turnover rising. Two rules matter:

- **A signal that cannot be evaluated scores 0. It is never awarded on faith.** Missing data therefore depresses the F-score, which is the conservative direction.
- It returns `None` entirely if there is no prior comparison year — every signal is a year-over-year delta.

**Altman Z** (`roic.altman_z`) is the public-company formula (`1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + 1.0·Sales/TA`). It needs `universe.market_cap` for MVE — **the one input XBRL cannot supply** — so a ticker with no market cap gets `None`, not a wrong number.

`asset_cagr` and `ebitda_cagr` (EBIT + D&A) are computed for the `ASSET_BLOAT` check in §3, not for points.

### NULL is not zero. The dashboard depends on this.

**`roic_score` is always written when the stage runs**, even when XBRL coverage failed completely and it is 0.

| State | Means |
|---|---|
| `roic_score` **NULL** | Stage 3 never ran on this ticker |
| `roic_score` **0** + `XBRL_INCOMPLETE` | Stage 3 ran and found nothing — **unmeasured, not bad** |
| `roic_score` **0**, no warning | Stage 3 ran and the company genuinely scored 0 |

Never read the middle row as a verdict on the company. It is a statement about EDGAR's tag coverage. `XBRL_INCOMPLETE` is a warning appended to `data_warnings` (via `db.merge_warnings`, which unions rather than overwrites, so Stage 3 cannot erase Stage 2's yfinance warnings) and it **never excludes** (PRD §2.4). The success criterion is **≥80% ROIC coverage, not 100%** — see [data-sources.md §3](data-sources.md#3-xbrl_incomplete--a-coverage-gap-never-a-verdict) for its two causes. Foreign private issuers (ADRs) that file 20-F in IFRS or a non-USD currency are now read like any other filer — see [data-sources.md §7](data-sources.md#7-foreign-private-issuers--20-f-ifrs-and-currency).

## 6. Stage 4 — moat (0–10) — implemented

`src/moat.py` + `.claude/skills/hunt-moat/SKILL.md`. This is the one stage where **Claude Code is the reasoning engine**, via fetch → judge → save (see [architecture.md](architecture.md#the-fetch--judge--save-pattern)).

**The rubric lives in `.claude/skills/hunt-moat/SKILL.md` and deliberately nowhere else — not in this file, not in Python.** That is a load-bearing constraint, not a preference: it is what keeps the project free of an LLM SDK. Read the SKILL.md for what a 0, 1, 2 or 3 actually looks like on each dimension.

**Claude judges; Python does arithmetic.** Claude supplies the six dimension scores; `moat.validate()` checks their shape and `moat.save_ticker()` sums and derives. A `moat_total` supplied in the payload is ignored — the judge does not get to do the addition.

| Field | Range | Source |
|-------|-------|--------|
| `moat_distribution`, `moat_brand`, `moat_network`, `moat_regulatory`, `moat_switching`, `moat_cost` | 0–3 each | Claude |
| `moat_total` | 0–18 | **summed in Python** from the six above |
| `moat_durability` | 0–5 | Claude |
| `founder_led` | bool | Claude |
| `reinvest_runway` | `narrow` \| `medium` \| `wide` | Claude |
| `moat_notes`, `key_risks` | text | Claude |
| `moat_score` | **0–10** | **derived in Python** — below |

### The derivation: how 18 + 5 becomes 10

`config.moat_score()` — the one place in the codebase this happens:

```
moat_score = round(6 × moat_total/18  +  4 × moat_durability/5)
```

Constants `MOAT_TOTAL_WEIGHT = 6`, `MOAT_DURABILITY_WEIGHT = 4`. **Durability carries 40%** because over a ten-year hold a wide but eroding moat is worth less than a narrow durable one — breadth tells you the moat exists, durability tells you it will still be there when it matters.

| `moat_total` | `moat_durability` | → `moat_score` |
|---|---|---|
| 12 | 4 | round(4.0 + 3.2) = **7** |
| 18 | 1 | round(6.0 + 0.8) = **7** |
| 6 | 3 | round(2.0 + 2.4) = **4** (the weakest ticker that still clears the gate) |

The middle row is the point: a *perfect* breadth score with fragile durability lands in the same place as a moderate, durable moat.

### The gate

`moat_total >= 6` **AND** `moat_durability >= 3`. Clearing it advances to Stage 4 **and sets `status = 'watchlist'`** — Stage 4 survivors are Watchlist B, the funnel's output.

**A moat miss is not an exclusion.** A below-gate ticker keeps its score row, its stage, and its status; nothing is written to `exclusions`. Judgement is not arithmetic, and a moat we could not see is not a moat we proved absent (PRD §2.4).

## 7. Sell triggers (Phase 3) — implemented

`src/triggers.py` (the arithmetic) + `src/monitor.py` (the I/O) + `.claude/skills/hunt-monitor/SKILL.md` (the 8-K judgement). `triggers.py` is to `monitor.py` what `fundamentals.py` is to `roic.py`: pure functions over the same year-keyed XBRL fact table Stage 3 builds, so the rules can be read and tested without EDGAR. A position is therefore judged against the standard it was admitted on.

### The mechanical table — implemented

Five rules, evaluated independently by `triggers.fired()`. Every firing yields a `(code, detail)` pair; the detail string carries the number that fired it, and lands in `monitoring_log.notes`.

| Code | Trigger | Exact threshold | Constant |
|------|---------|-----------------|----------|
| `ROIC_DETERIORATION` | ROIC below the floor for `SELL_TREND_YEARS` **consecutive** years | ROIC < 10% in **both** of the last 2 computable years | `SELL_ROIC_FLOOR = 0.10`, `SELL_TREND_YEARS = 2` |
| `REVENUE_DECLINE` | Revenue lower than the prior year, `SELL_TREND_YEARS` times running | needs 3 consecutive years; each must be below the one before | `SELL_TREND_YEARS = 2` |
| `MARGIN_COMPRESSION` | Operating margin (EBIT / revenue) down more than 5pp against two years ago | `then − now > 0.05`, strictly greater | `SELL_MARGIN_DROP = 0.05` |
| `DILUTION` | Share count up more than 5% year-over-year | `latest / prior − 1 > 0.05`, strictly greater | `SELL_DILUTION_PCT = 0.05` |
| `DISTRESS_ZONE` | Altman Z back in the bankruptcy zone | `altman_z < 1.8`, strictly less | `ALTMAN_Z_DISTRESS = 1.8` |

`ROIC_DETERIORATION` is the headline: the number the entire funnel selected on has stopped being true. Everything else on the list is corroboration. `DISTRESS_ZONE` reuses the same constant — and the same `fundamentals.altman_z` — as the Stage 3 auto-exclusion in §3, so a position is re-tested against the floor it was admitted over. It needs `universe.market_cap` for the MVE term, **the one input XBRL cannot supply**, so a ticker with no cap yields no trigger rather than a wrong one.

### Two invariants shape every rule

**1. One bad year is not a sell.** Every trend rule needs `SELL_TREND_YEARS` consecutive bad years, or a move too large to be noise. Selling a compounder on one soft year is how you lose the 100-bagger — the funnel exists to find a ten-year hold, and a screen that panics annually cannot deliver one.

**2. A trigger NEVER fires on missing data.** The same invariant as Stages 2–4 (PRD §2.4). Every rule returns `None` when the years it needs are absent: fewer than `SELL_TREND_YEARS` computable ROIC values, fewer than three revenue years, an uncomputable margin, a non-positive prior share count, no assets, no market cap. **An absent XBRL tag is a coverage gap, not a thesis break** — see [data-sources.md §2](data-sources.md#2-xbrl-tag-coverage-is-uneven--and-the-failure-mode-is-silent).

### Red flags — the judgement half

Not in the table above, and deliberately not in Python: a restatement or a going-concern paragraph is not a number, and no regex finds one honestly. Claude Code reads the recent 8-K text that `monitor check` drops in `data/monitor_input/{TICKER}.txt` and returns codes from a **closed vocabulary** (`config.RED_FLAGS`):

| Code | What Claude is looking for |
|------|---------------------------|
| `RESTATEMENT` | prior financials declared unreliable (Item 4.02) |
| `GOING_CONCERN` | substantial doubt about continuing as a going concern |
| `AUDITOR_RESIGNATION` | the auditor resigned or was dismissed |
| `SEC_INVESTIGATION` | a formal SEC inquiry or subpoena |
| `KEY_MAN_DEPARTURE` | the founder/CEO the thesis rests on is leaving |
| `MATERIAL_IMPAIRMENT` | a write-down large enough to restate the asset base |

**The vocabulary is closed and `monitor.validate()` rejects anything outside it.** An invented code would land in `monitoring_log.flags` and silently never match anything the user greps for, which is worse than a loud `ValueError`.

### The recommendation — `triggers.recommend()`

```
ACTIONS = ("HOLD", "REVIEW", "TRIM", "SELL")
```

| Input | → `recommended_action` |
|---|---|
| **any** red flag | **`SELL`** — immediately, regardless of the mechanical count |
| 0 mechanical flags | `HOLD` |
| 1 mechanical flag | `REVIEW` |
| 2 mechanical flags | `TRIM` |
| 3+ mechanical flags | `SELL` |

**A red flag is categorical, not cumulative:** one restatement is a sell however healthy the arithmetic looks. Mechanical triggers accumulate instead, because any one of them in isolation has an innocent explanation and three of them do not.

`monitor check` writes the mechanical verdict (flags as a JSON array, action, notes) to `monitoring_log` and raises a `sell` alert at severity **MEDIUM** per fired trigger. `monitor save` merges Claude's red flags into **today's same row**, re-derives the action, and raises a `red_flag` alert at severity **HIGH**. Saving twice is idempotent — the mechanical codes are re-read from the row, not recomputed. **Saving without a check first raises**: the mechanical triggers are half the verdict. See [schema.md §5](schema.md#5-later-phase-tables).

## 8. Entry signals (Phase 3) — implemented

`src/signals.py` + `.claude/skills/hunt-signals/SKILL.md`. Stages 1–4 answer *what* to buy; this answers *when*, and only for tickers already on Watchlist B (`status = 'watchlist'`). It is **not a score and not a gate** — it changes no ticker's stage or status. It writes `insider_events` and raises `buy` alerts.

### Insider cluster buy — only code `P` counts

| Rule | Exact definition | Constant |
|------|------------------|----------|
| What counts as a buy | Form 4 transaction code **`P`** **and** `AcquiredDisposed == "A"` | `OPEN_MARKET_BUY = "P"` |
| Cluster size | ≥ 3 **distinct people** (not distinct filings) | `CLUSTER_MIN_INSIDERS = 3` |
| Window | those buys fall inside one rolling 90-day window | `CLUSTER_WINDOW_DAYS = 90` |
| Aggregate value | ≥ $100,000 summed across the window | `CLUSTER_MIN_VALUE = 100_000` |
| How far back | 180 days of Form 4s | `INSIDER_LOOKBACK_DAYS = 180` |

**A grant (`A`) or an option exercise (`M`) is compensation, not conviction**, and counting either as a buy signal is the classic way to fool yourself with Form 4 data. A `P` that *disposes* is not a purchase whatever the code says, which is why the acquired/disposed flag is tested too.

**Distinct people, not distinct filings.** One director filing four times in a week is one insider; counting filings would manufacture a cluster out of a single person's conviction.

`signals.cluster()` tries **every buy as a window start**, so a cluster is found wherever it sits in the 180-day lookback rather than only in the most recent 90 days. The strongest qualifying window wins (most insiders, then largest value). Transaction-code and amendment handling is in [data-sources.md §5](data-sources.md#5-form-4--the-insider-signal-and-its-two-landmines).

### Valuation gates — three yes/no tests, not a score

| Gate | Passes when | Constant |
|------|-------------|----------|
| P/FCF | `marketCap / freeCashflow` ≤ 20 | `MAX_P_FCF = 20.0` |
| EV/EBITDA | `enterpriseValue / ebitda` ≤ 15 | `MAX_EV_EBITDA = 15.0` |
| PEG | `trailingPegRatio` ≤ 2 | `MAX_PEG = 2.0` |

All three come from yfinance (`yf.Ticker(t).info`) and each is independently **pass / fail / unknown**. Two rules:

- **A ratio that cannot be computed is UNKNOWN, never a pass** (PRD §2.4). `None` stays `None` through `signals.gates()`.
- **A non-positive denominator yields `None`.** A negative P/FCF is not a cheap stock, and a naive division would rank a cash-burning company as the bargain of the year.

`valuation_ok` means **at least one gate was measurable and none of the measurable ones failed.** An all-unknown valuation is not a pass; one failed gate sinks it.

### Price zone

`signals.price_zone()` — position in the 52-week range: **0.0 at the low, 1.0 at the high**. `(price − low) / (high − low)`, `None` when any of the three is missing or the range is degenerate. The buy zone is the lower half: `zone <= BUY_ZONE_MAX = 0.50`.

### The strength matrix

| `signal_strength` | Condition |
|---|---|
| **`HIGH`** | cluster buy **AND** `valuation_ok` |
| **`MEDIUM`** | cluster buy alone, **OR** (`valuation_ok` **AND** in the buy zone) |
| **`LOW`** | `valuation_ok` alone |
| `None` | nothing measurable passed |

**The cluster buy is what separates `HIGH` from the rest. Cheapness alone can never earn a `HIGH` — price is not a catalyst.**

**Alerts are raised for `HIGH` and `MEDIUM` only.** `LOW` is deliberately silent: it means "nothing broke", and alerting on that trains the user to ignore the feed — which is the only way this feature fails. Alerts dedupe on `ticker + type + message` within a day (`db.add_alert`), so a re-run does not resurrect an alert the user already acknowledged.
