---
name: hunt-score
description: Stage 2 — score every Stage 1 stock 0–14 on quantitative fundamentals (revenue CAGR, margins, FCF, leverage, dilution, insider ownership) and auto-exclude diluters, cash burners, and over-levered names. Use after /hunt-universe, or when the user wants to rescore or rank candidates. Runs 15–30 min.
---

# hunt-score — Stage 2 quantitative scoring

Scores the Stage 1 universe on arithmetic alone. No judgement is required, so
this skill is a thin wrapper: the rubric lives in `src/config.py` and
`docs/scoring.md`, not here and not in your head.

## Run

```
uv run python -m src.scorer --batch
```

Add `--limit N` for a smoke run over the first N tickers, or `--ticker XYZ` for
a single name. The full batch is 15–30 minutes — tell the user before starting.

Re-running is idempotent: it overwrites today's score row per ticker and never
duplicates. Score history across *dates* is preserved.

## What it does

Scores seven metrics into **0–14**, then applies auto-exclusion rules
(`CHRONIC_DILUTER`, `CASH_BURNER`, `EXCESSIVE_LEVERAGE`, `REVENUE_DECLINE`).
Tickers scoring **≥ 8/14** with no exclusion advance to Stage 2.

The exact bands are in `src/config.py`. Read it rather than reciting thresholds.

## Interpreting the output

The CLI prints a per-ticker line, then a score histogram, then a summary:

```
Scored 731  |  fetch failures 12
Excluded 180  |  incomplete data 94
Advanced to Stage 2 (>= 8/14): 94
```

Three numbers matter, and you should comment on each:

- **fetch failures** — yfinance broke on these tickers. They are not scored and
  not excluded; they simply have no row. A handful is normal. If it is a large
  fraction, yfinance is having a bad day — say so rather than treating the run
  as complete.
- **incomplete data** — the ticker scored, but one or more metrics were missing
  and contributed **0 points**. This means a good company can score low purely
  because Yahoo lacks its data. These are flagged, never auto-excluded
  (`data_warnings` column). Mention that low scores here are not verdicts.
- **Excluded** — these failed a hard rule, with the reason recorded. Exclusions
  are reversible.

**Never present a score as a verdict on a company.** It is a verdict on the
arithmetic Yahoo happened to have.

## Then

Report the histogram shape and the advance count. Suggest the user open the
dashboard (`uv run streamlit run src/app.py --server.port 8501`) to inspect the
ranked watchlist and the exclusion breakdown.

Stage 2 survivors are the input to `/hunt-roic`, which cross-checks them against
SEC XBRL — a primary source. Offer it as the next step.
