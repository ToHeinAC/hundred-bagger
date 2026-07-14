# docs/data-sources.md — EDGAR and yfinance: the contract and the traps

Two external sources, and they are not equals. **SEC EDGAR is a primary source** — it *is* the filing. **yfinance is an unofficial scraper** of a site that never promised us anything. Stage 3 exists partly to cross-check Stage 2 against Stage 3's numbers, and where they disagree, EDGAR wins.

Source of truth: `src/xbrl.py` (EDGAR JSON), `src/moat.py` (EDGAR text), `src/universe.py` + `src/scorer.py` (yfinance). Scoring rules are in [scoring.md](scoring.md); column semantics in [schema.md](schema.md).

## 1. SEC EDGAR — two surfaces, two libraries

| Need | Surface | Library | Module |
|------|---------|---------|--------|
| Stage 3 fundamentals | `companyfacts` JSON API | plain `requests` | `src/xbrl.py` |
| Stage 4 10-K Item 1 text | filing documents | `edgartools` | `src/moat.py` |

Both, not one. The XBRL numbers are already structured, so a JSON API and 40 lines of extraction beat a dependency. Filing *text* is the opposite problem — locating and parsing Item 1 out of a 10-K is exactly what edgartools is good at, and reimplementing it would be foolish.

### `SEC_USER_AGENT` is mandatory

The SEC rejects requests without a real contact email in the `User-Agent` header. Both modules **fail loudly when it is unset** (`xbrl._headers` raises `SecError`; `moat._identity` exits) rather than letting every ticker die of a confusing 403.

```
SEC_USER_AGENT='Jane Doe jane@example.com'
```

### The 10 req/s cap is enforced in code, not by discipline

Exceeding it gets the user's IP blocked — an outage of the funnel's highest-signal stage, caused by us. So it is structural:

- In `src/xbrl.py`, **every** request funnels through `_get()`, which calls `time.sleep(config.SEC_SLEEP)` *first*. There is no other way out to EDGAR, so the cap cannot be bypassed by a new caller.
- In `src/moat.py`, `_throttle()` sleeps before each edgartools entry point. Deliberately conservative — edgartools may issue more than one HTTP request per call, and we cannot see inside it.

`SEC_SLEEP = 0.11` (≈9 req/s). **This is why a full Stage 3 batch takes 30–60 minutes and cannot be sped up.** Do not "optimise" it.

## 2. XBRL tag coverage is uneven — and the failure mode is silent

The known, accepted gap (PRD §14). Small filers use non-standard tags, so every metric in `src/xbrl.py` is a **fallback chain** (`REVENUE`, `EQUITY`, `CFO`, …), and `xbrl.annual(facts, chain)` resolves it.

### It does NOT return the first tag with data. Here is why.

We hit this live, on a real ticker, and it produced a wrong number that looked entirely fine:

> **AMPH (Amphastar)** migrated from `StockholdersEquity` to `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` in 2022 — but it still reports the **retired** tag for 2011–2021, because those years appear as comparatives in old filings.
>
> First-tag-wins matched `StockholdersEquity`, got a series **ending in 2021**, and computed today's ROIC from 2019–2021 numbers: **2.4%, when the truth is 14.5%.**

Nothing about that output looked broken. No exception, no warning, no gap — just a confidently wrong number six times too small, flowing straight into the funnel's most important column.

**The general lesson, and the reason this section exists: a stale series is worse than no series.** A missing number gets flagged `XBRL_INCOMPLETE` and a human looks at it. A stale one gets scored.

So `annual()` prefers the **most current** series: of the tags in the chain that have any data, keep those running to within a year of the freshest, then break ties by length, then by chain order. A tag retired three years ago is discarded however much history it carries.

### Four more extraction rules worth knowing before re-deriving them

- **Series are keyed by the period-end calendar year, not EDGAR's `fy` field.** A single 10-K carries three years of income statement, all tagged with one `fy` — keying on it collapses them.
- **Duration facts must span 340–400 days.** Otherwise a quarter silently contaminates a "yearly" series.
- **Instant facts (balance sheet) have no `start`** and are always accepted.
- **Restatements: the latest-`filed` value wins** for a given year, so a restated figure supersedes the original.

Only `10-K` and `10-K/A` are read (`xbrl.ANNUAL_FORMS`).

## 3. `XBRL_INCOMPLETE` — a coverage gap, never a verdict

When ROIC cannot be computed, `src/roic.py` appends `XBRL_INCOMPLETE` to `scores.data_warnings` and **leaves the ticker in the funnel**. It is never an exclusion (PRD §2.4, §14).

The warning is written by `db.merge_warnings`, which **unions rather than overwrites** — Stage 3 runs after Stage 2 and writes into the same row, and a plain overwrite would erase the yfinance coverage warnings Stage 2 recorded, which are exactly what the dashboard surfaces.

The success criterion is **≥80% coverage, not 100%** (PRD §11). Sub-80% means the stage's central number is missing for too much of the funnel to trust the ranking — say so rather than reporting the run as clean.

See [scoring.md §5](scoring.md#5-stage-3--roic--avoidance-010--implemented) for the NULL-vs-zero distinction that goes with this: a **NULL** `roic_score` means Stage 3 never ran; a **0 with `XBRL_INCOMPLETE`** means it ran and found nothing. Unmeasured, not bad.

### It has two distinct causes. Do not read it as one signal.

1. **Genuinely non-standard tags** — a small filer no chain matches. The intended case.
2. **The company files 20-F, not 10-K.** Foreign private issuers file 20-F, which `ANNUAL_FORMS` excludes, so *every* fact is filtered out and the ticker flags `XBRL_INCOMPLETE`. Live example: **AHMA** maps to a 20-F filer.

Non-US issuers and ADRs are out of scope (PRD §4), so flagging case 2 is correct behaviour — it is the Stage 1 region filter leaking, caught downstream. But it means an `XBRL_INCOMPLETE` ticker is *either* worth a manual look *or* simply out of scope, and only opening it tells you which.

## 4. yfinance — the scraper, and what it is bad at

Stage 1 (`src/universe.py`) and Stage 2 (`src/scorer.py`). Free, unofficial, and **genuinely poor on microcaps** — missing fields, stale caps, wrong share counts. This is the single biggest threat to the funnel's validity (PRD §14), and the reason for the flag-don't-delete posture in [architecture.md](architecture.md#data-quality-posture-flag-dont-auto-delete): a missing metric scores 0 and is recorded in `data_warnings`; it never excludes.

**The installed version is 1.5.x, not the 0.2.x the PRD assumed.** Four constraints, established by probing it in Phase 1 (IMPLEMENTATION.md §5):

- The screener API is `yf.screen(query, ...)` with `EquityQuery`.
- **Page size caps at 250** — pagination is required.
- **`sector` comes back `None`** in the screener payload even though it is filterable server-side, so `universe.py` runs **one query per sector** and attributes the sector itself.
- **Yahoo's `exchange` filter takes country codes, not venue codes**, so OTC cannot be excluded server-side. Pink sheets (`PNK`, `OQB`, `OQX`) are dropped client-side against `config.ALLOWED_EXCHANGES`.

Also: `revenue_ttm` is filtered on server-side but **not returned**, so `universe.revenue_ttm` is NULL after Stage 1. Stage 2 backfills the real figure.

### The dashboard's one network call

Every dashboard page opens DuckDB `read_only=True` and otherwise touches nothing external. The single exception is the **Stock Detail** page's 1-year price chart, which calls `yf.Ticker(t).history()` behind a cache and degrades to a caption if yfinance fails. That is the only network call in the whole dashboard, and it should stay that way.

## See also

- [architecture.md](architecture.md) — the Python/Claude seam; fetch→judge→save
- [scoring.md](scoring.md) — rubrics, auto-exclusions, stage gates
- [schema.md](schema.md) — column semantics, `data_warnings`
- SEC EDGAR API — https://www.sec.gov/edgar/sec-api-documentation
