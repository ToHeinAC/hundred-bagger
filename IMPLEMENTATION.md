# IMPLEMENTATION

Current state of the build. Purpose and scope live in [PRD.md](PRD.md); component
detail lives in [docs/](docs/). This file is the map, not the territory — keep it
under 500 lines and push detail down.

**Phases 1 and 2 of 4 are complete.** Universe → quant scoring → ROIC → moat →
dashboard runs end to end against live data. The funnel now produces Watchlist B.

---

## 1. What exists

| Component | File | State |
|---|---|---|
| Config (thresholds + `.env`) | `src/config.py` | Done |
| Schema, 9 tables | `src/schema.sql` | Done — full schema, no migration ever |
| DB access (only SQL surface) | `src/db.py` | Done |
| Stage 1 universe | `src/universe.py` | Done |
| Stage 2 quant scoring | `src/scorer.py` | Done |
| SEC EDGAR client | `src/xbrl.py` | Done — **the one place the rate limit lives** |
| Stage 3 ROIC + avoidance | `src/roic.py` | Done |
| Stage 4 moat (fetch/save) | `src/moat.py` | Done — judgement is Claude's |
| Dashboard | `src/app.py`, `src/pages/` | Done — Pipeline, Watchlist, Stock Detail |
| Skills | `.claude/skills/hunt-{universe,score,roic,moat,status}/` | Done |
| Tests | `tests/` | Done — 110, network mocked, green offline |
| Signals / monitoring | `src/signals.py`, `src/monitor.py` | **Not started** (Phase 3) |
| Portfolio | `src/portfolio.py` | **Not started** (Phase 4) |

`total_score` (0–34) is now actually reachable: `quant_score` (0–14) +
`roic_score` (0–10) + `moat_score` (0–10).

---

## 2. Running it

```bash
uv sync
cp .env.example .env          # SEC_USER_AGENT is now MANDATORY
uv run python -m src.db --init

# In Claude Code:
/hunt-universe    # ~5-10 min
/hunt-score       # ~15-30 min
/hunt-roic        # ~30-60 min   (EDGAR rate cap; cannot be sped up)
/hunt-moat        # ~10-30 min   (you read the 10-Ks)
/hunt-status

uv run streamlit run src/app.py --server.port 8501
uv run pytest
```

---

## 3. Module CLI contract

This is what keeps `SKILL.md` files free of Python — skills shell out, they never
import.

```
uv run python -m src.universe --rebuild [--json]
uv run python -m src.scorer   --batch [--limit N] | --ticker XYZ
uv run python -m src.roic     --batch [--limit N] | --ticker XYZ
uv run python -m src.moat     fetch [--stage 3] [--limit N] [--force]
uv run python -m src.moat     save --ticker XYZ (--json '{...}' | --json-file PATH)
uv run python -m src.db       --init | --status
```

Phases 3–4 extend this with `src.signals`, `src.monitor`, and `src.portfolio` —
see [PRD.md](PRD.md) §10.

---

## 4. Verified against live data

Actually run, not asserted.

**Stage 1** — 762 tickers, inside the 400–1,200 target band:

```
after region        19,994
after sector        10,595  (−9,399)
after market_cap     2,328  (−8,267)
after volume         1,088  (−1,240)
after revenue          797    (−291)
after OTC filter       762     (−36)
```

**Stage 2** — scores, excludes with a reason, advances. `CRVL` 9/14 → Stage 2;
`EEX` 4/14 + `CHRONIC_DILUTER` → excluded, reversibly.

**Stage 3** — `AMPH` scores 6/10 on a 14.5% median ROIC from live EDGAR XBRL and
advances. `AHMA` flags `XBRL_INCOMPLETE` and is **left in the funnel**, not
excluded (it is a 20-F foreign filer — see §5).

**Stage 4** — the fetch → judge → save round-trip works end to end: `moat fetch`
pulled AMPH's real Item 1 (10-K filed 2026-02-26, 122K chars, truncated to 40K)
to `data/moat_input/AMPH.txt`; `moat save` validated the JSON, summed
`moat_total` 10/18, derived `moat_score` 7/10, cleared the gate, and promoted the
ticker to Stage 4 + `status='watchlist'`. Out-of-range input is rejected with a
precise `ValueError`.

> **Note on funnel depth.** Only ~40 of the 762 tickers were ever quant-scored (a
> Phase-1 smoke run), so just 2 reached Stage 2 and the Stage 3/4 cohorts are
> correspondingly tiny. The *pipeline* is verified; the *funnel* is not yet
> populated. Run a full `/hunt-score` to get a real Stage 2 cohort, and only then
> is the ≥80% ROIC coverage criterion meaningfully measurable.

---

## 5. Decisions worth knowing

Things a future contributor (human or AI) would otherwise re-derive the hard way.

### The stale-tag trap — the most expensive bug in this codebase

`xbrl.annual()` takes a **fallback chain** of XBRL tags, and it deliberately does
**not** return the first tag with data.

A company that migrates tags mid-life keeps reporting the retired one for its old
years. AMPH moved to `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`
in 2022 but still carries `StockholdersEquity` for 2011–2021. First-tag-wins
returned a series ending in **2021**, so ROIC was computed from 2019–2021 numbers
and reported as current: **2.4% instead of the true 14.5%.**

It was wrong, and *nothing about it looked wrong*. So the chain now prefers the
most **current** series (freshest max year; ties broken by length, then chain
order). The general rule, which applies to every metric in Stage 3:

> **A stale series is worse than no series.** A missing number gets flagged. A
> stale one does not.

### `XBRL_INCOMPLETE` has two distinct causes

Do not read it as one signal:

1. **Genuinely non-standard tags** — small filers use them; this is the accepted
   gap the 80% coverage criterion exists for.
2. **Out-of-scope filers.** Foreign private issuers file **20-F**, not 10-K.
   `xbrl.ANNUAL_FORMS` is `{10-K, 10-K/A}`, so a 20-F filer yields no facts at
   all. AHMA is one, and it slipped past the Stage 1 region filter. Flagging (not
   excluding) is correct — non-US issuers are out of scope per PRD §4.

### NULL vs zero on a subscore

Load-bearing, and the Stock Detail page depends on it. `roic_score` is **always
written**, even when coverage failed and it is 0.

- **NULL `roic_score`** = Stage 3 never ran.
- **`0` + `XBRL_INCOMPLETE`** = it ran and found nothing. **Unmeasured, not bad.**

`total_score` sums subscores with `coalesce(…, 0)`, so a page that rendered an
unrun stage as zeros would read as "scored badly on ROIC" when the truth is "we
never looked". The dashboard collapses an unscored stage to an explicit
"not yet scored — run `/hunt-roic`" instead.

### Missing data is flagged, never excluded

The distinction that matters most in this codebase, and it holds at every stage. A
metric a source did not return scores **0 points** and is recorded in
`scores.data_warnings`. It does **not** trigger an auto-exclusion — exclusion
rules fire only on a metric that is actually present.

`db.merge_warnings` **unions** warning codes rather than overwriting, so Stage 3
does not erase the yfinance gaps Stage 2 recorded.

A fetch failure (yfinance or EDGAR) leaves the ticker with **no score row at
all** — neither advanced nor excluded. Batches report the failure count rather
than aborting.

### Claude judges, Python does arithmetic

`moat save` takes Claude's six dimension scores and **sums `moat_total` itself**;
a `moat_total` in the payload is ignored. `moat_score` is derived by
`config.moat_score()` — the one place 18 + 5 becomes 10. The **rubric lives in
`.claude/skills/hunt-moat/SKILL.md` and nowhere else**, which is what keeps this
project free of an LLM SDK.

Clearing the moat gate promotes to Stage 4 **and** sets `status='watchlist'` —
Stage 4 survivors *are* Watchlist B, the funnel's output. A moat miss records the
score but is **not** an exclusion.

### `scorer.band` is shared

Renamed from `_band` in Phase 2 so `roic.py` could reuse the same first-band-wins
primitive rather than duplicate it. Stage 2 and Stage 3 evaluate their rubrics
identically.

### `stage` vs `status`

`stage` is a high-water mark (never lowered by `set_stage`); `status` is
orthogonal (`active|excluded|watchlist`). A ticker can be Stage 4 **and**
excluded — that is the audit trail, not a bug.

### `db.py` validates dynamic columns

`upsert_score(**cols)` takes arbitrary metric columns so Stage 2/3/4 each write
into the same `(ticker, score_date)` row. Column names are checked against the
live schema, so a typo raises `ValueError` rather than silently no-op'ing. Ticker
strings are never interpolated into SQL.

---

## 6. Known gaps

- **The funnel is not populated** — see the note in §4. Stage 2 was only ever run
  over a 40-ticker sample.
- **ROIC coverage is unmeasured in practice.** The ≥80% success criterion needs a
  real Stage 2 cohort to test against; n=2 says nothing.
- **`revenue_cagr_3y` is a CAGR over whatever periods yfinance returned**, usually
  4 annual periods (a true 3y CAGR) but sometimes fewer. The column name promises
  more precision than the data guarantees. (Stage 3's XBRL series does not have
  this problem.)
- **`universe.revenue_ttm` is NULL** — the screener filters on revenue but does
  not return it. Backfilled in Stage 2.
- **`REVENUE_DECLINE` is the dominant exclusion.** It fires on any negative 3y
  revenue CAGR, which in a $50M–$1B universe is a large minority (a 40-ticker
  sample excluded 32, mostly on this rule). Verified as correct, not a sign error.
  It makes the screen strict by design, and is the first threshold to revisit if
  the funnel runs dry.
- **Altman Z uses `universe.market_cap`**, which is refreshed only on a universe
  rebuild. A stale cap skews the Z-score's equity term. Rebuild before a Stage 3
  run if the universe is months old.
- **Item 1 text is truncated at 40K chars** (AMPH's was 122K). The moat judgement
  therefore reads the front of the Business section, which is where the
  substance normally is — but it is a real limit, and the file says when it bit.
- **No Stage 1 sanity gate in code.** The 400–1,200 band is enforced by the
  `/hunt-universe` skill telling Claude to flag an implausible count, not by an
  assertion. Deliberate: a shifting market should not crash a build.
- **Scoring is serial.** A full `/hunt-score` is 15–30 min; `/hunt-roic` is 30–60
  min and is *hard*-capped by EDGAR's 10 req/s limit. Not worth parallelising
  until it hurts.

---

## 7. Next: Phase 3

Entry signals (`src/signals.py` — Form 4 cluster buys, valuation gates) and
position monitoring (`src/monitor.py` — the sell-trigger table, 8-K red-flag
reading by Claude). `/hunt-monitor` is the second judgement-bearing skill and
reuses the fetch → judge → save pattern that Phase 2 established. The
sell-trigger table does not exist yet in code — see [docs/scoring.md](docs/scoring.md) §7.

---

## Component docs

| Doc | Covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | The Python/Claude seam; fetch→judge→save; invariants |
| [docs/schema.md](docs/schema.md) | Full DDL, column semantics, what's populated when |
| [docs/scoring.md](docs/scoring.md) | Quant, ROIC, and moat rubrics; auto-exclusions; gates |
| [docs/data-sources.md](docs/data-sources.md) | EDGAR contract, rate limits, tag-coverage traps |
