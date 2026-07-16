# docs/first-principles.md — how to read a score

Read this before reporting Stage 2, 3 or 4. It governs how you **interpret** the numbers, never how they are computed: the rubrics live in `src/config.py` and [scoring.md](scoring.md), and the scripts own every value. Nothing here licenses you to override a score.

The failure mode this exists to prevent: the funnel is excellent arithmetic wrapped around inherited convention, and it is entirely possible to rank 900 companies precisely while measuring the wrong thing. A score is an answer. This is the habit of checking the question.

## 1. Deconstruction — what is actually true here?

Strip out "best practice", "this is how screening is done", and the industry's standard metrics. What is left that is *necessarily* true of a company that returns 100x?

- **Arithmetic.** 100x over 20 years is ~26% annually, compounded, without interruption. That is not a hope; it is a constraint, and almost nothing clears it.
- **Economics.** Value comes from reinvesting capital at a return above its cost, repeatedly. A high return with nowhere to redeploy it is a dividend, not a compounder. This is why `reinvest_runway` exists and why it is the hardest judgement in the funnel.
- **Physics of the market.** The resulting company must *fit somewhere*. A $500M business cannot become a $50B business inside a $5B market. See §5 — this is the 100x plausibility check.
- **Psychology.** The holder must survive the drawdowns. Every 100-bagger had several 50%+ falls; the return belongs to whoever did not sell.
- **Time.** Irreducible. No screen shortens it.

Everything else in this repo — the 0–14 band structure, six moat dimensions, an 8/14 gate — is a *model* of the above, not the above.

## 2. Assumption check — which of our numbers are arbitrary?

Ask of any threshold you are about to report:

- **Why this number?** Why is the Stage 2 gate 8/14 and not 7 or 9? It is a defensible choice, not a law. `ROIC ≥ 20% → 5 points` is a convention that happens to correlate with quality; the necessity underneath is "returns exceed cost of capital", which 20% is merely a proxy for.
- **Why is this metric here at all?** Gross margin is in the rubric because it is *available*, not because a 100-bagger requires one. Availability is not necessity — much of what is easy to measure was chosen for that reason.
- **What is the number a verdict on?** Frequently the answer is "our data coverage", not "the company". A ticker with `data_warnings` scored 0 on the metrics Yahoo lacked. A `roic_score` of 0 from `XBRL_INCOMPLETE` means *unmeasured*, not *bad*. Reporting those as low-quality companies is the single most common lie this funnel can tell.
- **What does the rubric not capture?** Name it out loud. A score's silence is not evidence of absence.

## 3. Rebuild — starting from zero

If you had only §1 and no existing rubric, what would you measure? Usually a short list: can it reinvest, at what return, for how long, and is the market big enough to hold the result. Compare that to what the score in front of you actually measures, and report the gap where it matters.

This is not an invitation to invent a parallel scoring system. It is a check on whether the ranking you are about to present answers the question the user has.

## 4. Implementation — real barriers vs. inherited ones

Distinguish constraints that are physical from those that are habit:

- **Real:** EDGAR's 10 req/s cap. XBRL tag coverage for small filers (~68% at the last run). yfinance's gaps. Item 1 being a marketing document. These do not move because you want them to.
- **Not real:** "screens use these metrics", "market cap bands are how universes are built", "a moat has six dimensions". These are choices, and they can be argued with.

When you report a limitation, say which kind it is.

## 5. The 100x plausibility check

The one first-principles constraint the code enforces. It is a **display and alert only** — it never enters `total_score`, `stage` or `status` (see [scoring.md §9](scoring.md#9-the-100x-plausibility-check--not-a-score)).

For a 100x outcome to be arithmetically possible, the resulting company must fit inside its market:

```
market_cap × 100  <  10 × TAM      ⟺      TAM > 10 × market_cap
```

The `× 10` is deliberate headroom: the TAM itself grows over a 20-year hold through innovation and adjacency, so demanding the future company fit inside *today's* market would reject almost everything. What it does reject is the genuine impossibility — a company whose 100x market cap would exceed its entire market ten times over is not going to 100-bag, whatever its moat score says.

`TAM_HEADROOM_MIN = 10.0` and `MOONSHOT_MULTIPLE = 100` in `src/config.py`; the arithmetic is `config.tam_headroom()`.

## 6. Per-stage notes

### Stage 2 — `/hunt-score`

The score is a verdict on the arithmetic Yahoo happened to have. Before reporting:

- Separate the excluded from the **unmeasured**. `data_warnings` names every metric that scored 0 for absence. A large `incomplete data` count means the histogram's left tail is partly a data artefact — say so.
- The seven metrics are proxies for one question (does it earn well and keep its shares?). Where a ticker's shape is odd — high margin, no growth — the proxy has failed and the number is misleading.
- 8/14 is a choice. Names at 7 are not disqualified by nature, only by our band.

### Stage 3 — `/hunt-roic`

ROIC is the closest the funnel gets to a fundamental truth, and its coverage is the thing most likely to be lying.

- Coverage below 80% means the stage's central number is missing for too much of the funnel to trust the ranking. Report the percentage before the ranking, not after.
- `XBRL_INCOMPLETE` scores 0. That 0 is **unmeasured, not bad** — those are the names worth a manual look, not the ones to discard.
- Report the **100x target market cap** (`market_cap × 100`) alongside the histogram. It is the scale of the claim being made, and it is worth seeing before Stage 4 tests it against a TAM.
- Altman Z and Piotroski F are 1960s–90s models built on large industrials. They are useful and they are conventions.

### Stage 4 — `/hunt-moat`

Deconstruction *is* the posture here. The Business section asserts; you are checking what is necessarily true underneath.

- "Leading position in an attractive market" is a claim with no content. What would have to be physically true for it to hold — and does the filing show that?
- `reinvest_runway` is §1's economics restated. Ask it literally: could this company be ten times its current size doing the same thing?
- The TAM research is §5's input. The market is the one the company *serves*, not the broadest category with a big number attached.
- A moat score and an implausible 100x are not in conflict — a great business can be a bad 100x candidate. Report both.
