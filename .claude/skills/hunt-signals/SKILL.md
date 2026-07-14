---
name: hunt-signals
description: Entry signals for Watchlist B — check every Stage 4 survivor for insider cluster buys (Form 4), valuation gates, and where the price sits in its 52-week range, then raise buy alerts. Answers *when* to buy a name the funnel already qualified, never *what* to buy. Use after /hunt-moat, or for a weekly pass over the watchlist. Runs 2–5 min.
---

# hunt-signals — entry signals

Stages 1–4 answered *what*. This answers *when*, and it is a genuinely different
question: a great company at a silly price is not a buy, and neither is a cheap
one that nobody inside is willing to touch with their own money.

You are **not** the judge here — the arithmetic is entirely in Python. Your job is
to run it, read the result honestly, and tell the user what is actually
actionable. That is harder than it sounds; see §3.

## 1. Run

```
uv run python -m src.signals --check
```

Checks every ticker with `status='watchlist'` — the Stage 4 survivors, i.e.
Watchlist B. Add `--ticker XYZ` to check one name.

Requires `SEC_USER_AGENT` in `.env` (Form 4 comes from EDGAR). If the watchlist
is empty the skill says so and stops: entry signals on a name that never passed
the screen are noise, so run `/hunt-moat` first.

Each line reads:

```
[3/12] [HIGH] CRVL   cluster buy (4 insiders, $310,000, 22 days) + P/FCF 16.4 + 31% of 52w range
```

## 2. What the three tests mean

**Insider cluster buy** — the highest-signal input, and the one most easily
faked by a careless reading of Form 4. Only transaction code **`P`** counts: an
open-market purchase, made with the insider's own money. A grant (`A`) and an
option exercise (`M`) are *compensation*, and treating them as conviction is the
standard way to fool yourself with insider data. The code filters them out; do
not add them back by talking about "insider activity" when there were no buys.

A cluster is **several distinct people** (not several filings from one person)
buying inside a rolling window, above an aggregate dollar bar. Thresholds:
`CLUSTER_MIN_INSIDERS`, `CLUSTER_WINDOW_DAYS`, `CLUSTER_MIN_VALUE` in
`src/config.py`.

**Valuation gates** — three independent yes/no tests (P/FCF, EV/EBITDA, PEG), not
a score. A ratio that could not be computed is **unknown, never a pass**. One
failed gate sinks the valuation.

**Price zone** — where the price sits in its own 52-week range, 0% at the low and
100% at the high. It is a tiebreak, not a thesis.

### The strength matrix

| | Valuation ok | Valuation failed or unmeasurable |
|---|---|---|
| **Cluster buy** | **HIGH** | **MEDIUM** |
| **No cluster** | **MEDIUM** if in the buy zone, else **LOW** | no signal |

"Valuation ok" = at least one gate was measurable and none that were measurable
failed. **Cheapness alone never earns a HIGH** — price is not a catalyst.

Alerts are written for **HIGH and MEDIUM only**. LOW means "nothing broke", which
is not news; alerting on it would train the user to ignore the alert feed, and
that is the only way this feature actually fails.

## 3. Then — report like it matters, because it costs money

Lead with the HIGH signals, name them, and say what drove each one. Then MEDIUMs.
Do not list the LOWs individually; count them.

**The honesty rules, in order of how expensive they are to break:**

- **A quiet week is the normal week.** Zero HIGH signals across a 30-name
  watchlist is the *expected* outcome, not a failed run. Say "nothing is
  actionable this week" plainly and stop. Do not go hunting for something to
  report, do not promote a MEDIUM to sound useful, and do not soften it — the
  entire value of this skill is that it is trustworthy when it *does* fire.
- **A signal is not a recommendation.** This says a qualified name is
  *buyable*, not that the user should buy it. Position sizing, tax, and the rest
  of their portfolio are not in this database and are not yours to assume.
- **Say what was unmeasurable.** A name whose valuation gates were all unknown
  (yfinance is unreliable on microcaps) is *unscreened*, not cheap and not
  expensive. Report it as a gap.
- If a cluster buy fired, give the actual numbers — how many insiders, how much,
  over how many days. "Insiders are buying" is not a fact anyone can act on.

Point the user at the **Alerts** page for the feed, where alerts can be
acknowledged.

## 4. Scope

Writes `insider_events` (idempotent — re-running restates a ticker's Form 4
history rather than appending) and `alerts` (`alert_type='buy'`; the same alert
raised twice in one day is one alert, so a re-run cannot resurrect something the
user already acknowledged).

Changes **no** stage and **no** status. A signal is not an advance through the
funnel — the funnel is already done for these names.
