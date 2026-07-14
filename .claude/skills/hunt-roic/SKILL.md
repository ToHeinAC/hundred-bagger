---
name: hunt-roic
description: Stage 3 — compute ROIC, Piotroski F, and Altman Z from SEC EDGAR XBRL for every Stage 2 survivor, score them 0–10, and auto-exclude asset-bloated and financially distressed names. Use after /hunt-score, or when the user wants to rescore Stage 3. Runs 30–60 min.
---

# hunt-roic — Stage 3 ROIC + avoidance screening

Cross-checks the funnel's survivors against a **primary source**. Stage 2's numbers
come from a scraper; these come from the filing itself. Where they disagree, these
win — that is the entire point of the stage.

No judgement is required, so this skill is a thin wrapper: the rubric lives in
`src/config.py` and `docs/scoring.md`, not here and not in your head.

## Before you run

`SEC_USER_AGENT` must be set in `.env` — the SEC requires a contact email on every
request and rejects those without one. If it is unset the CLI fails immediately with
a message saying so. Do not try to work around it; ask the user to set it:

```
SEC_USER_AGENT='Jane Doe jane@example.com'
```

## Run

```
uv run python -m src.roic --batch
```

Add `--limit N` for a smoke run, or `--ticker XYZ` for a single name. The full batch
is **30–60 minutes** — EDGAR is capped at 10 requests/second and the cap is enforced
in code, so this cannot be sped up. Tell the user before starting.

Re-running is idempotent: it overwrites today's score row per ticker.

## What it does

Pulls `companyfacts` once per ticker, then computes:

- **`roic_3y_median`** — NOPAT / invested capital, median of the last 3 fiscal years.
  The single most important number in the funnel.
- **`piotroski_f`** (0–9) — accounting quality.
- **`altman_z`** — solvency.
- **`asset_cagr` vs `ebitda_cagr`** — the asset-bloat check.

These score **0–10** (ROIC 0–5, Piotroski 0–3, Altman 0–2). Tickers scoring **≥ 6/10**
with no exclusion advance to Stage 3. Two rules auto-exclude: `ASSET_BLOAT` (assets
compounding more than 10pp faster than EBITDA — growth bought with the balance sheet)
and `DISTRESS_ZONE` (Altman Z below 1.8).

The exact bands are in `src/config.py`. Read it rather than reciting thresholds.

## Interpreting the output

```
Scored 94  |  fetch failures 3
Excluded 18  |  XBRL incomplete 11
ROIC coverage: 83/94 = 88%  (target >= 80%)
Advanced to Stage 3 (>= 6/10): 41
```

Four numbers matter, and you should comment on each:

- **ROIC coverage** — the headline health check. Small filers use non-standard XBRL
  tags, so some genuinely cannot be computed. **The target is ≥ 80%, not 100%.** If
  coverage drops below 80%, say so plainly — it means the stage's central number is
  missing for too much of the funnel to trust the ranking.
- **XBRL incomplete** — ROIC could not be computed. These are flagged
  (`XBRL_INCOMPLETE` in `data_warnings`) and **left in the funnel**, never excluded.
  Their `roic_score` is a **0 that means "unmeasured", not "bad"**. Never present it
  as a verdict on the company. These are the names worth a manual look.
- **fetch failures** — EDGAR returned nothing at all (no CIK, network error). No row
  is written; the ticker is neither advanced nor excluded. A handful is normal.
- **Excluded** — failed `ASSET_BLOAT` or `DISTRESS_ZONE`, with the number recorded.
  Reversible, like every exclusion.

## Then

Report the histogram shape, the coverage percentage, and the advance count. Stage 3
survivors are the input to `/hunt-moat`, which is where judgement enters the pipeline
— offer it as the next step.
