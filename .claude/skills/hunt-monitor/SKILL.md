---
name: hunt-monitor
description: Check open positions (or any ticker) against the sell-trigger table — five mechanical triggers computed from SEC XBRL, plus red flags you read out of recent 8-K filings — and record a HOLD/REVIEW/TRIM/SELL recommendation. The second judgement-bearing skill; the 8-K reading is yours. Use for a weekly or monthly position review, or when the user asks whether a thesis has broken. Runs 5–15 min.
---

# hunt-monitor — position monitoring and sell triggers

A broken thesis should surface as a **flag**, not as a story the user tells
themselves about why it will come back. That is the whole job.

The check has two halves, split exactly where the project splits:

- **Mechanical triggers** — arithmetic over SEC XBRL, computed in Python
  (`src/triggers.py`). Not yours. They run in step 1.
- **Red flags** — read out of recent 8-K text. **Yours**, in step 2, because a
  restatement or a going-concern paragraph is not a number and no regex finds it
  honestly.

## 1. Check

```
uv run python -m src.monitor check
```

Runs on every open position. Pass `--ticker XYZ` for a single name — and until
Phase 4 populates the portfolio table, `--ticker` is how this runs at all.
Requires `SEC_USER_AGENT` in `.env`.

This computes the mechanical triggers, writes the log and the sell alerts, marks
open positions to market, and drops recent 8-K text into
`data/monitor_input/{TICKER}.txt`.

### The five mechanical triggers

| Code | Fires when |
|---|---|
| `ROIC_DETERIORATION` | ROIC below the floor for two consecutive years — the number the whole funnel selected on has stopped being true |
| `REVENUE_DECLINE` | Revenue lower than the prior year, two years running |
| `MARGIN_COMPRESSION` | Operating margin down more than 5pp against two years ago |
| `DILUTION` | Share count up more than 5% year-over-year |
| `DISTRESS_ZONE` | Altman Z back below 1.8 — the solvency floor Stage 3 screened on |

Thresholds live in `src/config.py`. Two properties are deliberate, and worth
stating to the user when they ask why something did *not* fire:

- **One bad year is not a sell.** Every trend rule needs two consecutive bad
  years. Selling a compounder on one soft year is how you lose the 100-bagger.
- **A trigger never fires on missing data.** An absent XBRL tag is a coverage
  gap, not a thesis break.

## 2. Judge — read the 8-Ks

Read `data/monitor_input/{TICKER}.txt`. Each 8-K in it carries a header with its
filing date and its **reported item numbers**, which are the fastest route to the
substance.

### The posture: almost every 8-K is nothing

**This is the inverse of the moat rubric's problem.** There, the risk was
believing a company's marketing. Here, the risk is **over-firing** — and a red
flag maps straight to a **SELL**. If you invent one, you sell a compounder on a
routine filing, which is the most expensive mistake available to you in this
codebase.

The overwhelming majority of 8-Ks are routine: earnings releases (Item 2.02),
shareholder-vote results (5.07), press releases and investor decks (7.01/8.01), a
new credit facility, a planned board retirement. **None of those are red flags.**
The default answer is an empty list.

Fire a flag only when the filing **says the thing happened**. Not when it might.
Not when it hints. Not when the tone is bad.

### The six red flags — a closed vocabulary

Any code outside this list is rejected by `save`, because an invented code would
land in the log and silently match nothing the user ever greps for.

**`RESTATEMENT`** — previously issued financials cannot be relied upon. Item
**4.02** is the canonical home, and it is close to dispositive on its own. An
immaterial revision or a reclassification is not this.

**`GOING_CONCERN`** — substantial doubt about the company's ability to continue as
a going concern, in those words or unmistakably equivalent ones: auditor doubt, a
covenant breach with no waiver, a stated inability to fund twelve months of
operations. Ordinary "we may need to raise additional capital" risk language is
**not** a going-concern flag.

**`AUDITOR_RESIGNATION`** — the auditor **resigned**, was dismissed amid a
**disagreement**, or the filing discloses a reportable event or material weakness
in connection with the change (Item **4.01**). A routine, amicable change of audit
firm — which happens constantly and is usually about fees — is **not** a red flag.
The disagreement is the signal, not the change.

**`SEC_INVESTIGATION`** — a formal SEC investigation, subpoena, Wells notice, or
enforcement action against the company or its officers. A routine staff comment
letter is not this.

**`KEY_MAN_DEPARTURE`** — the **founder, CEO, or CFO** departing (Item **5.02**),
where the departure is abrupt, unexplained, "for personal reasons", "effective
immediately", or leaves no named successor. **A planned retirement with an orderly
succession and a named replacement is not a red flag**, and neither is an ordinary
director rotating off the board. Ask: does this look like someone was pushed, or
like a plan? Only the first fires.

**`MATERIAL_IMPAIRMENT`** — a material write-down of goodwill or assets (Item
**2.06**). Materiality matters; a small routine impairment is not this.

### If you cannot tell

Say so, and **do not fire the flag**. An honest "the 8-Ks were ambiguous, here is
what I saw" is worth far more than a confident SELL on a filing you half-read. A
ticker with no 8-K file at all simply filed none in the window — common, and a
fine outcome rather than a gap.

## 3. Save

One call per ticker, immediately after reading its filings:

```
uv run python -m src.monitor save --ticker CRVL --json '{"red_flags": [], "notes": "Two 8-Ks in the window: Q3 earnings (2.02) and a routine credit-facility amendment. Nothing material."}'
```

The shape:

```json
{
  "red_flags": ["RESTATEMENT"],
  "notes": "Item 4.02 filed 2026-05-14: the audit committee concluded the FY2024 and FY2025 statements should no longer be relied upon, following errors in revenue cut-off. Restatement pending."
}
```

- `red_flags` — a list, **empty when nothing fired** (the common case). Codes must
  come from the vocabulary above.
- `notes` — what you actually saw, citing the item number and the date. The user
  must be able to read this in six months and reconstruct the call.
- **You must run `check` on the ticker first.** `save` refuses otherwise: the
  mechanical triggers are half the verdict, and a red-flag-only row would record
  half a judgement as if it were whole.
- Saving twice is idempotent. You do not send an action — Python derives it.

For long JSON, write it to a file and use `--json-file PATH`; a big blob on a
command line gets mangled by shell quoting.

## 4. The recommendation

Derived in Python from the merged flags. You do not choose it, and you must not
contradict it:

| Flags | Action |
|---|---|
| Any red flag | **SELL** |
| 3+ mechanical | **SELL** |
| 2 mechanical | **TRIM** |
| 1 mechanical | **REVIEW** |
| none | **HOLD** |

A red flag is **categorical, not cumulative** — one restatement is a sell however
healthy the arithmetic looks. Mechanical triggers accumulate instead, because any
one of them in isolation has an innocent explanation and three of them do not.

## 5. Then

Report per position: the flags, the action, and the *number* behind each flag —
`ROIC_DETERIORATION` means nothing to a user who cannot see the ROIC. Lead with
anything that came out `SELL`.

Then stop. **Do not tell the user to sell.** This skill produces a flag and a
recommendation on the numbers; the decision is theirs, and it turns on tax,
sizing, and conviction that are not in this database. Say what broke, and say what
the table says. That is the deal.

Alerts land in the dashboard's **Alerts** page, where they can be acknowledged.
