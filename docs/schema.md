# docs/schema.md — DuckDB schema reference

Source of truth: `src/schema.sql`. All access goes through `src/db.py`, the only SQL surface in the project. Scoring rubrics, bands, gates and exclusion codes are **not** here — see [scoring.md](scoring.md).

The full 9-table DDL ships in Phase 1 so no later phase migrates. Tables the current phase does not populate exist and are empty.

| Table | Holds | Written by | Populated in Phase 1? |
|-------|-------|-----------|-----------------------|
| `universe` | one row per ticker: identity, hard-filter facts, `stage`, `status` | `/hunt-universe`, `/hunt-score` (stage/status) | yes |
| `scores` | one row per (ticker, score_date): all Stage 2/3/4 metrics + subscores | `/hunt-score`, `/hunt-roic`, `/hunt-moat` | quant columns only |
| `exclusions` | machine-readable, reversible exclusion records | `/hunt-score`, `/hunt-roic` | yes (Stage 2 codes) |
| `insider_events` | Form 4 transactions, cluster-buy flag | `/hunt-signals` | no (Phase 3) |
| `alerts` | buy / sell / red-flag alerts, acknowledged flag | `/hunt-signals`, `/hunt-monitor` | no (Phase 3) |
| `monitoring_log` | per-check sell-trigger flags + recommended action | `/hunt-monitor` | no (Phase 3) |
| `portfolio` | open/closed positions with thesis and entry ROIC | `/hunt-portfolio`, dashboard | no (Phase 4) |
| `portfolio_actions` | hold/add/trim/sell/review history | `/hunt-portfolio`, dashboard | no (Phase 4) |
| `portfolio_snapshots` | daily price/value per position | `/hunt-monitor` | no (Phase 4) |

Five sequences (`seq_insider_events`, `seq_alerts`, `seq_monitoring_log`, `seq_portfolio`, `seq_portfolio_actions`) back the `BIGINT` surrogate keys on the append-only tables. The three keyed tables (`universe`, `scores`, `exclusions`) use natural keys instead and are upserted.

---

## 1. Two orthogonal axes: `stage` and `status`

This is the single most misread part of the schema.

| Column | Type | Semantics |
|--------|------|-----------|
| `universe.stage` | `INTEGER`, default 1 | **High-water mark**: the highest stage the ticker ever reached. `db.set_stage()` writes `greatest(stage, ?)` — it never lowers a stage. Re-running an earlier stage cannot demote a ticker. |
| `universe.status` | `VARCHAR`, default `'active'` | **Current disposition**, one of `active` \| `excluded` \| `watchlist`. Freely settable in both directions by `db.set_status()`. |

They are independent. **A ticker can be `stage = 4` and `status = 'excluded'`** — it reached moat scoring, then a later re-score tripped an auto-exclusion. `stage` records history; `status` records where it stands now.

Consequences for queries:

- "What is still in play?" → filter on `status = 'active'`, not on `stage`.
- "How deep did the funnel go?" → group by `stage` (see `db.funnel()`), and read the `active` count alongside the total, because the total includes excluded names that once passed.
- `db.get_universe(stage=N)` filters `stage >= N` (a Stage-3 ticker satisfies "reached Stage 2").

`db.add_exclusion()` sets `status = 'excluded'` as a side effect. Nothing sets it back automatically — reversal is a manual/skill action (see §4).

## 2. `universe`

One row per ticker. Written by `/hunt-universe` (`src/universe.py`) via `db.replace_universe()`, which upserts on `ticker`: an existing ticker keeps its `stage`, `status` and `added_date`, and only refreshes the market facts and `updated_date`. A rebuild therefore never resets pipeline progress.

| Column | Notes |
|--------|-------|
| `ticker` | PK. Yahoo `symbol`. |
| `name` | Yahoo `longName`, falling back to `shortName`. |
| `sector` | One of the six `config.INCLUDED_SECTORS`. Attributed by querying the screener one sector at a time (the screener payload does not return a sector field). |
| `exchange` | Yahoo venue code, restricted to `config.ALLOWED_EXCHANGES` (`NMS NYQ NGM NCM ASE PCX BTS`). Pink sheets (PNK/OQB/OQX/…) are dropped client-side. |
| `market_cap`, `avg_volume` | From the screener; the market-cap band and volume floor are enforced server-side by the query. |
| `revenue_ttm` | **Always NULL in Phase 1.** The screener *filters* on TTM revenue but does not *return* it. The `MIN_REVENUE_TTM` floor is still applied — the value is simply not retrievable from that payload. Do not treat NULL here as "revenue unknown/failed". |
| `stage`, `status` | See §1. |
| `added_date` | Date first seen. Never overwritten on refresh. |
| `updated_date` | Touched by every upsert, `set_stage`, and `set_status`. `db.status_summary()` uses `max(added_date)` as the universe freshness signal. |

## 3. `scores`

**Primary key `(ticker, score_date)` — one row per ticker per day, shared by all three scoring stages.**

Stage 2, Stage 3 and Stage 4 each write *their own columns into the same row*. `db.upsert_score()` takes arbitrary column names as kwargs, validates them against the live schema (an unknown name raises `ValueError`, so a typo cannot inject or silently no-op), and upserts only the columns passed. Columns not passed are left untouched.

Idempotence: re-running a stage on the same day overwrites that stage's columns in today's row. It never duplicates. Score history across dates is preserved — a new `score_date` is a new row, and `db.latest_scores()` returns the most recent row per ticker joined to `universe`.

### total_score recomputation

After every `upsert_score()` call, regardless of which columns were written:

```sql
total_score = coalesce(quant_score, 0) + coalesce(roic_score, 0) + coalesce(moat_score, 0)
```

So `total_score` is **always well-defined but partial**: a ticker that has only been quant-scored has `total_score == quant_score` and NULL `roic_score`/`moat_score`. A low `total_score` therefore means "has not been through the later stages yet" at least as often as it means "scored badly". Range 0–34; composition in [scoring.md](scoring.md).

### Stage 2 columns — populated in Phase 1 by `src/scorer.py`

| Column | Meaning |
|--------|---------|
| `revenue_cagr_3y` | CAGR across the yfinance income-statement revenue series (fraction, e.g. `0.18` = 18%). NULL if fewer than 2 periods or a non-positive endpoint. |
| `gross_margin`, `operating_margin` | Latest period gross profit / operating income (or EBIT) over latest revenue. Fractions. |
| `fcf_margin` | `(operating cash flow + capex)` over latest revenue. Capex is reported negative by yfinance, hence the addition. Fraction, can be negative. |
| `debt_to_equity` | yfinance `info["debtToEquity"]` **divided by 100** — Yahoo reports it as a percentage, this column stores a plain ratio (`0.45`, not `45`). |
| `share_change_pct` | CAGR of the balance-sheet share count. Positive = dilution, negative = buybacks. |
| `insider_pct` | yfinance `heldPercentInsiders`. Fraction. |
| `quant_score` | 0–14. |
| `data_warnings` | See §3.1. |

### 3.1 `data_warnings`

Comma-separated uppercased metric names that **yfinance did not return** for this ticker, e.g. `FCF_MARGIN,INSIDER_PCT`. The code set is exactly the seven Stage 2 metric column names uppercased: `REVENUE_CAGR_3Y`, `GROSS_MARGIN`, `OPERATING_MARGIN`, `FCF_MARGIN`, `DEBT_TO_EQUITY`, `SHARE_CHANGE_PCT`, `INSIDER_PCT`. NULL when nothing is missing.

**A warned metric scored 0 points. That is not the same as scoring badly.** yfinance is unreliable on microcaps by design of the source, not by fault of the company. Read a warned ticker's `quant_score` as a *floor*, not a measurement — its true score is at least as high. Two corollaries, both enforced in code:

- Missing data never triggers an auto-exclusion (`scorer.exclusions_for` guards every rule on `is not None`).
- Missing data never blocks the funnel silently — the ticker just falls short of the gate and stays visible with its warning codes. Flag, don't auto-delete (PRD §2.4).

Any ticker with a non-NULL `data_warnings` that lands near the Stage 2 gate deserves manual review before it is written off.

### Stage 3 columns — written by `/hunt-roic`

`roic_3y_median`, `piotroski_f`, `altman_z`, `asset_cagr`, `ebitda_cagr`, `roic_score` (0–10). Source is SEC EDGAR XBRL `companyfacts`, a primary source, which also serves as a cross-check on the yfinance figures above.

**`roic_score` has three distinct states, and collapsing them loses the point of the stage:**

| State | Means |
|-------|-------|
| `NULL` | Stage 3 never ran on this ticker. |
| `0` **with** `XBRL_INCOMPLETE` in `data_warnings` | It ran and found nothing. **Unmeasured, not bad.** |
| `0` with no warning | It ran, computed the metrics, and they genuinely scored zero. |

Because `total_score` sums subscores with `coalesce(…, 0)`, an unrun stage and a zero-scoring one contribute identically to the total — so always read `total_score` next to `stage` and `data_warnings`, never alone. See [scoring.md §5](scoring.md).

### Stage 4 columns — written by `/hunt-moat`

Written by Claude Code via the fetch→judge→save pattern. Claude supplies the six dimension scores; **Python does the arithmetic** — `moat_total` is summed and `moat_score` derived in `src/moat.py`, never taken from the payload.

| Column | Meaning |
|--------|---------|
| `moat_distribution`, `moat_brand`, `moat_network`, `moat_regulatory`, `moat_switching`, `moat_cost` | Six moat dimensions, 0–3 each. |
| `moat_total` | 0–18. Sum of the six dimensions. |
| `moat_durability` | 0–5. How long the moat is expected to hold. |
| `founder_led` | BOOLEAN. |
| `reinvest_runway` | `narrow` \| `medium` \| `wide`. |
| `moat_notes`, `key_risks` | Free text from Claude's reading of 10-K Item 1. |
| `moat_score` | 0–10, **derived**: `round(6 × moat_total/18 + 4 × moat_durability/5)` (`config.moat_score()`). This is the value that feeds `total_score`; `moat_total` (0–18) does not. Durability carries 40% — a wide but eroding moat is worth less over a ten-year hold than a narrow durable one. See [scoring.md §6](scoring.md). |

## 4. `exclusions`

**Primary key `(ticker, reason, excluded_date)`** — a ticker can carry several distinct reasons, and the same reason re-fired on a later date is a separate, additive record rather than an overwrite. Re-running a stage on the same day upserts in place (refreshes `detail`, resets `reversed = FALSE`).

| Column | Notes |
|--------|-------|
| `ticker` | Not a FK, but always a `universe.ticker`. |
| `reason` | Machine-readable code, e.g. `CHRONIC_DILUTER`. Full table in [scoring.md](scoring.md). |
| `detail` | Human-readable evidence with the actual number, e.g. `7.2% annual share growth`. |
| `stage` | Which stage fired the rule (2 for the quant rules). |
| `excluded_date` | Part of the PK. |
| `reversed` | BOOLEAN, default FALSE. **Exclusions are reversible.** Setting `reversed = TRUE` retires the record without deleting the audit trail — the reason a ticker was once excluded is never lost. |

Every read of exclusions must filter `WHERE NOT reversed` (as `db.exclusion_counts()` does); an unfiltered count includes retired records.

Note the asymmetry: `db.add_exclusion()` sets `universe.status = 'excluded'`, but flipping `reversed` does **not** restore `status` — that is a separate `db.set_status()` call. Reversing an exclusion is a deliberate two-part act.

## 5. Later-phase tables

Ship empty in Phase 1. Column semantics are documented here only where they are not self-evident from the DDL.

**`insider_events`** (Phase 3, `/hunt-signals`) — one row per Form 4 transaction. `is_cluster_buy` marks a purchase inside the winning cluster window; `signal_strength` grades the ticker's overall signal. The surrogate key is not a natural one, so `db.replace_insider_events` **deletes the ticker's rows before inserting**: re-running `/hunt-signals` restates a ticker's Form 4 history rather than appending a second copy of it.

**`alerts`** (Phase 3) — `alert_type` ∈ `buy` \| `sell` \| `red_flag`; `severity` ∈ `HIGH` \| `MEDIUM` \| `LOW`; `acknowledged` is set by the dashboard's acknowledge flow (the **one write path the UI has**) and is the basis of `status_summary()["unacked_alerts"]`. `db.add_alert` dedupes on `(ticker, alert_type, message, created_date)` — the same alert raised twice in one day is one row, so re-running a skill cannot resurrect something the user already acknowledged. The dedupe is **per day**: the same alert on a later date is a new row, by design.

**`monitoring_log`** (Phase 3, `/hunt-monitor`) — one row per ticker per `check_date`; re-checking overwrites it. `flags` is a **JSON array of sell-trigger codes** stored as text (not a comma-separated list — unlike `scores.data_warnings`), so a code can never be confused with a substring of another. It holds mechanical trigger codes and Claude's 8-K red-flag codes in the same array. `recommended_action` ∈ `HOLD` \| `TRIM` \| `SELL` \| `REVIEW`, derived in Python (`triggers.recommend`). `max(check_date)` is the monitoring-freshness signal.

**`portfolio`** (Phase 4) — one row per position, `status` ∈ `open` \| `closed`. `entry_roic` is snapshotted at entry so that thesis drift (entry ROIC vs current ROIC) is measurable without reconstructing history. `realized_return_pct` is written on close.

**`portfolio_actions`** (Phase 4) — append-only decision log. `created_by` ∈ `manual` \| `claude` \| `monitor` distinguishes a user's own action from a Claude-generated recommendation the user confirmed, from one raised by the monitor. `sell_triggers` records the conditions that would flip the action.

**`portfolio_snapshots`** (Phase 4) — PK `(ticker, snapshot_date)`, so a re-run on the same day overwrites rather than duplicates. `status_badge` is a display-layer summary.
