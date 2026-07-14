---
name: hunt-status
description: Report the screening pipeline's current state — ticker counts per stage, exclusions, watchlist size, open positions, unacknowledged alerts, and how stale the data is. Reads the database only, makes no network calls. Use when the user asks where the funnel stands, what to run next, or how fresh the data is. Runs in under a minute.
---

# hunt-status — pipeline summary

Read-only. No network calls. Safe to run at any time.

## Run

```
uv run python -m src.db --status
```

Emits JSON: counts per stage, active/excluded/watchlist splits, open positions,
unacknowledged alerts, and three freshness dates (`universe_last_built`,
`scores_last_run`, `monitor_last_run`).

If this raises `FileNotFoundError`, the database has not been created. Tell the
user to run `uv run python -m src.db --init`, then `/hunt-universe`.

## Interpreting the output

Summarise the funnel in prose, then say **what to run next**. That
recommendation is the actual value of this skill.

**Staleness is a fact to surface, not a failure to hide** (PRD §2.3 — there is
no scheduler; nothing refreshes unless the user asks):

- `universe_last_built` older than ~3 months → suggest `/hunt-universe`. The
  $50M–$1B cap band means names drift in and out of range constantly.
- `scores_last_run` older than ~1 month, or older than `universe_last_built`
  (which means tickers exist that were never scored) → suggest `/hunt-score`.
- A null date means that stage has never run.

State the dates plainly. Do not soften a stale pipeline.

## Scope

Stages 3–4, signals, monitoring, and portfolio are not implemented yet. Their
counts will read zero. Say "not yet implemented" rather than "none found" —
those mean very different things to a user deciding what to trust.
