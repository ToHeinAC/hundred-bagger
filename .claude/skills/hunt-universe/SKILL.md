---
name: hunt-universe
description: Stage 1 — build or refresh the candidate stock universe by applying the hard filters (market cap, volume, revenue, region, sector, exchange). Use when the user wants to start a screening cycle, rebuild the universe, or asks how many stocks are in the funnel. Runs 5–10 min.
---

# hunt-universe — Stage 1 universe build

Builds the candidate pool the whole funnel draws from. Every filter is a hard
filter: a stock either qualifies or it does not. No judgement is required here,
so this skill is a thin wrapper around one CLI call.

## Run

```
uv run python -m src.universe --rebuild
```

If the database does not exist yet, run `uv run python -m src.db --init` first.

This takes 5–10 minutes. It queries Yahoo's screener once per sector and pages
through the results.

## Filters applied (defined in `src/config.py`)

Market cap $75M–$2B · avg 3-month volume > 50k · TTM revenue > $10M · US region ·
six included sectors (financials, utilities, real estate, energy and basic
materials are excluded) · real exchanges only (OTC/pink sheets dropped).

Do not restate thresholds from memory — read `src/config.py` if the user asks.

## Interpreting the output

The CLI prints the cumulative survivor count after each successive filter, then
the final persisted count. Relay this drop-off to the user — it is the point of
the stage. Example shape:

```
  after region         8,142
  after sector         5,204  (−2,938)
  after market_cap     1,204  (−4,000)
  ...
Stage 1 universe: 731 tickers persisted.
```

**Sanity gate:** a healthy Stage 1 lands at roughly **400–1,200 tickers**.
- Far below 400 → a filter is too tight, or Yahoo returned a partial result. Say so.
- Far above 1,200 → a filter silently failed to apply. Say so.

Do not quietly accept an implausible count. Report it and ask before proceeding.

## Then

Rebuilding is idempotent: existing tickers keep their `stage` and `status`
(a ticker excluded in a prior cycle stays excluded), only the market data is
refreshed. New tickers enter at Stage 1 / active.

Ask the user whether to proceed to `/hunt-score`. Do not chain into it
automatically — scoring takes 15–30 minutes and the user may want to inspect the
universe first.
