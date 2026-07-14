# IMPLEMENTATION

Current state of the build. Purpose and scope live in [PRD.md](PRD.md); component
detail lives in [docs/](docs/). This file is the map, not the territory — keep it
under 500 lines and push detail down.

**Phase 1 of 4 is complete.** Universe → quantitative scoring → status →
dashboard runs end to end against live data.

---

## 1. What exists

| Component | File | State |
|---|---|---|
| Config (thresholds + `.env`) | `src/config.py` | Done |
| Schema, 9 tables | `src/schema.sql` | Done — full schema, no later migration |
| DB access (only SQL surface) | `src/db.py` | Done |
| Stage 1 universe | `src/universe.py` | Done |
| Stage 2 quant scoring | `src/scorer.py` | Done |
| Dashboard | `src/app.py`, `src/pages/` | Done — Pipeline + Watchlist |
| Skills | `.claude/skills/hunt-{universe,score,status}/` | Done |
| Tests | `tests/` | Done — network mocked, green offline |
| Stage 3 ROIC | `src/roic.py` | **Not started** (Phase 2) |
| Stage 4 moat | `src/moat.py` | **Not started** (Phase 2) |
| Signals / monitoring | `src/signals.py`, `src/monitor.py` | **Not started** (Phase 3) |
| Portfolio | `src/portfolio.py` | **Not started** (Phase 4) |

The `scores` table already carries the Stage 3 and Stage 4 columns; they are
NULL until those phases land.

---

## 2. Running it

```bash
uv sync
cp .env.example .env
uv run python -m src.db --init

# In Claude Code:
/hunt-universe    # ~5-10 min
/hunt-score       # ~15-30 min
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
uv run python -m src.db       --init | --status
```

Phases 2–4 extend this with `src.roic`, `src.moat` (`fetch`/`save`),
`src.signals`, `src.monitor`, and `src.portfolio` — see [PRD.md](PRD.md) §10.

---

## 4. Verified against live data

Phase 1's validation gate (PRD §12), actually run rather than asserted:

**Stage 1** — `/hunt-universe` produced **762 tickers**, inside the 400–1,200
target band, with the per-filter drop-off reported:

```
after region        19,994
after sector        10,595  (−9,399)
after market_cap     2,328  (−8,267)
after volume         1,088  (−1,240)
after revenue          797    (−291)
after OTC filter       762     (−36)
```

**Stage 2** — `/hunt-score` scores, excludes with a reason, and advances. Worked
examples: `CRVL` 9/14 → advances to Stage 2; `EEX` 4/14 + `CHRONIC_DILUTER` →
excluded, reversibly.

---

## 5. Decisions worth knowing

Things a future contributor (human or AI) would otherwise re-derive the hard way.

### yfinance 1.5.x, not 0.2.x

The PRD assumed `yfinance>=0.2.40`; the installed version is **1.5.1**. The
screener API is `yf.screen(query, offset, size, sortField, sortAsc)` with
`EquityQuery`. Verified working. Three constraints discovered by probing it:

1. **Page size caps at 250.** Pagination is required.
2. **`sector` is always `None` in the screener payload** — even though it is
   filterable server-side. So `universe.py` runs **one query per sector** and
   attributes the sector itself. This is why the code loops sectors rather than
   issuing a single OR query.
3. **Yahoo's `exchange` filter takes country codes, not venue codes**, so OTC
   cannot be excluded server-side. Pink sheets (`PNK`, `OQB`, `OQX`) are dropped
   client-side against `config.ALLOWED_EXCHANGES`. Skipping this silently admits
   ~36 OTC names.

`revenue_ttm` is filtered on server-side but **not returned** by the screener, so
`universe.revenue_ttm` is NULL after Stage 1. Stage 2 has the real figure.

### Missing data is flagged, never excluded

The distinction that matters most in this codebase. A metric yfinance did not
return scores **0 points** and is recorded in `scores.data_warnings`. It does
**not** trigger an auto-exclusion — `scorer.exclusions_for` only fires on a
metric that is actually present. Consequence: **a low quant score on a ticker
with warnings is a statement about Yahoo's coverage, not about the company.**
The dashboard surfaces `data_warnings` so this stays visible.

A yfinance fetch failure leaves the ticker with no score row at all — neither
advanced nor excluded. The batch reports the failure count rather than aborting.

### `stage` vs `status`

`stage` is a high-water mark (never lowered by `set_stage`); `status` is
orthogonal (`active|excluded|watchlist`). A ticker can be Stage 4 **and**
excluded — that is the audit trail, not a bug.

### `db.py` validates dynamic columns

`upsert_score(**cols)` takes arbitrary metric columns so Stage 2/3/4 can each
write into the same `(ticker, score_date)` row. Column names are checked against
the live schema, so a typo raises `ValueError` rather than silently no-op'ing.
Ticker strings are never interpolated into SQL.

---

## 6. Known gaps

- **`moat_score` (0–10) has no defined derivation.** The schema stores
  `moat_total` (0–18) and `moat_durability` (0–5), and `total_score` sums the
  derived 0–10 `moat_score` — but *nothing in code says how 18 + 5 collapses to
  10*. This must be written into `config.py` **before the first moat score is
  persisted**, or `total_score` will be silently wrong. Blocks Phase 2.
- **`revenue_cagr_3y` is a CAGR over whatever periods yfinance returned**, which
  is usually 4 annual periods (a true 3y CAGR) but can be fewer. The column name
  promises more precision than the data guarantees.
- **`universe.revenue_ttm` is NULL** — the screener filters on revenue but does
  not return it. Backfilled in Stage 2. Harmless, but surprising if unexplained.
- **`REVENUE_DECLINE` is the dominant exclusion** — it fires on any negative 3y
  revenue CAGR, which in a $50M–$1B universe is a large minority of names (a
  40-ticker sample excluded 32, mostly on this rule). Verified as correct, not a
  sign error: the underlying declines are real. But it makes the screen strict by
  design, and it is the first threshold to revisit if the funnel runs too dry.
- **No Stage 1 sanity gate in code.** The 400–1,200 band is enforced by the
  `/hunt-universe` skill telling Claude to flag an implausible count, not by an
  assertion. Deliberate: a legitimately shifting market should not crash a build.
- **Scoring the full 762-ticker universe takes 15–30 min** and is serial. Not
  worth parallelising until it actually hurts.

---

## 7. Next: Phase 2

ROIC from SEC EDGAR XBRL (`src/roic.py`) and moat scoring by Claude Code
(`src/moat.py`), introducing the **fetch → judge → save** pattern —
see [docs/architecture.md](docs/architecture.md). `SEC_USER_AGENT` becomes
mandatory, and the 10 req/s EDGAR cap must be enforced in code.

---

## Component docs

| Doc | Covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | The Python/Claude seam; fetch→judge→save; invariants |
| [docs/schema.md](docs/schema.md) | Full DDL, column semantics, what's populated when |
| [docs/scoring.md](docs/scoring.md) | Quant rubric, auto-exclusions, stage gates |
