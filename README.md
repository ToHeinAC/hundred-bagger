# 100-Bagger Hunter

A human-driven stock screening funnel for long-horizon compounders, in the
$50M–$1B market cap band.

**Claude Code is the reasoning engine.** Each pipeline stage is a skill the user
invokes on demand. Python only fetches, computes, and persists — it never calls
an LLM. There is no `anthropic` dependency, no API key, and no per-run inference
cost. State lives in one DuckDB file; a Streamlit dashboard reads it.

> Research tool, not investment advice. It never places an order and never runs
> unattended.

## How it works

```
User ──▶ Claude Code (hunt-* skills) ──▶ src/*.py (no LLM) ──▶ DuckDB ──▶ Streamlit
```

Python does arithmetic and I/O. Claude Code does judgement. The two meet only at
the database. See [docs/architecture.md](docs/architecture.md).

## Setup

```bash
uv sync
cp .env.example .env      # set SEC_USER_AGENT (needed from Phase 2 on)
uv run python -m src.db --init
```

Requires Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/).

## Use

In Claude Code, invoke the skills:

| Skill | Does | Runtime |
|---|---|---|
| `/hunt-universe` | Stage 1 — build the candidate pool from hard filters | 5–10 min |
| `/hunt-score` | Stage 2 — score 0–14 on fundamentals, auto-exclude | 15–30 min |
| `/hunt-status` | Pipeline summary and data freshness (no network) | < 1 min |

A first cycle is `/hunt-universe` → `/hunt-score` → `/hunt-status`, which takes
you from roughly 8,000 US listings to a ranked shortlist.

Then open the dashboard:

```bash
uv run streamlit run src/app.py --server.port 8501
```

Pipeline Overview (funnel + exclusion breakdown) and Watchlist (ranked,
filterable). It opens the database **read-only** and carries a safe exit button
that terminates only its own process.

Every module is also a CLI, which is exactly what the skills shell out to:

```bash
uv run python -m src.universe --rebuild
uv run python -m src.scorer   --batch [--limit N]
uv run python -m src.db       --status
```

## Status

**Phase 1 of 4 is implemented** — universe, quantitative scoring, status, and the
dashboard. The full 9-table schema ships now, so later phases never migrate.

| Phase | Scope | State |
|---|---|---|
| 1 | Universe, quant scoring (0–14), status, dashboard | **Done** |
| 2 | ROIC from SEC XBRL, moat scoring by Claude | Not started |
| 3 | Entry signals, position monitoring | Not started |
| 4 | Portfolio recommendations | Not started |

## Configuration

- **`src/config.py`** — screening thresholds (cap band, volume, revenue, sectors,
  scoring bands, stage gates). Code-as-config, under version control, so a change
  to the screen shows up in a diff.
- **`.env`** — environment scalars only (`SEC_USER_AGENT`, `DUCKDB_PATH`,
  `STREAMLIT_PORT`).

## A caveat worth reading

yfinance is unreliable on microcaps. The pipeline **flags rather than excludes**
on missing data: an absent metric scores 0 points and is recorded in
`data_warnings`, but never triggers an auto-exclusion. So a low score can mean
"Yahoo has no data" rather than "bad company" — the dashboard surfaces which.
Stage 3 (Phase 2) cross-checks survivors against SEC XBRL, a primary source, for
exactly this reason.

## Tests

```bash
uv run pytest
```

All network calls are mocked; the suite is green with no internet.

## Docs

| Doc | Role |
|---|---|
| [PRD.md](PRD.md) | Purpose, scope, requirements |
| [IMPLEMENTATION.md](IMPLEMENTATION.md) | Current state |
| [docs/architecture.md](docs/architecture.md) | The Python/Claude seam |
| [docs/schema.md](docs/schema.md) | DuckDB DDL and column semantics |
| [docs/scoring.md](docs/scoring.md) | Rubrics, exclusions, stage gates |
| [AGENTS.md](AGENTS.md) | Rules for AI coding tools in this repo |

## Licence

Apache-2.0.
