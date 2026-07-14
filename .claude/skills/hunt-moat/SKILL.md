---
name: hunt-moat
description: Stage 4 — read each Stage 3 survivor's 10-K Item 1 (Business) and score its moat 0–18 across six dimensions plus durability 0–5, then persist the judgement. This is the one stage where you are the reasoning engine, not a wrapper around a script. Use after /hunt-roic, or when the user wants to (re)judge moats. Runs 10–30 min.
---

# hunt-moat — Stage 4 moat scoring

**You are the judge here.** Every other `hunt-*` skill shells out to a script and
reports the number it got back. This one does not. Python fetches the text and
validates your JSON; the scoring is yours, and the rubric below is the only place
it exists. Apply it literally.

## 1. Fetch

```
uv run python -m src.moat fetch --stage 3
```

Writes one `data/moat_input/{TICKER}.txt` per active Stage 3 ticker — the 10-K
Item 1 (Business) section, with a header giving the company, filing date and
accession. Already-fetched tickers are skipped (`--force` to re-pull). Requires
`SEC_USER_AGENT` in `.env`; it fails loudly without one.

## 2. Judge

Read each file. Score it against the rubric. Do this **one company at a time** and
save as you go — do not read forty files and then try to score them from memory.

### The posture: be skeptical

**Item 1 is a marketing document.** The company wrote it, its lawyers cleaned it,
and every filer on earth claims a "leading position in an attractive market."
Claims are not evidence.

- **The default score for a dimension is 0.** Score above 0 only on *specific,
  checkable* evidence — named contracts, quantified switching costs, cited market
  share, a real patent estate, actual customer counts.
- Adjectives are worth nothing. "Strong brand," "sticky customers," "significant
  barriers to entry" score 0 unless the filing shows *why*.
- Absence of evidence is a 0, not a 1. If Item 1 does not discuss a dimension at
  all, that dimension scores 0. Do not award a point for the benefit of the doubt.
- Score what the company *has*, not what it plans. Roadmaps are not moats.

### The six dimensions — 0–3 each, `moat_total` 0–18

**`distribution` — can it reach customers in a way a rival cannot cheaply copy?**

| | |
|---|---|
| **0** | Sells through the same open channels as everyone (generic direct sales, standard retail, an app store). No advantage described. |
| **1** | Some channel depth: an established sales force, a modest partner/reseller network, shelf presence — but replicable with money. |
| **2** | A distribution asset a rival would need years to build: a large exclusive dealer/agent network, deep integration into partners' workflows, a physical footprint with real density. |
| **3** | Distribution is the business's core barrier: exclusive or near-exclusive access to the customer, a channel competitors are structurally shut out of (locked-in OEM relationships, sole-source contracts, a network competitors cannot re-paper). |

**`brand` — does the name let it charge more or sell more easily?**

| | |
|---|---|
| **0** | A B2B supplier competing on price and spec, or a consumer name nobody would pay a premium for. Most microcaps are here. |
| **1** | Recognised within a niche; brand helps win the bid but does not command a premium. |
| **2** | Demonstrable pricing power or preference: premium pricing sustained against cheaper equivalents, repeat purchase driven by the name, brand cited as a reason customers choose it. |
| **3** | The brand *is* the category — the name is the reason to buy, and a rival with an identical product at a lower price still loses. Very rare at $50M–$1B. |

**`network` — does each additional user make the product better for the others?**

| | |
|---|---|
| **0** | No network effect. A product's value is the same to user 10 and user 10,000. **This is the honest answer for the large majority of companies — do not manufacture a network effect out of "we have many customers."** |
| **1** | Weak or indirect: a user community, a developer ecosystem, data that mildly improves with scale. |
| **2** | A real two-sided or data network effect operating within a segment: marketplace liquidity, a data asset that compounds with usage and visibly improves the product. |
| **3** | Strong, self-reinforcing network effects that are the primary barrier — a marketplace or platform where scale makes displacement close to impossible. |

**`regulatory` — does the law, a licence, or IP keep rivals out?**

| | |
|---|---|
| **0** | No regulatory barrier; anyone can enter. |
| **1** | Routine licensing or certification a competitor can obtain with time and money. Generic patents that mostly deter copying. |
| **2** | A meaningful barrier: hard-won approvals (FDA clearances, defence/ITAR clearance, difficult state-by-state licensure), or a patent estate that is central and enforced. |
| **3** | A near-exclusive legal position: an actual monopoly franchise, an orphan/exclusivity window, a certification incumbency competitors would need many years and a track record to obtain. |

**`switching` — what does it cost the customer to leave?**

| | |
|---|---|
| **0** | Trivial to switch. Transactional sales, no contracts, no integration. |
| **1** | Mild friction: retraining, a contract term, minor re-implementation. |
| **2** | Real lock-in: system-of-record status, deep data/workflow integration, multi-year contracts with high renewal rates, revenue that is contractually recurring. Look for a stated retention or renewal rate. |
| **3** | Ripping it out is a business-threatening project: mission-critical embedded systems, regulatory-of-record data, certified components designed into a customer's own product for its lifetime. |

**`cost` — can it profitably undercut rivals, structurally?**

| | |
|---|---|
| **0** | No cost advantage. Same inputs, same scale, same margins as peers. |
| **1** | Modest efficiency: some scale benefit, a decent process, better-than-average utilisation. |
| **2** | A structural advantage a rival cannot match by trying harder: genuine scale economics, proprietary process or automation, uniquely cheap access to an input, an asset-light model in an asset-heavy industry. |
| **3** | A durable low-cost position that sets the industry's floor price — the company is the price-maker and rivals cannot follow it down. |

### `durability` — 0–5

Not "how wide is the moat" but **"will it still be there in ten years?"** Score it
independently of `moat_total`. A wide moat eroding fast is worth less than a
narrow one that holds — that is why this carries 40% of the final `moat_score`.

| | |
|---|---|
| **0** | No moat to be durable, or one actively collapsing (technology shift, disclosed customer/patent losses). |
| **1** | Fragile — dependent on one customer, one contract, one patent nearing expiry, or a single unreplaced key person. |
| **2** | Holds for now but under visible pressure: credible new entrants, commoditising product, share drifting away. |
| **3** | Stable. The advantage has held for several years and nothing in the filing threatens it in the near term. **This is the minimum to clear the Stage 4 gate.** |
| **4** | Strengthening — the advantage compounds with scale, and the filing shows it widening (rising retention, deepening integration, growing network). |
| **5** | Structurally near-permanent: entrenched, self-reinforcing, and protected by something a competitor cannot buy (regulation, decades of switching cost, an unassailable network). Award this rarely and only with hard evidence. |

### `founder_led` — boolean

`true` only if a founder (or a co-founder) is currently CEO, Executive Chairman,
or otherwise clearly running the company. A long-tenured non-founder CEO is
`false`. A founder who is now a passive board member is `false`. If Item 1 does
not say, `false` — do not guess from the company's age.

### `reinvest_runway` — narrow | medium | wide

*Not* how good the company is — how much room it has to redeploy its own cash at
a high rate of return. This is what separates a good business from a compounder,
and it is the single most important qualitative judgement in the whole funnel.

- **`narrow`** — the market is saturated or the model does not absorb capital. Growth
  from here means price increases or acquisitions. Cash comes back as dividends
  and buybacks because there is nothing better to do with it.
- **`medium`** — a clear runway in the existing market (more geographies, more
  segments, adjacent products), but a finite and visible one.
- **`wide`** — the company could deploy many multiples of its current asset base at
  attractive returns: a large under-penetrated TAM it is credibly early in, a
  repeatable expansion unit (a store, a clinic, a market) it can keep replicating.
  Ask literally: *could this company be ten times its current size in the same
  business?* If not, it is not `wide`.

### `notes` and `key_risks`

- **`notes`** — 2–4 sentences justifying the scores, citing what in Item 1 drove
  them. The user must be able to read this in six months and see *why*. Name the
  evidence; do not restate the rubric.
- **`key_risks`** — the 2–4 things that would actually break this moat. Specific
  and falsifiable ("top customer is 31% of revenue and its contract renews in
  2026"), never generic ("competition", "macro conditions").

## 3. Save

One call per ticker, immediately after judging it:

```
uv run python -m src.moat save --ticker CRVL --json '{...}'
```

For long JSON, write it to a file first and use `--json-file PATH` — a big JSON
blob on a command line gets mangled by shell quoting.

Emit exactly this shape:

```json
{
  "distribution": 2,
  "brand": 1,
  "network": 0,
  "regulatory": 2,
  "switching": 3,
  "cost": 1,
  "durability": 4,
  "founder_led": false,
  "reinvest_runway": "medium",
  "notes": "Item 1 describes a claims-management platform embedded in customers' workflows, with the filing citing multi-year contracts and a stated 95% client retention rate. Regulatory scoring reflects state-by-state licensure that took years to assemble. No network effect is described and none is claimed.",
  "key_risks": [
    "Top ten clients are 38% of revenue; a single loss is material",
    "Cost advantage rests on in-house software that a well-funded rival could replicate in ~3 years",
    "State licensure is a barrier to entrants but not to the two incumbents already licensed"
  ]
}
```

Rules on the payload:

- The six dimensions, `durability`, `founder_led`, `reinvest_runway`, `notes` and
  `key_risks` are all **required**. Missing or out-of-range values are rejected.
- **Do not send `moat_total` or `moat_score`.** You score the six dimensions;
  Python sums them and derives the 0–10 score. Arithmetic is not your job here —
  that separation is what keeps the scoring auditable.
- `key_risks` may be a JSON array (preferred) or a string.
- `founder_led` must be a real boolean, not `"true"`.

The command prints the result and whether the ticker cleared the gate:

```
CRVL  moat_total 9/18  durability 4/5  -> moat_score 6/10  |  ADVANCED to Stage 4 (Watchlist B)
```

## 4. The gate

**`moat_total >= 6` AND `moat_durability >= 3`.** Both, not either.

Clearing it advances the ticker to Stage 4 and sets its status to `watchlist` —
this is Watchlist B, the funnel's actual output. Failing it is **not** an
exclusion: the ticker keeps its stage and status, the moat score is recorded, and
the user can revisit it. Nothing is deleted (PRD §2.4).

The gate is deliberately not a high bar on breadth — `moat_total >= 6` is a
median company scoring 1 across the board. The durability floor is what does the
work. A company with a wide moat and `durability = 2` does **not** advance, and
that is correct: it is not going to compound for a decade.

## 5. Then

Report to the user:

- how many tickers you judged, and how many cleared the gate
- the score distribution, and the names at the top
- any ticker whose Item 1 was too thin to judge honestly — say so rather than
  inventing a score from nothing. A file you could not score is a fact worth
  reporting, not a gap to fill.

Remind them that Watchlist B is now populated and visible in the dashboard
(`uv run streamlit run src/app.py --server.port 8501`).

Stage 3 (`/hunt-roic`) must have run first — a Stage 4 judgement on a company
that never passed the ROIC screen is wasted work.
