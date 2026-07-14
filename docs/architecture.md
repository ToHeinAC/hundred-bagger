# Architecture

## The seam

One rule explains every structural decision in this repo:

> **Python does arithmetic and I/O. Claude Code does judgement.**

There is no LLM inside the app. No `anthropic` dependency, no API key, no
inference cost. Claude Code — the tool the user is already sitting in — *is* the
reasoning engine, and it reaches the pipeline through skills.

```
┌────────────┐  invokes   ┌───────────────┐  shells out  ┌──────────┐
│    User    │───────────▶│  Claude Code  │─────────────▶│ src/*.py │
│            │            │ hunt-* skills │◀─────────────│ (no LLM) │
└────────────┘            └───────┬───────┘  stdout/JSON └────┬─────┘
                                  │                           │ writes
                                  │ judgement, written back   ▼
                                  │                     ┌──────────┐
                                  └────────────────────▶│  DuckDB  │
                                                        │ (1 file) │
                                                        └────┬─────┘
                                                             │ read-only
                                                        ┌────▼─────┐
                                                        │Streamlit │
                                                        └──────────┘
```

## Three invariants

**1. The database is the contract.** Skills and dashboard share exactly one
interface: `data/100baggers.duckdb`. A skill never talks to the dashboard; the
dashboard never invokes a skill. Either side can be rewritten without touching
the other.

**2. `db.py` is the only SQL surface.** No other module writes SQL. This is what
makes the schema safe to evolve — a column rename is a one-file change. Dynamic
column names in `upsert_score` are validated against the live schema, so a typo
raises instead of silently doing nothing.

**3. Skills are thin.** A `SKILL.md` says which CLI to invoke, how to read the
output, what rubric to apply if judgement is needed, and what to ask next. It
contains no Python. Correspondingly, every domain module exposes a
`python -m src.<module>` CLI, so a skill shells out rather than importing.

## The fetch → judge → save pattern

This replaces every LLM API call in a conventional design. Any stage needing
judgement is three steps, not one:

1. **Fetch** — a Python CLI writes raw text to disk.
   `uv run python -m src.moat fetch --stage 3` → `data/moat_input/*.txt`
2. **Judge** — the skill instructs Claude Code to read those files and produce
   structured JSON against a rubric held **in the SKILL.md**, not in Python.
3. **Save** — Claude calls `uv run python -m src.moat save --ticker X --json '{...}'`,
   which validates and persists.

The Python side never sees a prompt; the Claude side never sees SQL.

The rubric living in Markdown rather than in Python is the load-bearing part. It
is what makes the pattern worth the extra steps, and it is why the temptation to
"just add the SDK" must be resisted — `grep -r anthropic src/ pyproject.toml`
returning nothing is an explicit success criterion (PRD §11).

*Phase 1 contains no judgement-bearing stage.* The pattern lands with
`/hunt-moat` in Phase 2.

## Stage vs status

Two orthogonal axes on `universe`, and conflating them is the easiest available
mistake:

- **`stage`** is a **high-water mark** — the furthest point a ticker reached.
  `set_stage` never lowers it.
- **`status`** is `active | excluded | watchlist` — where it stands *now*.

A ticker can be Stage 4 **and** excluded: it got deep into the funnel, then
failed a rule. That history is the audit trail, and collapsing the two axes would
destroy it.

## Idempotence

Re-running any stage overwrites that ticker's row for today's `score_date` and
never duplicates. Score history across *dates* is preserved, so a candidate's
drift over quarters stays queryable. Rebuilding the universe refreshes market
data but preserves each ticker's `stage` and `status` — a stock excluded six
months ago does not quietly re-enter the funnel.

## Data-quality posture: flag, don't auto-delete

yfinance is an unofficial scraper and is genuinely poor on microcaps — missing
fields, stale caps, wrong share counts. This is the single biggest threat to the
funnel's validity, so the pipeline is built to degrade visibly rather than
silently:

- A missing metric scores **0 points** and is recorded in `data_warnings`. It is
  **not** an exclusion. A low score on a ticker with warnings is a statement
  about Yahoo's coverage, not about the company.
- An auto-exclusion **never fires on a metric that is absent** — only on one we
  actually have (see `scorer.exclusions_for`).
- A yfinance fetch failure leaves the ticker with no score row at all. It is
  neither advanced nor excluded, and the batch reports the failure count.
- Exclusions carry a machine-readable reason, and are reversible.

Stage 3 cross-checks every survivor's fundamentals against SEC XBRL, which is a
primary source, precisely because Stage 2's source is not.

## Streamlit read-only discipline

Every dashboard page opens DuckDB with `read_only=True`. In Phase 1 there is no
write path from the UI at all; the Portfolio page (Phase 4) will be the sole
exception. A dashboard bug can therefore never corrupt screening state.

The app carries a **safe exit button** that sends `SIGTERM` to its own PID. It is
never a port-kill — that risks terminating SSH or forwarded connections.

## Layout

```
.claude/skills/hunt-*/SKILL.md   # one per pipeline stage; no Python
src/
  app.py  pages/                 # Streamlit; read-only
  config.py                      # thresholds (versioned) + .env scalars
  db.py  schema.sql              # the ONLY SQL surface; 9 tables
  universe.py  scorer.py         # Phase 1
  roic.py  moat.py               # Phase 2 (not yet)
  signals.py  monitor.py  portfolio.py   # Phases 3-4 (not yet)
tests/                           # network fully mocked; green offline
data/100baggers.duckdb           # gitignored — the app's entire state
```

## Why DuckDB

Zero server, single file, fast analytical queries, reads and writes DataFrames
natively. The whole point is that skills and dashboard share one file with no
service to run. It is also why "delete the database and re-run the skills" is a
complete and honest recovery procedure.

## See also

- [schema.md](schema.md) — full DDL and column semantics
- [scoring.md](scoring.md) — rubrics, auto-exclusions, stage gates
- [../PRD.md](../PRD.md) — purpose, scope, phases
