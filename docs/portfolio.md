# docs/portfolio.md — the portfolio

Source: `src/portfolio.py` (arithmetic + CSV + quotes), `src/pages/5_Portfolio.py`
(the page), `src/db.py` (all SQL), `src/config.py` (the thresholds). Phase 4.

## Why it is hold-biased

The tracker exists to keep the picks in view and count their progress, **not** to
generate trade signals. That follows the *100 Baggers* research (Christopher
Mayer; Thomas Phelps, *100 to 1 in the Stock Market*): the chief enemy of a
100-bagger is selling too soon. A screen that panics annually cannot deliver a
ten-year hold — the same reasoning that gives [scoring.md](scoring.md#7-sell-triggers)'s
sell triggers their two-consecutive-bad-years rule.

So `hold` is the default and the point. Nothing here is investment advice.

## The three questions

A position raises three questions, and they have three different owners:

| Question | Owner | Where |
|---|---|---|
| *What do I hold?* | the **user** — no skill can derive it | CSV import → `portfolio` |
| *How far has it come?* | **arithmetic** | `price / entry_price`, toward `config.MOONSHOT_MULTIPLE` |
| *Does the thesis still hold?* | **the filings** — via `/hunt-monitor` | `monitoring_log.recommended_action` |

The third is the load-bearing one. `portfolio.recommend` **reads** the monitor's
verdict; it never re-derives one from price. Price knows when a position is down;
only the filings know whether that matters. A ticker `/hunt-monitor` has never
checked has no entry and is left unjudged — never cleared. That is the same
missing-data invariant `triggers.py` keeps: an absent fact is a coverage gap, not
a clean bill of health.

This is why there is no `thesis_broken` flag for the user to tick. A boolean set
by hand would compete with five named, evidenced triggers, and lose.

## The rules

`recommend(gain_pct, weight, monitor_action)` — first match wins:

| Rule | When | Meaning |
|---|---|---|
| **sell** / **review** / **trim** | the monitor said so | Evidenced, from XBRL. Outranks the arithmetic: a broken thesis is a fact about the *business*, weight and drawdown are facts about the *book*. |
| **trim** | `weight > CONCENTRATION_CAP` (0.25) | Risk management only — *never* profit-taking. Outranks the dip, so a position both too big and cheap is trimmed, not topped up. |
| **add** | `gain_pct <= ADD_DIP_PCT` (−0.20) | Thesis-consistent top-up on a dip. |
| **hold** | otherwise | Including a 50-bagger, and any position with no price. |

Note what is **absent**: no rule trims a winner for being up. A `HOLD` verdict
from the monitor is likewise not an action — it falls through to the rules above,
so a clean monitor pass on a 40%-of-the-book position still reads `trim`.

Thresholds are code-as-config in `src/config.py`.

## Vocabulary (a real trap)

Two action vocabularies exist and they are **not** the same:

- `triggers.ACTIONS` — `HOLD|REVIEW|TRIM|SELL`, uppercase, **no `add`**. The
  monitor's answer to "has the thesis broken?"
- `portfolio_actions.action` — `hold|add|trim|sell|review`, lowercase, **has
  `add`**. The book's answer to "what do I do with this position?"

`portfolio.py` uses the second; `_FROM_MONITOR` maps the first onto it.

## CSV contract

Import and snapshot round-trip through the same parser.

- Required: `ticker`, `shares`, `entry_price`. Optional: `entry_date`
  (`YYYY-MM-DD`, defaults to today), `thesis`.
- `quantity` / `buy_price` are accepted as aliases for `shares` / `entry_price`.
- Headers are matched case-insensitively and trimmed; tickers are upper-cased;
  blank lines are skipped.
- A missing column, a non-numeric number or a bad date **raises**, naming the
  row. A portfolio that is quietly wrong is worse than one that fails to load.
- Import appends by default; `--replace` empties the table first, so a corrected
  file is a re-import rather than a merge with what it was meant to fix.

One buy is one row: a second purchase of the same ticker is a second row, not an
edit, so each tranche keeps its own entry price.

## The page

`src/pages/5_Portfolio.py`. Two things it does that no other page does — both
sanctioned, neither accidental (see [architecture.md](architecture.md#streamlit-read-only-discipline)):

1. **It writes.** The CSV import. PRD §6 named this page as the sole UI write
   path from the outset; positions are the user's own facts.
2. **It fetches quotes.** Only on **Refresh prices**, never on load. Offline the
   page still lists the book; price columns are blank and every position reads
   `hold`.

`fetch_prices` uses yfinance `fast_info` (a whole book at once, quote only), while
`monitor.py` and `signals.py` use the heavier `.info`. That duplication is known
and left alone deliberately — see IMPLEMENTATION §6.

Unpriced positions are never dropped from the table: a position missing from the
book is worse than one missing a number. Weights are taken against the *priced*
value, so a partially priced book still yields sensible ones.

## What is not here yet

The *judgement* half of Phase 4: `/hunt-portfolio` with `suggest`, position close
(`realized_return_pct`), and the `portfolio_actions` audit trail — so the page
shows the current action but keeps no history of it. See IMPLEMENTATION §7.

## See also

- [architecture.md](architecture.md) — the read-only discipline and the two write paths
- [schema.md](schema.md) — `portfolio`, `portfolio_actions`, `portfolio_snapshots` DDL
- [scoring.md](scoring.md) — the sell triggers this page reads the verdict of
- [../PRD.md](../PRD.md) §10 — the full Phase 4 spec
