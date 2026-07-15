# PRD — 100-Bagger Hunter

> **Purpose.** Product requirements for a human-driven 100-bagger stock screening system. Claude Code *is* the reasoning engine: each pipeline stage is a **skill** the user invokes on demand. Python modules only fetch, compute, and persist — they never call an LLM. State lives in one DuckDB file. A Streamlit dashboard reads it.
>
> **Scope of this document.** The *what* and *why*. The *how* lives in `IMPLEMENTATION.md` (current state) and `docs/` (component detail). This document does not contain implementation code.

---

## 1. Executive Summary

Retail investors hunting for 100-baggers face a screening problem that is 95% mechanical and 5% judgement. The mechanical part — building a universe, computing revenue CAGR, ROIC, Piotroski F, Altman Z, dilution, insider clusters — is tedious and error-prone by hand. The judgement part — *does this company actually have a moat, and is the reinvestment runway wide enough to compound for a decade?* — cannot be reduced to a formula, but it also doesn't need a bespoke LLM integration when the user is already sitting in Claude Code.

This system splits along exactly that seam. **Python does arithmetic and I/O. Claude Code does judgement.** Eight `hunt-*` skills walk a funnel from ~8,000 US-listed stocks down to a Watchlist B of 20–50 qualified candidates, then track entry signals, monitor open positions for sell triggers, and maintain a sample portfolio with position-level hold/trim/sell recommendations. Every stage persists to a single `100baggers.duckdb` file. A Streamlit dashboard visualises the funnel, the watchlist, per-stock detail, the portfolio, and an alert feed.

**Core value proposition:** a screening funnel with a human in the loop at every gate, zero recurring API cost, zero scheduler, and a full audit trail of *why* every stock advanced, was excluded, or was flagged for sale.

**MVP goal:** a user can run `/hunt-universe → /hunt-score → /hunt-status`, open the dashboard on `localhost:8501`, and see a ranked, filterable candidate list backed by a persistent database — with the remaining stages landing in later phases against the same schema.

---

## 2. Mission

**Make the mechanical parts of long-horizon stock screening cheap and repeatable, so the user's attention goes entirely to the judgement calls that actually determine returns.**

### Core principles

1. **No LLM inside the app.** All business logic that needs reasoning is performed by Claude Code via skills, triggered by the user. Python modules are deterministic: fetch, compute, persist. There is no `anthropic` dependency, no API key, no per-run inference cost.
2. **The database is the contract.** Skills and dashboard share exactly one interface: `100baggers.duckdb`. A skill never talks to the dashboard; the dashboard never calls a skill. Either side can be rewritten independently.
3. **Human-triggered, never scheduled.** No daemon, no cron, no background automation. The user decides when to rescreen. A stale universe is a visible fact (`/hunt-status` reports data freshness), not a silent failure.
4. **Flag, don't auto-delete.** When data is ambiguous or a source is unreliable (yfinance on microcaps routinely is), the pipeline flags for manual review rather than silently excluding a candidate. Exclusions are recorded with a reason and are always reversible.
5. **Every advance is auditable.** A stock's stage, score, exclusion reason, moat notes, and monitoring history are all persisted. The user can always answer "why is this on my watchlist?" and "why did I sell?"

---

## 3. Target Users

### Primary persona: the technically-comfortable long-horizon retail investor

- **Who:** an individual investor running their own money, hunting asymmetric multi-year positions in the $75M–$2B market cap band. Comfortable in a terminal, already uses Claude Code, reads 10-Ks.
- **Technical comfort:** high. Can run `uv`, edit a TOML file, read a SQL schema. Does *not* want to maintain a scheduler, a server, or a cloud bill.
- **Time budget:** ~10 min/week for signals and monitoring; ~1 hour/month for a full rescreen; ~2 hours/quarter for a universe rebuild.

### Key needs and pain points

| Need | Current pain |
|------|--------------|
| Screen a wide universe on Mayer-style criteria | Free screeners don't expose ROIC, Piotroski F, or asset-bloat flags; paid ones cost $300+/yr |
| Judge a moat from a 10-K | Reading 30–80 Item 1 sections by hand is a weekend; a generic chatbot loses the scoring rubric between stocks |
| Know *when* to buy, not just *what* | Insider cluster buys and valuation gates are scattered across Form 4s and three ratios |
| Not fool oneself when a thesis breaks | Without recorded sell triggers, a deteriorating position becomes a sunk-cost story |
| Keep a research trail | Spreadsheets rot; the reason a stock was excluded 6 months ago is gone |

### Explicit non-user

Anyone wanting automated or algorithmic trading. This system never places an order, never runs unattended, and produces research artifacts — not investment advice.

---

## 4. MVP Scope

### Core functionality

- ✅ Stage 1 universe build with hard filters (market cap, volume, revenue, country, sector exclusions)
- ✅ Stage 2 quantitative scoring (0–14) from yfinance, with auto-exclusion rules
- ✅ `/hunt-status` pipeline summary with data-freshness reporting
- ✅ DuckDB persistence: the **full 9-table schema** ships in Phase 1, even though later phases populate parts of it
- ✅ Streamlit dashboard: Pipeline Overview + Watchlist pages
- ✅ Safe exit button in the dashboard (SIGTERM to the app's own PID)
- ✅ Stage 3 ROIC + avoidance screening from SEC EDGAR XBRL *(Phase 2)*
- ✅ Stage 4 moat scoring — Claude Code reads 10-K Item 1, scores, writes back *(Phase 2)*
- ✅ Entry signals: insider cluster buys, valuation gates, price zone *(Phase 3)*
- ✅ Position monitoring with a defined sell-trigger table *(Phase 3)*
- ✅ Sample portfolio: positions, actions, snapshots, Claude-generated recommendations *(Phase 4)*

### Technical

- ✅ `uv` for environment and execution; dependencies in `pyproject.toml`
- ✅ All domain modules in `src/`; every module exposes a `python -m src.<module>` CLI so skills shell out rather than import
- ✅ `python-dotenv` for scalar config; screening thresholds live in `src/config.py` with `.env` overrides
- ✅ Apache-2.0 licence
- ✅ Test suite **under 200 tests total**, network mocked

### Out of scope

- ❌ **Any LLM API call from Python.** No `anthropic` dependency. Moat scoring and portfolio suggestions are performed by Claude Code, not by the app.
- ❌ Scheduler, cron, daemon, or any unattended execution
- ❌ Brokerage integration, order placement, paper trading
- ❌ Real-time or intraday data; all data is daily-or-slower
- ❌ Multi-user, authentication, hosted deployment (the app is `localhost`-only)
- ❌ Non-US listings, ADRs, OTC/pink sheets
- ❌ Backtesting the screening rules against historical returns
- ❌ Mobile or responsive design

---

## 5. User Stories

1. **As an investor, I want to rebuild my stock universe with hard filters, so that I start each screening cycle from a defensible ~600–800 stock candidate pool rather than the whole market.**
   *Example:* the user runs `/hunt-universe`; the skill reports `8,142 raw → 2,380 after market cap → 1,790 after volume → 1,204 after revenue → 731 final`, and asks whether to proceed to scoring.

2. **As an investor, I want each stock scored on quantitative fundamentals, so that I can rank hundreds of candidates without opening a single financial statement.**
   *Example:* `/hunt-score` processes 731 tickers, prints a score histogram, and advances the 94 stocks scoring ≥8/14 to Stage 2.

3. **As an investor, I want chronic diluters and cash-burners auto-excluded with a recorded reason, so that I never waste a moat analysis on a company that was disqualified on arithmetic.**
   *Example:* `PLTX` is excluded with reason `CHRONIC_DILUTER` (7.2% annual dilution); the exclusion is visible in the dashboard's exclusion-reason table and can be reversed.

4. **As an investor, I want ROIC computed from primary-source SEC XBRL rather than a scraped ratio, so that I trust the single most important number in the entire funnel.**
   *Example:* `/hunt-roic` pulls `companyfacts` for each Stage 2 survivor, computes a 3-year median ROIC alongside Piotroski F and Altman Z, and flags `ASSET_BLOAT` where asset CAGR outruns EBITDA CAGR.

5. **As an investor, I want Claude Code itself to read the 10-K Business section and score the moat against a fixed rubric, so that I get consistent qualitative judgement across 30–80 companies without paying for an API.**
   *Example:* `/hunt-moat` fetches Item 1 text for the 41 Stage 3 survivors into `data/moat_input/`; Claude reads each, scores six moat dimensions plus durability, and persists the JSON to the `scores` table.

6. **As an investor, I want to know when a qualified candidate becomes *buyable*, so that I act on insider conviction and valuation rather than on boredom.**
   *Example:* `/hunt-signals` reports `🟢 HIGH: CRVL — cluster buy (4 insiders, $310K, 22 days) + P/FCF 16.4`.

7. **As an investor, I want my open positions checked against explicit sell triggers, so that a broken thesis surfaces as a flag rather than as a story I tell myself.**
   *Example:* `/hunt-monitor` flags `ROIC_DETERIORATION` on a position whose ROIC fell below 10% for a second consecutive year and writes a `SELL` recommendation to `monitoring_log`.

8. **As an investor, I want a recommendation on each position that accounts for my original thesis, so that I can judge whether the reason I bought still holds.**
   *Example:* `/hunt-portfolio suggest MELI` — Claude reads the position's thesis, entry ROIC vs current ROIC, recent flags, and prior actions, then recommends `HOLD, 24 months, confidence: medium` with the specific conditions that would flip it to `SELL`.

### Technical user stories

9. **As a developer, I want every domain module to expose a CLI, so that a skill is a thin Markdown wrapper around `uv run python -m src.scorer --batch` rather than a code path of its own.**
10. **As a developer, I want the dashboard to open DuckDB read-only for every page except Portfolio, so that a dashboard bug can never corrupt screening state.**

---

## 6. Core Architecture & Patterns

### The seam: deterministic Python, judgemental Claude

```
┌────────────────┐   invokes    ┌──────────────────┐   shells out   ┌─────────────┐
│      User      │─────────────▶│   Claude Code    │───────────────▶│   src/*.py  │
│                │              │  hunt-* skills   │◀───────────────│   (no LLM)  │
└────────────────┘              └──────────────────┘   stdout/JSON  └──────┬──────┘
                                         │                                 │
                                         │ reads text, applies             │ writes
                                         │ judgement, writes back          ▼
                                         │                          ┌─────────────┐
                                         └─────────────────────────▶│   DuckDB    │
                                                                    │  (1 file)   │
                                                                    └──────┬──────┘
                                                                           │ read-only
                                                                    ┌──────▼──────┐
                                                                    │  Streamlit  │
                                                                    └─────────────┘
```

### The fetch → judge → save pattern

This is the central pattern and it replaces every LLM API call in the original design. Any stage needing judgement is three steps, not one:

1. **Fetch** — a Python CLI writes raw text to disk. `uv run python -m src.moat fetch --stage 3` drops one `.txt` per ticker into `data/moat_input/`.
2. **Judge** — the skill instructs Claude Code to read each file and produce structured JSON against a rubric held *in the SKILL.md*, not in Python.
3. **Save** — Claude calls `uv run python -m src.moat save --ticker CRVL --json '{...}'`, which validates against the schema and persists.

The Python side never sees a prompt; the Claude side never sees SQL. Applies identically to `/hunt-moat` (10-K Item 1), `/hunt-monitor` (8-K red-flag extraction), and `/hunt-portfolio suggest` (position recommendation).

### Directory structure

```
hundred-bagger/
├── .claude/skills/
│   ├── hunt-universe/SKILL.md      # Stage 1 universe build
│   ├── hunt-score/SKILL.md         # Stage 2 quantitative scoring
│   ├── hunt-roic/SKILL.md          # Stage 3 ROIC + avoidance
│   ├── hunt-moat/SKILL.md          # Stage 4 moat scoring (rubric lives here)
│   ├── hunt-signals/SKILL.md       # Entry signal check
│   ├── hunt-monitor/SKILL.md       # Position monitoring + sell triggers
│   ├── hunt-portfolio/SKILL.md     # Portfolio CRUD + recommendations
│   └── hunt-status/SKILL.md        # Pipeline summary
├── src/
│   ├── app.py                      # Streamlit entry point (+ safe exit button)
│   ├── pages/                      # pipeline, watchlist, stock_detail, portfolio, alerts
│   ├── config.py                   # thresholds + .env scalar loading
│   ├── db.py                       # connection, init, status summary — the ONLY SQL surface
│   ├── schema.sql                  # 9-table DDL
│   ├── universe.py  scorer.py  roic.py  moat.py
│   ├── signals.py   monitor.py  portfolio.py
├── tests/                          # <200 tests, network mocked
├── docs/                           # architecture, schema, skills, scoring, data-sources, dashboard
├── data/100baggers.duckdb          # gitignored
├── PRD.md  IMPLEMENTATION.md  README.md  AGENTS.md  CLAUDE.md
└── pyproject.toml  .env.example
```

### Key design patterns

- **`db.py` is the only SQL surface.** No module writes raw SQL. This is what makes the schema safe to evolve.
- **Skills are thin.** A `SKILL.md` contains: which CLI to invoke, how to interpret the output, what rubric to apply if judgement is needed, and what to ask the user next. It contains no Python.
- **Idempotent stages.** Re-running `/hunt-score` overwrites that ticker's row for today's `score_date`; it never duplicates. Score history is preserved across dates.
- **Stage is a high-water mark.** `universe.stage` records the highest stage a ticker reached; `status` (`active | excluded | watchlist`) is orthogonal. A ticker can be Stage 4 *and* excluded.
- **Read-only by default.** Every dashboard page opens DuckDB with `read_only=True` except the Portfolio page, which is the sole write path from the UI.

---

## 7. Skills

Eight skills, each a `SKILL.md` under `.claude/skills/`. Full specifications in `docs/skills.md`.

| Skill | Purpose | Reads | Writes | Runtime |
|-------|---------|-------|--------|---------|
| `/hunt-universe` | Build/refresh Stage 1 universe | yfinance screener | `universe` | 5–10 min |
| `/hunt-score` | Quantitative scoring (0–14) | yfinance | `scores`, `exclusions` | 15–30 min |
| `/hunt-roic` | ROIC + avoidance flags (0–10) | SEC XBRL `companyfacts` | `scores`, `exclusions` | 30–60 min |
| `/hunt-moat` | Moat scoring (0–18) + durability (0–5) | 10-K Item 1 via edgartools | `scores` | 10–30 min |
| `/hunt-signals` | Entry signals for Watchlist B | yfinance + Form 4 | `alerts`, `insider_events` | 2–5 min |
| `/hunt-monitor` | Sell-trigger checks on open positions | XBRL, 8-K, Form 4 | `monitoring_log`, `alerts`, `portfolio_snapshots` | 5–15 min |
| `/hunt-portfolio` | Add / update / close / suggest / review | DB + yfinance | `portfolio`, `portfolio_actions` | interactive |
| `/hunt-status` | Pipeline summary, no network calls | DB only | — | <1 min |

### Judgement-bearing skills (fetch → judge → save)

**`/hunt-moat`** — the rubric lives in the SKILL.md. Claude scores six dimensions (distribution, brand, network effects, regulatory, switching costs, cost structure) at 0–3 each, plus durability 0–5, `founder_led`, `reinvest_runway` (narrow/medium/wide), and top risks. Advance to Stage 4 requires `moat_total ≥ 6 AND moat_durability ≥ 3`.

**`/hunt-monitor`** — Claude reads recent 8-K text for red flags (restatement, going-concern, key-man departure, SEC investigation) and maps them onto the sell-trigger table. Mechanical triggers (ROIC, revenue, margin, dilution) are computed in Python; only the filing-text reading is judgemental.

**`/hunt-portfolio suggest`** — Claude reads the position's thesis, entry-vs-current ROIC, monitoring flags, and prior actions, then emits `{action, horizon_months, confidence, reason, key_risks, sell_triggers}`. The user confirms before it is written with `created_by='claude'`.

### Scoring model

Total score is **0–34**: `quant_score` (0–14) + `roic_score` (0–10) + `moat_score` (0–10, derived from the 0–18 moat total and 0–5 durability). Advancement gates: Stage 2 at ≥8/14, Stage 3 at ≥6/10, Stage 4 at the moat gate above. Rubrics and auto-exclusion rules are specified in `docs/scoring.md`.

---

## 8. Technology Stack

| Layer | Choice | Version | Rationale |
|-------|--------|---------|-----------|
| Language | Python | ≥3.11 | AGENTS.md §5.3 |
| Env / runner | `uv` | latest | AGENTS.md §5.3; deps in `pyproject.toml` |
| Database | DuckDB | ≥0.10 | Zero-server, single file, fast analytical queries, reads/writes DataFrames natively — the whole point is that the dashboard and the skills share one file with no service to run |
| Dashboard | Streamlit | ≥1.35 | Port **8501** (project setting; overrides the global avoid-8501 preference) |
| Charts | Plotly | ≥5.20 | Funnel chart, return distribution |
| Market data | yfinance | ≥0.2.40 | Free; `EquityQuery` gives a server-side pre-filter. Unreliable for microcaps — every fetch logs data-quality warnings |
| Filings | edgartools | ≥2.20 | 10-K Item 1, Form 4 insider transactions, 8-K |
| Fundamentals | SEC EDGAR XBRL `companyfacts` | — | Primary source for ROIC. No API key; requires `SEC_USER_AGENT` email header and ≤10 req/s (sleep 0.11s) |
| Config | python-dotenv | ≥1.0 | Scalars only, per AGENTS.md §5.3 |
| Tests | pytest + responses | — | Network fully mocked; suite stays under 200 tests |

**Deliberately absent:** `anthropic`. There is no LLM SDK in this project. Claude Code is the LLM.

**Optional:** `openbb` / `openbb-finviz` as a fallback universe source if yfinance `EquityQuery` proves unreliable — evaluated in Phase 1, not assumed.

---

## 9. Security & Configuration

### Configuration

Two tiers, per AGENTS.md §5.3:

- **`.env` (scalars, gitignored)** — `SEC_USER_AGENT` (your email, required by SEC), `DUCKDB_PATH`, `STREAMLIT_PORT`. Shipped as `.env.example`.
- **`src/config.py` (screening thresholds)** — market cap band, min volume, min revenue, excluded SIC codes, scoring rubric cutoffs, stage gates. Code-as-config: these are the tunable knobs of the screen, they change together, and they belong under version control so a change to a threshold shows up in a diff.

### Security scope

**In scope:**
- No secrets in the repo. `.gitignore` covers `.env`, `*.duckdb`, and `.claude/settings.local.json`.
- SEC EDGAR rate limiting is enforced in code (10 req/s cap), not left to discipline — exceeding it gets the user's IP blocked.
- DuckDB opened read-only from every dashboard page that does not write.
- Parameterised queries throughout; ticker strings are never interpolated into SQL.

**Out of scope (and why):**
- Authentication / authorisation — the app binds to `localhost` and is single-user by design.
- Encryption at rest — the DuckDB file contains public market data and the user's own position notes, on the user's own machine.
- Network hardening, TLS, CORS — no hosted deployment.

### Deployment

There is none. `uv run streamlit run src/app.py --server.port 8501`, on the user's machine. The dashboard carries a **safe exit button** that sends `SIGTERM` to the app's own PID — never a port-kill, which risks terminating SSH or forwarded connections.

---

## 10. Data Interface (in lieu of an API)

This system exposes no HTTP API. Its two integration surfaces are the **DuckDB schema** and the **module CLIs**.

### DuckDB schema — 9 tables

| Table | Holds | Written by |
|-------|-------|-----------|
| `universe` | ticker, name, cap, sector, `stage`, `status` | `/hunt-universe` |
| `scores` | one row per ticker per `score_date`: all Stage 2/3/4 metrics + subscores + `total_score` | `/hunt-score`, `/hunt-roic`, `/hunt-moat` |
| `exclusions` | ticker + reason + date | `/hunt-score`, `/hunt-roic` |
| `insider_events` | Form 4 transactions, cluster-buy flag, signal strength | `/hunt-signals` |
| `alerts` | buy/sell/red-flag alerts, `acknowledged` | `/hunt-signals`, `/hunt-monitor` |
| `monitoring_log` | per-check flags + recommended action | `/hunt-monitor` |
| `portfolio` | positions: entry, shares, thesis, horizon, status | `/hunt-portfolio`, dashboard |
| `portfolio_actions` | hold/add/trim/sell/review history, `created_by` (manual\|claude\|monitor) | `/hunt-portfolio`, dashboard |
| `portfolio_snapshots` | daily price/value/status-badge per position | `/hunt-monitor` |

Full DDL and column semantics in `docs/schema.md`.

### Module CLI contract

Every domain module is invocable and returns human-readable stdout plus, where relevant, machine-readable JSON:

```
uv run python -m src.universe   --rebuild
uv run python -m src.scorer     --batch [--limit N]
uv run python -m src.roic       --batch
uv run python -m src.moat       fetch --stage 3        # → data/moat_input/*.txt
uv run python -m src.moat       save  --ticker X --json '{...}'
uv run python -m src.signals    --check
uv run python -m src.monitor    --ticker X
uv run python -m src.portfolio  add|update|close|review --ticker X ...
uv run python -m src.db         --status | --init
```

This contract is what keeps `SKILL.md` files free of Python.

---

## 11. Success Criteria

### MVP is successful when

A user with no prior setup can clone the repo, run `uv sync`, set `SEC_USER_AGENT`, invoke `/hunt-universe` and `/hunt-score`, open `localhost:8501`, and see a ranked candidate list they trust enough to act on — with every exclusion explained.

### Functional requirements

- ✅ `/hunt-universe` produces a Stage 1 universe of 400–1,200 tickers and reports the count dropped by each filter
- ✅ `/hunt-score` scores the full Stage 1 universe without a single unhandled exception, and logs a data-quality warning for every ticker with missing yfinance fields
- ✅ Re-running any stage is idempotent — no duplicate rows for the same ticker + date
- ✅ Every excluded ticker has a machine-readable reason code in `exclusions`
- ✅ `/hunt-status` reports data freshness for universe, scores, and last monitoring run
- ✅ The dashboard renders the funnel, exclusion breakdown, and a filterable watchlist from a database populated entirely by skills
- ✅ The safe exit button terminates only the Streamlit process
- ✅ `grep -r anthropic src/ pyproject.toml` returns nothing

### Quality indicators

- Test suite under 200 tests, all network calls mocked, green on a machine with no internet
- ROIC computed for ≥80% of Stage 2 survivors (the rest flagged, not silently dropped — XBRL tag coverage is genuinely uneven for microcaps)
- `IMPLEMENTATION.md` under 500 lines, deferring detail to `docs/`
- No Python file over ~200 lines; no function over ~40 lines

### User experience goals

- A weekly pass (`/hunt-signals` + `/hunt-monitor` + dashboard review) takes under 10 minutes
- The user can answer "why is this stock on my watchlist?" from the Stock Detail page alone, without re-running anything

---

## 12. Implementation Phases

### Phase 1 — Foundation & Quantitative Funnel

**Goal:** a working end-to-end slice — universe in, ranked watchlist out, visible in a browser.

- ✅ `pyproject.toml`, `uv sync`, `.env.example`, Apache-2.0 `LICENSE`, `README.md`
- ✅ `src/db.py` + `src/schema.sql` — **all 9 tables**, so later phases never migrate
- ✅ `src/config.py` — thresholds and `.env` scalars
- ✅ `src/universe.py` + `/hunt-universe`
- ✅ `src/scorer.py` + `/hunt-score` (quant rubric + auto-exclusions)
- ✅ `/hunt-status`
- ✅ `src/app.py` with safe exit button; Pipeline Overview + Watchlist pages
- ✅ `IMPLEMENTATION.md`, `docs/architecture.md`, `docs/schema.md`, `docs/scoring.md`

**Validation:** `/hunt-universe` → `/hunt-score` → `/hunt-status` runs clean; dashboard shows a non-empty funnel and a ranked watchlist; tests green offline.

### Phase 2 — Quality Screen & Moat Judgement

**Goal:** the funnel narrows on ROIC, and Claude's judgement enters the pipeline.

- ✅ `src/roic.py` — XBRL `companyfacts`, ROIC 3y median, Piotroski F, Altman Z, avoidance flags, rate limiting
- ✅ `/hunt-roic`
- ✅ `src/moat.py` — `fetch` (10-K Item 1 → disk) and `save` (validated JSON → DB)
- ✅ `/hunt-moat` with the full scoring rubric in the SKILL.md
- ✅ Stock Detail dashboard page (price chart, metrics, moat notes, risks)
- ✅ `docs/data-sources.md` — EDGAR contract, rate limits, tag coverage caveats

**Validation:** ROIC lands for ≥80% of Stage 2 survivors; a Stage 4 Watchlist B of 20–50 names exists; the fetch→judge→save round-trip persists valid JSON with no `anthropic` import anywhere.

### Phase 3 — Signals & Monitoring

**Goal:** know when to buy, and know when the thesis has broken.

- ✅ `src/signals.py` — Form 4 cluster-buy detection, valuation gates, price zone, signal strength
- ✅ `/hunt-signals`
- ✅ `src/monitor.py` — sell-trigger table, 8-K red-flag fetch, snapshot writes
- ✅ `/hunt-monitor`
- ✅ Alerts dashboard page with acknowledge flow

**Validation:** a synthetic cluster buy in a fixture produces a HIGH signal; each sell trigger in the table has a test that fires it; alerts can be acknowledged from the dashboard and stay acknowledged.

### Phase 4 — Portfolio Intelligence

**Goal:** position-level hold/trim/sell recommendations with a full audit trail.

- ✅ `src/portfolio.py` — add/update/close/review, realized-return calculation
- ✅ `/hunt-portfolio` incl. `suggest` (fetch → judge → save)
- ✅ Portfolio dashboard page: overview, position actions, add position
- ✅ `docs/dashboard.md`; final `README.md` pass

**Validation:** a position can be added in the dashboard and closed via the skill with a correct realized return; `suggest` produces a recommendation the user confirms before it is written with `created_by='claude'`.

---

## 13. Future Considerations

- **Backtesting harness** — replay the screen against historical fundamentals to measure whether the gates actually select for compounders, or merely for survivors.
- **International universe** — the Mayer framework isn't US-specific, but the EDGAR dependency is; supporting non-US names means a second fundamentals source.
- **Score-history charting** — the `scores` table is already keyed by date; a page charting a candidate's ROIC/moat drift over quarters is nearly free.
- **Position sizing** — Kelly-style or conviction-weighted sizing suggestions on the portfolio page.
- **Thesis-drift detection** — diff the current moat scoring against the one at entry and surface the delta as a monitoring flag.
- **Export** — a Markdown research memo per watchlist name, generated by a skill from the DB.

---

## 14. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **yfinance data quality on microcaps** is genuinely poor — missing fields, stale caps, wrong share counts. This is the single biggest threat to the whole funnel. | Silently wrong scores; a good company excluded on bad data | Log a data-quality warning per ticker per missing field; **flag rather than auto-exclude** on missing data; cross-check every Stage 2 survivor's fundamentals against SEC XBRL in Stage 3, which is a primary source. Evaluate `openbb-finviz` as a fallback in Phase 1. |
| **yfinance is an unofficial scraper** and can break without notice | Pipeline dead on a Monday morning | Isolate all yfinance calls behind `src/universe.py` and `src/scorer.py`; keep the OpenBB fallback path evaluated; the DB retains the last good state, so a break degrades to stale-but-usable |
| **SEC EDGAR rate limit / IP block** | Loss of the ROIC source, which is the highest-signal stage | Enforce the 10 req/s cap in code with `time.sleep(0.11)`, not by convention; require `SEC_USER_AGENT` at startup and fail loudly if unset |
| **XBRL tag coverage is uneven** — small filers use non-standard tags, so `OperatingIncomeLoss` is sometimes absent | ROIC unavailable for a slice of candidates | Try a tag fallback chain; on failure, flag `XBRL_INCOMPLETE` for manual review rather than excluding. Success criterion is 80%, not 100% — this is a known, accepted gap. |
| **Scope creep back toward an in-app LLM** — the fetch→judge→save pattern is more steps than `client.messages.create()`, and the temptation to "just add the SDK" is real | Violates the project's core constraint; adds cost, keys, and a failure mode | The `grep -r anthropic` check is an explicit MVP success criterion. The rubric living in `SKILL.md` rather than in Python is what makes the pattern work — keep it there. |
| **Survivorship and confirmation bias in the screen itself** | The user believes the funnel is more predictive than it is | Every stage records *why*; exclusions are reversible and visible; the dashboard shows the funnel's brutal drop-off. A backtesting harness is the honest long-term answer (§13). |

---

## 15. Appendix

### Related documents

| Document | Role |
|----------|------|
| `PRD.md` | This document — purpose, scope, requirements |
| `IMPLEMENTATION.md` | Current implementation state; <500 lines; links into `docs/` |
| `AGENTS.md` | Collaboration rules for AI coding tools; `CLAUDE.md` imports it |
| `docs/architecture.md` | The Python/Claude seam; fetch→judge→save in detail |
| `docs/schema.md` | Full DuckDB DDL and column semantics |
| `docs/scoring.md` | Quant, ROIC, and moat rubrics; auto-exclusion and sell-trigger tables |
| `docs/skills.md` | Per-skill specification |
| `docs/data-sources.md` | yfinance, EDGAR XBRL, edgartools; rate limits and known gaps |
| `docs/dashboard.md` | Page structure, read-only discipline, safe exit |

### Key dependencies

- DuckDB — https://duckdb.org/docs/
- yfinance — https://github.com/ranaroussi/yfinance
- edgartools — https://github.com/dgunning/edgartools
- SEC EDGAR XBRL frames API — https://www.sec.gov/edgar/sec-api-documentation
- Streamlit — https://docs.streamlit.io/

### Licence

Apache-2.0.

### Disclaimer

This is a research tool, not investment advice. Every recommendation it produces — mechanical or Claude-generated — is an input to a human decision that must also account for the user's financial situation, tax position, and position-sizing discipline.
