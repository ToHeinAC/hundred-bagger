# 100-Bagger Hunter

A human-driven stock screening funnel for long-horizon compounders, in the
$75M–$2B market cap band.

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
| `/hunt-roic` | Stage 3 — ROIC, Piotroski F, Altman Z from SEC XBRL | 30–60 min |
| `/hunt-moat` | Stage 4 — *you* read each 10-K Item 1 and score the moat | 10–30 min |
| `/hunt-signals` | Entry signals — insider cluster buys, valuation gates, price zone | 2–5 min |
| `/hunt-monitor` | Sell triggers on open positions; *you* read the 8-Ks for red flags | 5–15 min |
| `/hunt-status` | Pipeline summary and data freshness (no network) | < 1 min |

A full cycle is `/hunt-universe` → `/hunt-score` → `/hunt-roic` → `/hunt-moat`,
which takes you from roughly 8,000 US listings to **Watchlist B**: the 20–50 names
that cleared every gate. `/hunt-status` tells you where you are and what to run
next.

The funnel answers *what* to buy. `/hunt-signals` answers *when* — it checks
Watchlist B for insider cluster buys (only open-market purchases count; a grant is
not conviction), three yes/no valuation gates, and where the price sits in its
52-week range, then raises buy alerts. `/hunt-monitor` asks the other question:
*has the thesis broken?* — five mechanical sell triggers computed from SEC XBRL,
plus red flags Claude reads out of recent 8-Ks. Neither changes a ticker's score or
status; both surface on the Alerts page.

`/hunt-moat` and `/hunt-monitor` are the stages where Claude Code is the reasoning
engine rather than a wrapper around a script. Python fetches the filing text to
disk, Claude reads it and judges it against a rubric that lives in the skill, and
Python validates the JSON back into the database — the **fetch → judge → save**
pattern. That is what makes the LLM-free app possible.

Then open the dashboard:

```bash
uv run streamlit run src/app.py --server.port 8501
```

Pipeline Overview (funnel + exclusion breakdown), Watchlist (ranked, filterable),
Stock Detail (price chart, every metric grouped by stage, moat notes, risks —
enough to answer "why is this on my watchlist?" without re-running anything), and
Alerts (buy signals, sell triggers, red flags, with an acknowledge flow). It opens
the database **read-only** everywhere except the one acknowledge write, and carries
a safe exit button that terminates only its own process.

Every module is also a CLI, which is exactly what the skills shell out to:

```bash
uv run python -m src.universe --rebuild
uv run python -m src.scorer   --batch [--limit N]
uv run python -m src.roic     --batch [--limit N]
uv run python -m src.moat     fetch --stage 3
uv run python -m src.moat     save --ticker XYZ --json-file moat.json
uv run python -m src.signals  --check
uv run python -m src.monitor  check --ticker XYZ
uv run python -m src.monitor  save  --ticker XYZ --json-file flags.json
uv run python -m src.db       --status
```

## Status

**Phases 1, 2 and 3 of 4 are implemented.** The funnel runs end to end and produces
Watchlist B; entry signals and position monitoring sit on top of it. The full
9-table schema shipped in Phase 1, so no stage ever migrates.

| Phase | Scope | State |
|---|---|---|
| 1 | Universe, quant scoring (0–14), status, dashboard | **Done** |
| 2 | ROIC from SEC XBRL (0–10), moat scoring by Claude (0–10), Stock Detail | **Done** |
| 3 | Entry signals, sell triggers, 8-K red flags, Alerts page | **Done** — but never yet run against live EDGAR; mocked tests only |
| 4 | Portfolio recommendations | Not started |

The `portfolio` table stays empty until Phase 4, so `/hunt-monitor` currently runs
one ticker at a time via `--ticker` rather than over open positions.

`SEC_USER_AGENT` is **mandatory** from Phase 2 on: the SEC rejects requests
without a contact email. EDGAR's 10 req/s cap is enforced in code, which is why
`/hunt-roic` takes 30–60 minutes and cannot be hurried.

## Configuration

- **`src/config.py`** — screening thresholds (cap band, volume, revenue, sectors,
  scoring bands, stage gates). Code-as-config, under version control, so a change
  to the screen shows up in a diff.
- **`.env`** — environment scalars only (`SEC_USER_AGENT`, `DUCKDB_PATH`,
  `STREAMLIT_PORT`).

## A caveat worth reading

Data sources are unreliable, and the pipeline is built to say so rather than to
paper over it. It **flags rather than excludes** on missing data: an absent metric
scores 0 points and is recorded in `data_warnings`, but never triggers an
auto-exclusion.

So **a low score can mean "the source has no data", not "bad company"** — and the
dashboard always surfaces which. A `0` ROIC score carrying an `XBRL_INCOMPLETE`
warning means *unmeasured*, not *bad*; a NULL means the stage never ran at all.
Those three states are kept distinct on purpose.

yfinance (Stage 2) is an unofficial scraper and is genuinely poor on microcaps.
Stage 3 cross-checks every survivor against SEC XBRL — a primary source — for
exactly that reason. See [docs/data-sources.md](docs/data-sources.md), which also
documents the stale-XBRL-tag trap that silently reported a 2.4% ROIC where the
truth was 14.5%.

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
| [docs/data-sources.md](docs/data-sources.md) | EDGAR contract, rate limits, tag-coverage traps |
| [AGENTS.md](AGENTS.md) | Rules for AI coding tools in this repo |

## Licence

Apache-2.0.
