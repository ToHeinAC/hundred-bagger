# 100-Bagger Stock Hunting: Claude Code CLI Skills, DuckDB Persistence & Streamlit Dashboard Blueprint

> **Document Purpose:** Complete implementation guide for a Claude Code CLI-driven 100-bagger screening system. Each screening stage is a discrete Claude *skill* (slash-command). Results persist in a local DuckDB database. A Streamlit dashboard visualises the watchlist and a sample portfolio with position-level recommendations.

***

## Executive Summary

The system has three layers:

1. **Claude Code CLI Skills** — individual `/hunt-*` slash-commands that a human invokes on demand to run each pipeline stage. No scheduler, no automation daemon. The human decides when to rescreen.
2. **DuckDB persistence** — a single `100baggers.duckdb` file stores the full screening funnel, score history, qualitative LLM notes, and the sample portfolio with hold/sell recommendations.
3. **Streamlit dashboard** — a `dashboard.py` app reads directly from the DuckDB file and presents the watchlist funnel, individual stock detail, and the sample portfolio with action status.

The critical analytical framework (Mayer's 100-bagger criteria, critical review, data sources) from the previous report is unchanged and assumed as background. This document focuses entirely on the Claude Code + DuckDB + Streamlit implementation.

***

## Part 1: Project Structure

```
100baggers/
├── .claude/
│   └── commands/                    # Claude Code custom slash-commands (skills)
│       ├── hunt-universe.md         # /hunt-universe  — Stage 1 universe build
│       ├── hunt-score.md            # /hunt-score     — Stage 2 fundamental scoring
│       ├── hunt-roic.md             # /hunt-roic      — Stage 3 ROIC + avoidance
│       ├── hunt-moat.md             # /hunt-moat      — Stage 4 LLM moat scoring
│       ├── hunt-signals.md          # /hunt-signals   — Entry signal check
│       ├── hunt-monitor.md          # /hunt-monitor   — Weekly monitoring of watchlist
│       ├── hunt-portfolio.md        # /hunt-portfolio — Update portfolio positions
│       └── hunt-status.md          # /hunt-status    — Print pipeline summary
├── skills/                          # Python modules called by skills
│   ├── db.py                        # DuckDB schema + CRUD helpers
│   ├── universe.py                  # Stage 1: build + filter universe
│   ├── scorer.py                    # Stage 2: yfinance quantitative scoring
│   ├── roic.py                      # Stage 3: EDGAR XBRL ROIC + avoidance flags
│   ├── moat.py                      # Stage 4: Claude API 10-K moat scoring
│   ├── signals.py                   # Entry signal generation
│   ├── monitor.py                   # Monitoring checks + sell trigger evaluation
│   └── portfolio.py                 # Portfolio CRUD + recommendation engine
├── dashboard.py                     # Streamlit app
├── requirements.txt
├── 100baggers.duckdb                # Single-file database (gitignored)
└── CLAUDE.md                        # Project instructions read by Claude Code
```

***

## Part 2: DuckDB Schema

All state lives in a single `100baggers.duckdb` file. DuckDB is ideal here: zero-server, file-based, fast analytical queries on DataFrames, and directly readable by both Python scripts and Streamlit.[1]

```sql
-- -------------------------------------------------------
-- skills/schema.sql  (run once via db.py init_db())
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS universe (
    ticker          VARCHAR PRIMARY KEY,
    company_name    VARCHAR,
    market_cap      DOUBLE,
    sector          VARCHAR,
    industry        VARCHAR,
    country         VARCHAR,
    avg_volume      DOUBLE,
    revenue_ttm     DOUBLE,
    added_date      DATE DEFAULT current_date,
    stage           INTEGER DEFAULT 1,   -- highest stage reached
    status          VARCHAR DEFAULT 'active'  -- active | excluded | watchlist
);

CREATE TABLE IF NOT EXISTS scores (
    id              INTEGER PRIMARY KEY,
    ticker          VARCHAR REFERENCES universe(ticker),
    score_date      DATE DEFAULT current_date,
    -- Stage 2 metrics
    rev_cagr_3y     DOUBLE,
    gross_margin    DOUBLE,
    gm_trend        VARCHAR,   -- expanding | stable | contracting
    annual_dilution DOUBLE,
    trailing_pe     DOUBLE,
    price_fcf       DOUBLE,
    ev_ebit         DOUBLE,
    peg_ratio       DOUBLE,
    insider_pct     DOUBLE,
    fcf_positive    BOOLEAN,
    quant_score     INTEGER,   -- 0–14 points
    -- Stage 3 metrics
    roic_3y_median  DOUBLE,
    roe_adjusted    DOUBLE,
    roa             DOUBLE,
    debt_ebitda     DOUBLE,
    altman_z        DOUBLE,
    piotroski_f     INTEGER,
    asset_bloat_flag BOOLEAN,
    acq_flag        BOOLEAN,
    dilution_flag   BOOLEAN,
    roic_score      INTEGER,   -- 0–10 points
    -- Stage 4 LLM
    moat_total      INTEGER,   -- 0–18
    moat_durability INTEGER,   -- 0–5
    moat_notes      TEXT,
    founder_led     BOOLEAN,
    reinvest_runway VARCHAR,   -- narrow | medium | wide
    top_risks       TEXT,
    llm_score       INTEGER,   -- 0–10 points
    -- Aggregate
    total_score     INTEGER,   -- sum of all subscores
    stage_reached   INTEGER    -- 1, 2, 3, or 4
);

CREATE TABLE IF NOT EXISTS exclusions (
    ticker          VARCHAR PRIMARY KEY,
    reason          VARCHAR,
    excluded_date   DATE DEFAULT current_date
);

CREATE TABLE IF NOT EXISTS insider_events (
    id              INTEGER PRIMARY KEY,
    ticker          VARCHAR,
    event_date      DATE,
    insider_name    VARCHAR,
    role            VARCHAR,
    transaction     VARCHAR,   -- buy | sell
    shares          BIGINT,
    value_usd       DOUBLE,
    is_cluster_buy  BOOLEAN DEFAULT FALSE,
    signal_strength VARCHAR    -- high | medium | low
);

CREATE TABLE IF NOT EXISTS monitoring_log (
    id              INTEGER PRIMARY KEY,
    ticker          VARCHAR,
    check_date      DATE DEFAULT current_date,
    flags           TEXT,      -- JSON array of flag codes
    action          VARCHAR,   -- none | review | sell
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY,
    ticker          VARCHAR,
    alert_date      DATE DEFAULT current_date,
    alert_type      VARCHAR,   -- buy_signal | sell_signal | cluster_buy | red_flag
    message         TEXT,
    acknowledged    BOOLEAN DEFAULT FALSE
);

-- -------------------------------------------------------
-- PORTFOLIO TABLES
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS portfolio (
    id              INTEGER PRIMARY KEY,
    ticker          VARCHAR,
    company_name    VARCHAR,
    entry_date      DATE,
    entry_price     DOUBLE,
    shares          DOUBLE,
    position_pct    DOUBLE,    -- % of total portfolio at entry
    status          VARCHAR DEFAULT 'open',  -- open | closed | partial
    thesis          TEXT,      -- why you bought it
    target_horizon  VARCHAR,   -- e.g. "5–10 years", "3 years min"
    target_price    DOUBLE,    -- optional rough target
    close_date      DATE,
    close_price     DOUBLE,
    realized_return DOUBLE
);

CREATE TABLE IF NOT EXISTS portfolio_actions (
    id              INTEGER PRIMARY KEY,
    portfolio_id    INTEGER REFERENCES portfolio(id),
    action_date     DATE DEFAULT current_date,
    action_type     VARCHAR,   -- hold | add | trim | sell | review
    reason          TEXT,      -- e.g. "ROIC declining" | "cluster insider sell"
    horizon_months  INTEGER,   -- how many more months to hold (nullable)
    trigger_price   DOUBLE,    -- optional: "sell if it drops below X"
    created_by      VARCHAR DEFAULT 'manual',  -- manual | claude | monitor
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_date   DATE,
    ticker          VARCHAR,
    price           DOUBLE,
    market_value    DOUBLE,
    unrealized_pct  DOUBLE,
    roic_latest     DOUBLE,
    rev_growth_latest DOUBLE,
    moat_score      INTEGER,
    status_badge    VARCHAR    -- green | yellow | red
);
```

***

## Part 3: Claude Code Custom Skills (Slash-Commands)

Each skill is a Markdown file in `.claude/commands/`. Claude Code reads these as system-level instructions when the slash-command is invoked. Each skill calls its corresponding Python module in `skills/`.

### 3.1 CLAUDE.md — Project Context File

```markdown
# 100-Bagger Hunting System

## Project Overview
This project screens for potential 100-bagger stocks using the Mayer framework
(critically reviewed). All data is stored in `100baggers.duckdb`.
The Streamlit dashboard is `dashboard.py`.

## Key Conventions
- Always use `skills/db.py` for all database reads/writes — never write raw SQL in skill scripts
- API keys are in environment variables: ANTHROPIC_API_KEY
- SEC EDGAR requires User-Agent header: set in skills/config.py
- Rate limit EDGAR calls: max 10 req/sec, use time.sleep(0.11) between calls
- yfinance data quality is unreliable for microcaps — always log data quality warnings
- When in doubt, flag for manual review rather than auto-exclude

## Available Skills
| Command            | Purpose                                  | Typical Runtime |
|--------------------|------------------------------------------|-----------------|
| /hunt-universe     | Rebuild stock universe (Stage 1 filter)  | 5–10 min        |
| /hunt-score        | Run quantitative scoring (Stage 2)       | 15–30 min       |
| /hunt-roic         | ROIC + avoidance screening (Stage 3)     | 30–60 min       |
| /hunt-moat         | LLM moat scoring via Claude (Stage 4)    | 10–30 min       |
| /hunt-signals      | Check entry signals for watchlist        | 2–5 min         |
| /hunt-monitor      | Run weekly monitoring on open positions  | 5–15 min        |
| /hunt-portfolio    | Add/update portfolio positions           | interactive     |
| /hunt-status       | Print pipeline summary from DB           | <1 min          |

## Workflow
Run stages in order: universe → score → roic → moat → signals
For existing watchlist: run signals + monitor weekly
For portfolio: run monitor weekly, update portfolio manually or via /hunt-portfolio
```

***

### 3.2 `/hunt-universe` — Stage 1 Universe Builder

```markdown
<!-- .claude/commands/hunt-universe.md -->
# Skill: /hunt-universe

Build or refresh the stock universe with Stage 1 hard filters.

## What to do
1. Run `python skills/universe.py --rebuild` in the terminal
2. This script:
   - Fetches all US-listed stocks via yfinance EquityQuery or OpenBB finviz screener
   - Applies hard filters: market cap $50M–$1B, avg volume >$300K, revenue TTM >$30M,
     price >$1, country=US, excludes SIC codes for oil/gas/mining/banking
   - Writes results to `universe` table in 100baggers.duckdb
   - Prints summary: total fetched, total after filters, excluded by each criterion
3. Report back the summary table to the user
4. Ask: "Run Stage 2 scoring now? (/hunt-score)"

## Expected output
A table like:
| Step | Count |
|------|-------|
| Raw universe | ~8,000 |
| After market cap filter | ~2,400 |
| After volume filter | ~1,800 |
| After revenue filter | ~1,200 |
| After country/SIC filter | ~800 |
| Final Stage 1 universe | ~600–800 |
```

**`skills/universe.py` key logic:**

```python
import yfinance as yf
import duckdb
from skills.db import get_conn

EXCLUDED_SIC = [
    "1311", "2911",  # oil & gas extraction, petroleum refining
    "1040", "1090",  # gold/silver mining, metal mining
    "6020", "6022",  # banks
    "6311", "6321",  # insurance
]

def build_universe(min_cap=50e6, max_cap=1e9, min_vol=300_000, min_rev=30e6):
    # Use yfinance EquityQuery for server-side pre-filter
    query = yf.EquityQuery(
        "and",
        [
            yf.EquityQuery("gt", ["marketcap", min_cap]),
            yf.EquityQuery("lt", ["marketcap", max_cap]),
            yf.EquityQuery("gt", ["avgdailyvol3month", min_vol]),
            yf.EquityQuery("eq", ["region", "us"]),
        ]
    )
    result = yf.screen(query, sortField="marketcap", sortAsc=True, size=250)
    # ... paginate, deduplicate, apply SIC filter, write to DuckDB
```

***

### 3.3 `/hunt-score` — Stage 2 Fundamental Scoring

```markdown
<!-- .claude/commands/hunt-score.md -->
# Skill: /hunt-score

Run quantitative fundamental scoring on the Stage 1 universe.

## What to do
1. Read all tickers from `universe` where `stage >= 1` and `status = 'active'`
2. Run `python skills/scorer.py --batch` — processes in batches of 50 to respect rate limits
3. For each ticker, calculate and store in `scores` table:
   - Revenue CAGR (3yr), gross margin + trend, annual dilution, P/E, P/FCF, PEG,
     insider %, FCF sign — all from yfinance
   - Compute quant_score (0–14) per scoring rubric
   - Flag auto-exclusions (dilution >5%, negative FCF for 2+ years)
4. Update `universe.stage = 2` for stocks scoring ≥ 8/14
5. Print a score distribution histogram (text-based, ASCII) and top 30 by quant_score
6. Ask: "Run Stage 3 ROIC screen now? (/hunt-roic)"

## Scoring rubric (quant_score 0–14)
| Metric              | 2pts         | 1pt        | 0pts      |
|---------------------|-------------|------------|-----------|
| Revenue CAGR (3yr)  | >15%        | 8–15%      | <8%       |
| Gross margin trend  | Expanding   | Stable     | Contracting|
| Annual dilution     | <2%         | 2–5%       | >5%       |
| Trailing P/E        | <15         | 15–25      | >25       |
| FCF positive+growing| Yes         | Yes, flat  | No        |
| Insider ownership   | >15%        | 5–15%      | <5%       |
| Market cap          | $50–300M    | $300–800M  | else      |

## Auto-exclusion rules
- Annual dilution > 5% → exclude with reason "CHRONIC_DILUTER"
- Negative FCF for 2+ consecutive years → exclude with reason "CASH_BURN"
- Write exclusions to `exclusions` table
```

***

### 3.4 `/hunt-roic` — Stage 3 ROIC + Avoidance

```markdown
<!-- .claude/commands/hunt-roic.md -->
# Skill: /hunt-roic

Calculate ROIC from SEC EDGAR XBRL data and apply avoidance rules.

## What to do
1. Read tickers from `universe` where `stage >= 2` and `status = 'active'`
2. Run `python skills/roic.py --batch`
3. For each ticker:
   a. Fetch XBRL company facts from https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json
      (no API key required — use email as User-Agent header)
   b. Parse: Revenues, OperatingIncomeLoss, LongTermDebt, StockholdersEquity,
      CashAndCashEquivalentsAtCarryingValue, CommonStockSharesOutstanding,
      Assets, GoodwillAndIntangibleAssetsDisclosureAbstract
   c. Calculate:
      - ROIC = NOPAT / Invested Capital (3-year median)
      - ROE = Net Income / Avg Equity (DuPont components)
      - Debt/EBITDA ratio
      - Asset CAGR vs EBITDA CAGR (3yr) → flag if assets grow faster
      - Goodwill / Total Assets ratio
      - Altman Z-Score (use public-company formula)
      - Piotroski F-Score (9-point)
   d. Store all in `scores` table
4. Apply avoidance auto-exclusions:
   - Asset CAGR > EBITDA CAGR → flag "ASSET_BLOAT"
   - Goodwill/Assets > 40% → flag "ACQ_DEPENDENT"
   - Debt/EBITDA > 4x → flag "OVER_LEVERAGED"
   - Altman Z < 1.81 → flag "DISTRESS" → auto-exclude
   - Country indicators pointing to VIE/Cayman → flag "JURISDICTION_RISK"
5. Set `stage = 3` for tickers passing with roic_score ≥ 6/10
6. Print top 20 by ROIC with Piotroski and Altman Z columns

## ROIC Formula
NOPAT = OperatingIncome × (1 - 0.21)
InvestedCapital = TotalEquity + TotalDebt - CashAndEquivalents
ROIC = NOPAT / InvestedCapital

## EDGAR rate limit
Always sleep 0.11 seconds between CIK requests.
```

***

### 3.5 `/hunt-moat` — Stage 4 LLM Moat Scoring

```markdown
<!-- .claude/commands/hunt-moat.md -->
# Skill: /hunt-moat

Score the economic moat of Stage 3 candidates using Claude and SEC 10-K filings.

## What to do
1. Read tickers from `universe` where `stage >= 3` and `status = 'active'`
   (typically 30–80 stocks — this is the expensive LLM step)
2. For each ticker:
   a. Fetch the most recent 10-K "Item 1 – Business" section via edgartools:
      `Company(ticker).get_filings(form="10-K")[0]`
   b. Extract Business section text (truncate to 8,000 chars if needed)
   c. Call Claude API with structured moat scoring prompt (see below)
   d. Parse JSON response → store in `scores` table:
      - moat_total (0–18), moat_durability (0–5)
      - founder_led (bool), reinvest_runway, top_risks (text)
      - llm_score (0–10, derived from moat scores)
3. Set `stage = 4` for tickers with moat_total ≥ 6 AND moat_durability ≥ 3
4. Print the resulting Watchlist B (stage 4 tickers) sorted by total_score

## Claude Prompt Template
```
You are a fundamental equity analyst specialising in economic moat analysis.
Analyse this 10-K Business section for {ticker} ({company_name}).

Return ONLY valid JSON matching this schema:
{
  "moat_scores": {
    "distribution": 0-3,
    "brand": 0-3,
    "network_effects": 0-3,
    "regulatory": 0-3,
    "switching_costs": 0-3,
    "cost_structure": 0-3
  },
  "moat_total": <sum 0-18>,
  "moat_durability": 0-5,
  "founder_led": true/false/null,
  "reinvest_runway": "narrow" | "medium" | "wide",
  "top_risks": ["risk1", "risk2", "risk3"],
  "gross_margin_proxy": "pricing power evidence from text",
  "summary": "2-sentence moat thesis"
}

TEXT:
{business_section_text}
```

## Cost estimate
~1,500 tokens per stock. At Claude claude-haiku-4-5 pricing ($0.25/M input tokens):
30 stocks ≈ $0.01 total. Use claude-haiku-4-5 for moat scoring unless deep analysis needed.
```

***

### 3.6 `/hunt-signals` — Entry Signal Checker

```markdown
<!-- .claude/commands/hunt-signals.md -->
# Skill: /hunt-signals

Check entry signals for all Watchlist B (stage 4) tickers.

## What to do
1. Read all tickers from `universe` where `stage = 4` and `status IN ('active', 'watchlist')`
2. Run `python skills/signals.py` which for each ticker:
   a. Fetch current price, 52-week high/low, 200-day SMA via yfinance
   b. Fetch recent insider transactions via edgartools Form 4
      → detect cluster buys: ≥3 insiders buying in open market in last 60 days
      → detect CEO/CFO purchases > $25K
   c. Check valuation gate: P/FCF < 20 OR EV/EBIT < 15 OR P/E < 20
   d. Check price zone: within 0–25% of 52-week low (optional filter)
   e. Check Piotroski F-Score ≥ 6 (from scores table, refreshed if >30 days old)
   f. Compute signal_strength: "high" | "medium" | "low"
3. Write all signals to `alerts` table (unacknowledged)
4. Write insider events to `insider_events` table
5. Print:
   - 🟢 HIGH SIGNAL: tickers with cluster buy + valuation gate open + price in buy zone
   - 🟡 MEDIUM SIGNAL: valuation gate open, no cluster buy
   - 🔵 WATCH: on watchlist, no current signal
   - Any new unacknowledged alerts

## Signal strength definition
| Condition                                          | Strength |
|----------------------------------------------------|----------|
| Cluster buy (3+ insiders) + valuation gate open    | HIGH     |
| CEO/CFO buy >$25K + valuation gate open            | HIGH     |
| Valuation gate open + Piotroski ≥ 7                | MEDIUM   |
| Only valuation gate open                           | LOW      |
```

***

### 3.7 `/hunt-monitor` — Watchlist & Portfolio Monitoring

```markdown
<!-- .claude/commands/hunt-monitor.md -->
# Skill: /hunt-monitor

Run monitoring checks on all open portfolio positions and watchlist stocks.

## What to do
1. Read all open positions from `portfolio` where `status = 'open'`
2. For each position, run `python skills/monitor.py --ticker {ticker}`:
   a. Refresh key metrics: revenue YoY growth, gross margin, ROIC (from last 10-Q via XBRL)
   b. Check sell triggers (see table below)
   c. Fetch recent 8-K filings from last 30 days → send to Claude for red flag extraction:
      Look for: restatement, going-concern, key-man departure, SEC investigation, fraud
   d. Check insider selling: any cluster selling (3+ insiders, >$100K total, 60 days)
   e. Write result to `monitoring_log` (flags list + recommended action)
   f. Write any triggered alerts to `alerts` table
3. Update `portfolio_snapshots` table with today's prices and status_badge
4. Print monitoring summary:
   - Positions with 🔴 SELL flags
   - Positions with 🟡 REVIEW flags
   - Positions with 🟢 NO ISSUES
   - Any unacknowledged alerts

## Sell trigger table
| Trigger                                        | Action  | Auto-flag code        |
|------------------------------------------------|---------|-----------------------|
| Revenue YoY growth < 0% for 2+ quarters       | REVIEW  | REV_DECLINE           |
| ROIC < 10% for 2 consecutive years             | SELL    | ROIC_DETERIORATION    |
| Gross margin compressed >10pp over 2 years    | REVIEW  | MARGIN_COMPRESSION    |
| CEO/Founder departure (8-K)                   | REVIEW  | CEO_DEPARTURE         |
| Cluster insider selling (3+ insiders, >$100K) | REVIEW  | CLUSTER_SELL          |
| P/E expanded >50x (froth)                     | TRIM    | VALUATION_EXTREME     |
| Accounting restatement (8-K)                  | SELL    | RESTATEMENT           |
| Going concern opinion (10-K/Q)                | SELL    | GOING_CONCERN         |
| Annual dilution >10% in single year           | REVIEW  | DILUTION_SPIKE        |
```

***

### 3.8 `/hunt-portfolio` — Portfolio Manager

```markdown
<!-- .claude/commands/hunt-portfolio.md -->
# Skill: /hunt-portfolio

Interactively add, update, or review portfolio positions.

## What to do
Read the user's intent from $ARGUMENTS. Supported sub-commands:
- `/hunt-portfolio add {ticker}` — add a new position interactively
- `/hunt-portfolio update {ticker}` — update action/recommendation for a position
- `/hunt-portfolio close {ticker}` — mark a position as closed (record exit price)
- `/hunt-portfolio suggest {ticker}` — generate a Claude recommendation for the position
- `/hunt-portfolio review` — print all positions with latest actions

### Sub-command: add {ticker}
Ask the user for:
1. Entry price and number of shares
2. Position size (% of portfolio)
3. Investment thesis (free text — why are you buying?)
4. Target holding horizon (e.g., "3–7 years", "minimum 5 years")
5. Optional target price
Write to `portfolio` table. Generate a default HOLD action in `portfolio_actions`.

### Sub-command: update {ticker}
Show current position data. Ask user to choose action:
- HOLD [horizon_months] [notes]
- ADD [reason]
- TRIM [reason] [trigger_price]
- SELL [reason]
- REVIEW [flag]
Write to `portfolio_actions`.

### Sub-command: suggest {ticker}
1. Read position data, latest scores, monitoring_log, alerts for the ticker
2. Fetch latest financials via yfinance (revenue growth, ROIC trend, P/FCF)
3. Build a prompt for Claude containing all this data
4. Ask Claude: "Given this position data, what is the recommended action?
   Output JSON: {action, horizon_months, reason, confidence: high/medium/low, risks}"
5. Display recommendation prominently
6. Ask user to confirm → if confirmed, write to `portfolio_actions` with created_by='claude'

### Sub-command: close {ticker}
Ask for exit date and exit price. Calculate realized return.
Update portfolio.status = 'closed', record close_price, realized_return.
Write a SELL action with the user-provided reason.

### Sub-command: review
Print a table of all positions:
| Ticker | Entry | Current | Return% | Status | Last Action | Horizon | Alert |
```

***

### 3.9 `/hunt-status` — Quick Pipeline Summary

```markdown
<!-- .claude/commands/hunt-status.md -->
# Skill: /hunt-status

Print a concise pipeline summary from the database. No external API calls.

## What to do
Run `python skills/db.py --status` which queries the DB and prints:

1. Pipeline funnel:
   Stage 1 universe: N stocks
   Stage 2 survivors: N stocks (quant score ≥ 8)
   Stage 3 survivors: N stocks (ROIC score ≥ 6)
   Stage 4 watchlist: N stocks (moat score passed)
   Excluded total: N (breakdown by reason)

2. Latest alerts (unacknowledged, last 14 days):
   🟢 Buy signals: N
   🔴 Sell/review triggers: N

3. Portfolio summary:
   Open positions: N
   Best performer: {ticker} (+X%)
   Worst performer: {ticker} (-X%)
   Positions with active alerts: N

4. Data freshness:
   Universe last rebuilt: {date}
   Scores last updated: {date}
   Last monitoring run: {date}
```

***

## Part 4: Streamlit Dashboard

### 4.1 Page Structure

```python
# dashboard.py — top-level navigation
import streamlit as st

st.set_page_config(
    page_title="100-Bagger Hunter",
    page_icon="💎",
    layout="wide"
)

PAGES = {
    "🔭 Pipeline Overview": "pages/pipeline.py",
    "📋 Watchlist": "pages/watchlist.py",
    "🔍 Stock Detail": "pages/stock_detail.py",
    "💼 Portfolio": "pages/portfolio.py",
    "🚨 Alerts": "pages/alerts.py",
    "📊 Score History": "pages/score_history.py",
}

# Run: streamlit run dashboard.py
```

***

### 4.2 Page 1: Pipeline Overview

```python
# pages/pipeline.py
import streamlit as st
import duckdb
import pandas as pd
import plotly.graph_objects as go

DB = "100baggers.duckdb"

def show():
    st.title("Pipeline Overview")

    con = duckdb.connect(DB, read_only=True)

    # Funnel counts
    counts = con.execute("""
        SELECT
            COUNT(*) FILTER (WHERE stage >= 1) as stage1,
            COUNT(*) FILTER (WHERE stage >= 2) as stage2,
            COUNT(*) FILTER (WHERE stage >= 3) as stage3,
            COUNT(*) FILTER (WHERE stage >= 4) as stage4
        FROM universe WHERE status != 'excluded'
    """).fetchone()

    excl = con.execute("SELECT reason, COUNT(*) as n FROM exclusions GROUP BY reason ORDER BY n DESC").df()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Stage 1 Universe", counts[0])
    col2.metric("Stage 2 Quality", counts[1], delta=f"{counts[1]-counts[0]}")
    col3.metric("Stage 3 ROIC", counts[2])
    col4.metric("Stage 4 Watchlist B", counts[3])

    # Funnel chart (Plotly)
    fig = go.Figure(go.Funnel(
        y=["Universe", "Quality Filter", "ROIC Screen", "Watchlist B"],
        x=list(counts),
        textinfo="value+percent initial"
    ))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Exclusion Reasons")
    st.dataframe(excl, use_container_width=True)

    # Data freshness
    freshness = con.execute("""
        SELECT MAX(score_date) as last_scored FROM scores
    """).fetchone()
    st.caption(f"Scores last updated: {freshness[0] or 'never'}")

    con.close()
```

***

### 4.3 Page 2: Watchlist

```python
# pages/watchlist.py
import streamlit as st
import duckdb
import pandas as pd

DB = "100baggers.duckdb"

def show():
    st.title("Watchlist B — Qualified Candidates")

    con = duckdb.connect(DB, read_only=True)

    df = con.execute("""
        SELECT
            u.ticker,
            u.company_name,
            u.market_cap / 1e6 as mkt_cap_m,
            u.sector,
            s.total_score,
            s.rev_cagr_3y * 100 as rev_cagr_pct,
            s.gross_margin * 100 as gross_margin_pct,
            s.gm_trend,
            s.roic_3y_median * 100 as roic_pct,
            s.trailing_pe,
            s.price_fcf,
            s.insider_pct * 100 as insider_pct,
            s.moat_total,
            s.moat_durability,
            s.founder_led,
            s.reinvest_runway,
            s.piotroski_f,
            s.altman_z,
            -- Signal badge
            CASE
                WHEN EXISTS (SELECT 1 FROM alerts a
                    WHERE a.ticker = u.ticker
                    AND a.alert_type = 'buy_signal'
                    AND a.acknowledged = FALSE) THEN '🟢 BUY SIGNAL'
                WHEN EXISTS (SELECT 1 FROM alerts a
                    WHERE a.ticker = u.ticker
                    AND a.alert_type IN ('sell_signal', 'red_flag')
                    AND a.acknowledged = FALSE) THEN '🔴 FLAG'
                ELSE '🔵 WATCH'
            END as signal
        FROM universe u
        JOIN scores s ON u.ticker = s.ticker
            AND s.score_date = (SELECT MAX(score_date) FROM scores WHERE ticker = u.ticker)
        WHERE u.stage >= 4 AND u.status = 'active'
        ORDER BY s.total_score DESC
    """).df()

    con.close()

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        min_roic = st.slider("Min ROIC %", 0, 50, 15)
    with col2:
        signal_filter = st.multiselect("Signal", ["🟢 BUY SIGNAL", "🔴 FLAG", "🔵 WATCH"],
                                        default=["🟢 BUY SIGNAL", "🔵 WATCH"])
    with col3:
        sector_filter = st.multiselect("Sector", df["sector"].unique().tolist())

    filtered = df[df["roic_pct"] >= min_roic]
    if signal_filter:
        filtered = filtered[filtered["signal"].isin(signal_filter)]
    if sector_filter:
        filtered = filtered[filtered["sector"].isin(sector_filter)]

    # Colour ROIC column
    def colour_roic(val):
        if val >= 20: return "background-color: #d4edda"
        elif val >= 15: return "background-color: #fff3cd"
        return "background-color: #f8d7da"

    st.dataframe(
        filtered.style.applymap(colour_roic, subset=["roic_pct"]),
        use_container_width=True,
        height=600
    )

    st.download_button(
        "Export CSV",
        data=filtered.to_csv(index=False),
        file_name="watchlist_b.csv",
        mime="text/csv"
    )
```

***

### 4.4 Page 3: Stock Detail

```python
# pages/stock_detail.py
import streamlit as st
import duckdb
import yfinance as yf
import plotly.express as px

DB = "100baggers.duckdb"

def show():
    st.title("Stock Deep Dive")

    con = duckdb.connect(DB, read_only=True)
    tickers = con.execute("SELECT ticker FROM universe WHERE stage >= 3 ORDER BY ticker").df()
    con.close()

    ticker = st.selectbox("Select ticker", tickers["ticker"].tolist())

    if ticker:
        col1, col2 = st.columns([2, 1])

        with col1:
            # Price chart
            t = yf.Ticker(ticker)
            hist = t.history(period="5y")
            fig = px.line(hist, y="Close", title=f"{ticker} — 5-Year Price")
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            # Key metrics from DB
            con = duckdb.connect(DB, read_only=True)
            s = con.execute("""
                SELECT * FROM scores WHERE ticker = ? ORDER BY score_date DESC LIMIT 1
            """, [ticker]).df()
            u = con.execute("SELECT * FROM universe WHERE ticker = ?", [ticker]).df()

            st.metric("ROIC (3yr median)", f"{s['roic_3y_median'].iloc[0]*100:.1f}%")
            st.metric("Revenue CAGR (3yr)", f"{s['rev_cagr_3y'].iloc[0]*100:.1f}%")
            st.metric("Gross Margin", f"{s['gross_margin'].iloc[0]*100:.1f}%")
            st.metric("Moat Score", f"{s['moat_total'].iloc[0]}/18 (durability {s['moat_durability'].iloc[0]}/5)")
            st.metric("Piotroski F", f"{s['piotroski_f'].iloc[0]}/9")
            st.metric("Insider %", f"{s['insider_pct'].iloc[0]*100:.1f}%")
            st.metric("Total Score", f"{s['total_score'].iloc[0]}/34")
            con.close()

        # LLM moat notes
        con = duckdb.connect(DB, read_only=True)
        notes = con.execute("SELECT moat_notes, top_risks, reinvest_runway FROM scores WHERE ticker=? ORDER BY score_date DESC LIMIT 1", [ticker]).fetchone()
        con.close()

        if notes and notes[0]:
            st.subheader("Moat Analysis (Claude)")
            st.info(notes[0])
            st.subheader("Top Risks")
            for r in (notes[1] or "").split("\n"):
                if r.strip(): st.write(f"• {r.strip()}")
            st.write(f"**Reinvestment Runway:** {notes[2]}")

        # Insider events
        con = duckdb.connect(DB, read_only=True)
        ins = con.execute("""
            SELECT event_date, insider_name, role, transaction, shares, value_usd, signal_strength
            FROM insider_events WHERE ticker = ? ORDER BY event_date DESC LIMIT 20
        """, [ticker]).df()
        con.close()

        if not ins.empty:
            st.subheader("Insider Transactions")
            st.dataframe(ins, use_container_width=True)

        # Monitoring log
        con = duckdb.connect(DB, read_only=True)
        log = con.execute("""
            SELECT check_date, flags, action, notes
            FROM monitoring_log WHERE ticker = ? ORDER BY check_date DESC LIMIT 10
        """, [ticker]).df()
        con.close()

        if not log.empty:
            st.subheader("Monitoring History")
            st.dataframe(log, use_container_width=True)
```

***

### 4.5 Page 4: Portfolio — The Core New Feature

```python
# pages/portfolio.py
import streamlit as st
import duckdb
import yfinance as yf
import pandas as pd
import plotly.express as px
from datetime import date

DB = "100baggers.duckdb"

def get_current_price(ticker: str) -> float:
    try:
        return yf.Ticker(ticker).fast_info["lastPrice"]
    except:
        return None

def show():
    st.title("💼 Sample Portfolio")
    st.caption("Track your 100-bagger candidates with position-level hold/sell recommendations.")

    con = duckdb.connect(DB)

    tab1, tab2, tab3 = st.tabs(["📊 Portfolio Overview", "🎯 Position Actions", "➕ Add Position"])

    # -------------------------------------------------------
    # TAB 1: PORTFOLIO OVERVIEW
    # -------------------------------------------------------
    with tab1:
        positions = con.execute("""
            SELECT
                p.id,
                p.ticker,
                p.company_name,
                p.entry_date,
                p.entry_price,
                p.shares,
                p.position_pct,
                p.thesis,
                p.target_horizon,
                p.target_price,
                p.status,
                -- Latest action
                pa.action_type as latest_action,
                pa.reason as action_reason,
                pa.horizon_months,
                pa.trigger_price,
                pa.action_date,
                pa.created_by,
                pa.notes as action_notes,
                -- Score data
                s.roic_3y_median,
                s.rev_cagr_3y,
                s.moat_total,
                s.moat_durability
            FROM portfolio p
            LEFT JOIN portfolio_actions pa ON pa.portfolio_id = p.id
                AND pa.action_date = (SELECT MAX(action_date) FROM portfolio_actions WHERE portfolio_id = p.id)
            LEFT JOIN scores s ON s.ticker = p.ticker
                AND s.score_date = (SELECT MAX(score_date) FROM scores WHERE ticker = p.ticker)
            WHERE p.status = 'open'
            ORDER BY p.entry_date
        """).df()

        if positions.empty:
            st.info("No open positions yet. Use the 'Add Position' tab or run `/hunt-portfolio add {ticker}` in Claude Code.")
        else:
            # Enrich with live prices
            rows = []
            for _, row in positions.iterrows():
                price = get_current_price(row["ticker"])
                cost_basis = row["entry_price"] * row["shares"]
                mkt_value = (price * row["shares"]) if price else None
                ret_pct = ((price / row["entry_price"]) - 1) * 100 if price else None
                rows.append({
                    **row.to_dict(),
                    "current_price": price,
                    "cost_basis": cost_basis,
                    "market_value": mkt_value,
                    "return_pct": ret_pct,
                })

            df = pd.DataFrame(rows)

            # Summary metrics
            total_cost = df["cost_basis"].sum()
            total_value = df["market_value"].sum()
            total_return = ((total_value / total_cost) - 1) * 100 if total_cost else 0

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total Cost Basis", f"${total_cost:,.0f}")
            m2.metric("Market Value", f"${total_value:,.0f}")
            m3.metric("Total Return", f"{total_return:.1f}%")
            m4.metric("Positions", len(df))

            # Action badge colouring
            def action_colour(action):
                colours = {
                    "hold": "🟢 HOLD",
                    "add": "🔵 ADD",
                    "trim": "🟡 TRIM",
                    "sell": "🔴 SELL",
                    "review": "🟠 REVIEW",
                }
                return colours.get(str(action).lower(), action)

            df["action_badge"] = df["latest_action"].apply(action_colour)

            # Main portfolio table
            display_cols = [
                "ticker", "company_name", "entry_date", "entry_price",
                "current_price", "return_pct", "position_pct",
                "target_horizon", "action_badge", "action_reason",
                "horizon_months", "action_date", "roic_3y_median", "moat_total"
            ]

            def colour_return(val):
                try:
                    v = float(val)
                    if v > 50: return "background-color: #155724; color: white"
                    if v > 0: return "background-color: #d4edda"
                    if v > -20: return "background-color: #fff3cd"
                    return "background-color: #f8d7da"
                except: return ""

            st.dataframe(
                df[display_cols].style.applymap(colour_return, subset=["return_pct"]),
                use_container_width=True,
                height=500,
                column_config={
                    "return_pct": st.column_config.NumberColumn("Return %", format="%.1f%%"),
                    "roic_3y_median": st.column_config.NumberColumn("ROIC", format="%.1%"),
                    "entry_price": st.column_config.NumberColumn("Entry $", format="$%.2f"),
                    "current_price": st.column_config.NumberColumn("Current $", format="$%.2f"),
                }
            )

            # Return distribution chart
            fig = px.bar(
                df.sort_values("return_pct", ascending=False),
                x="ticker", y="return_pct",
                color="return_pct",
                color_continuous_scale=["#dc3545", "#ffc107", "#28a745"],
                title="Position Returns (%)",
                labels={"return_pct": "Return %"}
            )
            st.plotly_chart(fig, use_container_width=True)

            # Position action history drill-down
            st.subheader("Action History by Position")
            sel_ticker = st.selectbox("Select position", df["ticker"].tolist(), key="hist_sel")
            if sel_ticker:
                pid = df[df["ticker"] == sel_ticker]["id"].iloc[0]
                hist = con.execute("""
                    SELECT action_date, action_type, reason, horizon_months,
                           trigger_price, created_by, notes
                    FROM portfolio_actions
                    WHERE portfolio_id = ?
                    ORDER BY action_date DESC
                """, [int(pid)]).df()
                st.dataframe(hist, use_container_width=True)

    # -------------------------------------------------------
    # TAB 2: POSITION ACTIONS — Update recommendation
    # -------------------------------------------------------
    with tab2:
        st.subheader("Update Position Recommendation")
        st.caption("Record your latest thinking on each position. These are stored and shown in the overview.")

        tickers_open = con.execute("SELECT ticker, company_name FROM portfolio WHERE status='open' ORDER BY ticker").df()

        if tickers_open.empty:
            st.info("No open positions.")
        else:
            sel = st.selectbox("Position", tickers_open["ticker"].tolist(), key="action_sel",
                               format_func=lambda t: f"{t} — {tickers_open[tickers_open['ticker']==t]['company_name'].iloc[0]}")

            pid = con.execute("SELECT id FROM portfolio WHERE ticker=? AND status='open'", [sel]).fetchone()

            if pid:
                col1, col2 = st.columns(2)
                with col1:
                    action = st.selectbox("Action", ["hold", "add", "trim", "sell", "review"])
                    horizon = st.number_input("Horizon (months)", min_value=0, max_value=360, value=24,
                                             help="How many more months to hold? 0 = indefinite")
                    trigger = st.number_input("Trigger price (optional)", min_value=0.0, value=0.0,
                                             help="e.g. sell if drops below $X, or trim if reaches $Y")
                with col2:
                    reason = st.text_area("Reason", height=80,
                                         placeholder="e.g. ROIC trending up, moat intact, hold for 2+ years")
                    notes = st.text_area("Notes", height=80,
                                        placeholder="Any additional context, risks, catalysts")

                if st.button("💾 Save Action", type="primary"):
                    con.execute("""
                        INSERT INTO portfolio_actions
                            (portfolio_id, action_date, action_type, reason, horizon_months,
                             trigger_price, created_by, notes)
                        VALUES (?, ?, ?, ?, ?, ?, 'manual', ?)
                    """, [pid[0], date.today(), action,
                          reason, int(horizon) if horizon > 0 else None,
                          float(trigger) if trigger > 0 else None, notes])
                    st.success(f"✅ Saved {action.upper()} action for {sel}")
                    st.rerun()

    # -------------------------------------------------------
    # TAB 3: ADD NEW POSITION
    # -------------------------------------------------------
    with tab3:
        st.subheader("Add New Position")
        st.caption("Record a new holding. Ticker must exist in the universe table (run /hunt-score first).")

        with st.form("add_position"):
            c1, c2 = st.columns(2)
            with c1:
                new_ticker = st.text_input("Ticker", placeholder="e.g. MELI")
                company_name = st.text_input("Company Name")
                entry_date = st.date_input("Entry Date", value=date.today())
                entry_price = st.number_input("Entry Price ($)", min_value=0.01)
            with c2:
                shares = st.number_input("Shares", min_value=0.001)
                pos_pct = st.number_input("Position Size (% of portfolio)", min_value=0.1, max_value=100.0)
                target_horizon = st.text_input("Target Horizon", placeholder="e.g. 5–10 years")
                target_price = st.number_input("Target Price (optional)", min_value=0.0)

            thesis = st.text_area("Investment Thesis", height=120,
                                  placeholder="Why are you buying this? What moat, growth driver, catalyst?")
            submitted = st.form_submit_button("Add Position", type="primary")

        if submitted and new_ticker and entry_price > 0 and shares > 0:
            con.execute("""
                INSERT INTO portfolio
                    (ticker, company_name, entry_date, entry_price, shares, position_pct,
                     thesis, target_horizon, target_price, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """, [new_ticker.upper(), company_name, entry_date, entry_price,
                  shares, pos_pct, thesis, target_horizon,
                  target_price if target_price > 0 else None])

            pid = con.execute("SELECT id FROM portfolio WHERE ticker=? ORDER BY id DESC LIMIT 1",
                              [new_ticker.upper()]).fetchone()[0]
            con.execute("""
                INSERT INTO portfolio_actions (portfolio_id, action_date, action_type, reason, created_by)
                VALUES (?, ?, 'hold', 'Initial position', 'manual')
            """, [pid, date.today()])

            st.success(f"✅ Added {new_ticker.upper()} to portfolio")
            st.rerun()

    con.close()
```

***

### 4.6 Page 5: Alerts Feed

```python
# pages/alerts.py
import streamlit as st
import duckdb
import pandas as pd

DB = "100baggers.duckdb"

def show():
    st.title("🚨 Alerts")

    con = duckdb.connect(DB)

    unacked = con.execute("""
        SELECT a.id, a.ticker, u.company_name, a.alert_date, a.alert_type, a.message
        FROM alerts a
        LEFT JOIN universe u ON u.ticker = a.ticker
        WHERE a.acknowledged = FALSE
        ORDER BY a.alert_date DESC
    """).df()

    if unacked.empty:
        st.success("No unacknowledged alerts.")
    else:
        st.warning(f"{len(unacked)} unacknowledged alert(s)")

        for _, row in unacked.iterrows():
            icon = "🟢" if "buy" in row["alert_type"] else "🔴"
            with st.expander(f"{icon} [{row['alert_date']}] {row['ticker']} — {row['alert_type'].upper()}"):
                st.write(f"**Company:** {row['company_name']}")
                st.write(f"**Message:** {row['message']}")
                if st.button(f"✅ Acknowledge", key=f"ack_{row['id']}"):
                    con.execute("UPDATE alerts SET acknowledged=TRUE WHERE id=?", [int(row["id"])])
                    st.rerun()

    st.subheader("Alert History (last 30 days)")
    history = con.execute("""
        SELECT a.alert_date, a.ticker, a.alert_type, a.message, a.acknowledged
        FROM alerts a
        WHERE a.alert_date >= current_date - INTERVAL 30 DAYS
        ORDER BY a.alert_date DESC
    """).df()
    st.dataframe(history, use_container_width=True)
    con.close()
```

***

## Part 5: Requirements & Setup

### 5.1 `requirements.txt`

```text
# Data
yfinance>=0.2.40
edgartools>=2.20
openbb>=4.3
openbb-finviz>=1.2
insidertracker>=0.1.2
duckdb>=0.10
requests>=2.31
pandas>=2.1
numpy>=1.26

# LLM
anthropic>=0.25

# Dashboard
streamlit>=1.35
plotly>=5.20

# Utilities
python-dotenv>=1.0
pyyaml>=6.0
```

### 5.2 Initial Setup

```bash
# 1. Install
pip install -r requirements.txt

# 2. Set environment variables
export ANTHROPIC_API_KEY="sk-ant-..."
export SEC_USER_AGENT="your@email.com"   # required by SEC EDGAR

# 3. Initialise database
python -c "from skills.db import init_db; init_db()"

# 4. Run dashboard
streamlit run dashboard.py

# 5. In Claude Code CLI — run first pipeline pass:
# /hunt-universe
# /hunt-score
# /hunt-roic
# /hunt-moat
# /hunt-signals
```

### 5.3 `skills/db.py` — Core Database Helper

```python
import duckdb
import os

DB_PATH = os.getenv("DUCKDB_PATH", "100baggers.duckdb")

def get_conn(read_only=False):
    return duckdb.connect(DB_PATH, read_only=read_only)

def init_db():
    con = get_conn()
    with open("skills/schema.sql") as f:
        con.execute(f.read())
    con.close()
    print(f"Database initialised at {DB_PATH}")

def get_status_summary() -> str:
    con = get_conn(read_only=True)
    funnel = con.execute("""
        SELECT
            COUNT(*) FILTER (WHERE stage >= 1) as s1,
            COUNT(*) FILTER (WHERE stage >= 2) as s2,
            COUNT(*) FILTER (WHERE stage >= 3) as s3,
            COUNT(*) FILTER (WHERE stage >= 4) as s4
        FROM universe WHERE status != 'excluded'
    """).fetchone()
    alerts = con.execute("SELECT COUNT(*) FROM alerts WHERE acknowledged=FALSE").fetchone()[0]
    portfolio = con.execute("SELECT COUNT(*) FROM portfolio WHERE status='open'").fetchone()[0]
    last_score = con.execute("SELECT MAX(score_date) FROM scores").fetchone()[0]
    con.close()

    return f"""
Pipeline Funnel:
  Stage 1 Universe:    {funnel[0]:>5}
  Stage 2 Quality:     {funnel[1]:>5}
  Stage 3 ROIC:        {funnel[2]:>5}
  Stage 4 Watchlist B: {funnel[3]:>5}

Unacknowledged alerts: {alerts}
Open portfolio positions: {portfolio}
Scores last updated: {last_score or 'never'}
"""
```

***

## Part 6: Typical Weekly Workflow (Human-in-the-Loop)

```
Monday morning (10 min):
  → /hunt-signals          # check for new entry signals on watchlist
  → /hunt-monitor          # check open portfolio positions for red flags
  → Review alerts in dashboard (localhost:8501)
  → Acknowledge or act on flagged items

Monthly (30–60 min):
  → /hunt-score            # refresh quantitative scores (new earnings data)
  → /hunt-roic             # refresh ROIC from latest 10-Q XBRL
  → /hunt-status           # print pipeline summary

Quarterly (1–2 hours):
  → /hunt-universe         # rebuild universe (new listings, delistings)
  → /hunt-moat             # re-score moat for any new stage-3 entrants

On-demand:
  → /hunt-portfolio add {ticker}     # after buy decision
  → /hunt-portfolio update {ticker}  # after monitoring raises a flag
  → /hunt-portfolio suggest {ticker} # ask Claude for a recommendation
  → /hunt-portfolio close {ticker}   # after selling
```

***

## Part 7: CLAUDE.md Extensions for Portfolio Intelligence

Add to `CLAUDE.md` so Claude Code has full context when running `/hunt-portfolio suggest`:

```markdown
## Portfolio Recommendation Context

When generating a recommendation for a position via /hunt-portfolio suggest:

1. Always read:
   - portfolio row (thesis, entry price, target horizon)
   - latest scores row (ROIC trend, revenue CAGR, moat score)
   - last 5 monitoring_log rows (any flags)
   - last 5 portfolio_actions rows (prior recommendations)
   - any unacknowledged alerts

2. Compare current ROIC vs entry ROIC — is the business improving or deteriorating?

3. Check if target_horizon is still consistent with thesis — has anything changed?

4. Output format (always JSON, then human-readable summary):
{
  "action": "hold" | "add" | "trim" | "sell" | "review",
  "horizon_months": integer or null,
  "confidence": "high" | "medium" | "low",
  "reason": "one sentence",
  "key_risks": ["risk1", "risk2"],
  "sell_triggers": ["specific condition that would change recommendation"]
}

5. Always remind the user: this is a research tool, not investment advice.
   Decisions should incorporate personal financial situation, tax implications,
   and position sizing discipline.
```