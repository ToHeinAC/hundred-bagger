-- 100-Bagger Hunter — full 9-table schema.
-- Phase 1 populates `universe`, `scores` (quant columns), and `exclusions`.
-- Later phases populate the rest; the DDL ships whole so no stage ever migrates.
-- Column semantics: docs/schema.md

CREATE SEQUENCE IF NOT EXISTS seq_insider_events START 1;
CREATE SEQUENCE IF NOT EXISTS seq_alerts START 1;
CREATE SEQUENCE IF NOT EXISTS seq_monitoring_log START 1;
CREATE SEQUENCE IF NOT EXISTS seq_portfolio START 1;
CREATE SEQUENCE IF NOT EXISTS seq_portfolio_actions START 1;

-- Stage is a high-water mark (highest stage reached); status is orthogonal.
CREATE TABLE IF NOT EXISTS universe (
    ticker       VARCHAR PRIMARY KEY,
    name         VARCHAR,
    sector       VARCHAR,
    exchange     VARCHAR,
    market_cap   BIGINT,
    avg_volume   BIGINT,
    revenue_ttm  BIGINT,
    stage        INTEGER NOT NULL DEFAULT 1,
    status       VARCHAR NOT NULL DEFAULT 'active',  -- active|excluded|watchlist
    added_date   DATE    NOT NULL,
    updated_date DATE    NOT NULL
);

-- One row per ticker per score_date. Re-running a stage overwrites today's row.
CREATE TABLE IF NOT EXISTS scores (
    ticker            VARCHAR NOT NULL,
    score_date        DATE    NOT NULL,

    -- Stage 2 (quant, yfinance)
    revenue_cagr_3y   DOUBLE,
    gross_margin      DOUBLE,
    operating_margin  DOUBLE,
    fcf_margin        DOUBLE,
    debt_to_equity    DOUBLE,
    share_change_pct  DOUBLE,
    insider_pct       DOUBLE,
    quant_score       INTEGER,
    data_warnings     VARCHAR,   -- comma-separated missing-field codes

    -- Stage 3 (ROIC + avoidance, SEC XBRL)
    roic_3y_median    DOUBLE,
    piotroski_f       INTEGER,
    altman_z          DOUBLE,
    asset_cagr        DOUBLE,
    ebitda_cagr       DOUBLE,
    roic_score        INTEGER,

    -- Stage 4 (moat, judged by Claude Code)
    moat_distribution INTEGER,
    moat_brand        INTEGER,
    moat_network      INTEGER,
    moat_regulatory   INTEGER,
    moat_switching    INTEGER,
    moat_cost         INTEGER,
    moat_total        INTEGER,   -- 0-18
    moat_durability   INTEGER,   -- 0-5
    founder_led       BOOLEAN,
    reinvest_runway   VARCHAR,   -- narrow|medium|wide
    moat_notes        VARCHAR,
    key_risks         VARCHAR,
    moat_score        INTEGER,   -- 0-10, derived

    total_score       INTEGER,   -- 0-34
    PRIMARY KEY (ticker, score_date)
);

-- Exclusions are reversible and always carry a machine-readable reason.
CREATE TABLE IF NOT EXISTS exclusions (
    ticker        VARCHAR NOT NULL,
    reason        VARCHAR NOT NULL,   -- CHRONIC_DILUTER|CASH_BURNER|...
    detail        VARCHAR,
    stage         INTEGER,
    excluded_date DATE    NOT NULL,
    reversed      BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (ticker, reason, excluded_date)
);

CREATE TABLE IF NOT EXISTS insider_events (
    id               BIGINT PRIMARY KEY DEFAULT nextval('seq_insider_events'),
    ticker           VARCHAR NOT NULL,
    filed_date       DATE,
    transaction_date DATE,
    insider_name     VARCHAR,
    insider_title    VARCHAR,
    transaction_type VARCHAR,
    shares           BIGINT,
    price            DOUBLE,
    value            DOUBLE,
    is_cluster_buy   BOOLEAN NOT NULL DEFAULT FALSE,
    signal_strength  VARCHAR
);

CREATE TABLE IF NOT EXISTS alerts (
    id           BIGINT PRIMARY KEY DEFAULT nextval('seq_alerts'),
    ticker       VARCHAR NOT NULL,
    alert_type   VARCHAR NOT NULL,   -- buy|sell|red_flag
    severity     VARCHAR,            -- HIGH|MEDIUM|LOW
    message      VARCHAR,
    created_date DATE    NOT NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS monitoring_log (
    id                 BIGINT PRIMARY KEY DEFAULT nextval('seq_monitoring_log'),
    ticker             VARCHAR NOT NULL,
    check_date         DATE    NOT NULL,
    flags              VARCHAR,   -- JSON array of triggered sell-trigger codes
    recommended_action VARCHAR,   -- HOLD|TRIM|SELL|REVIEW
    notes              VARCHAR
);

CREATE TABLE IF NOT EXISTS portfolio (
    id                   BIGINT PRIMARY KEY DEFAULT nextval('seq_portfolio'),
    ticker               VARCHAR NOT NULL,
    entry_date           DATE    NOT NULL,
    entry_price          DOUBLE  NOT NULL,
    shares               DOUBLE  NOT NULL,
    thesis               VARCHAR,
    horizon_months       INTEGER,
    entry_roic           DOUBLE,
    status               VARCHAR NOT NULL DEFAULT 'open',  -- open|closed
    exit_date            DATE,
    exit_price           DOUBLE,
    realized_return_pct  DOUBLE
);

CREATE TABLE IF NOT EXISTS portfolio_actions (
    id             BIGINT PRIMARY KEY DEFAULT nextval('seq_portfolio_actions'),
    ticker         VARCHAR NOT NULL,
    action_date    DATE    NOT NULL,
    action         VARCHAR NOT NULL,   -- hold|add|trim|sell|review
    horizon_months INTEGER,
    confidence     VARCHAR,            -- low|medium|high
    reason         VARCHAR,
    key_risks      VARCHAR,
    sell_triggers  VARCHAR,
    created_by     VARCHAR NOT NULL DEFAULT 'manual'  -- manual|claude|monitor
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    ticker                VARCHAR NOT NULL,
    snapshot_date         DATE    NOT NULL,
    price                 DOUBLE,
    value                 DOUBLE,
    unrealized_return_pct DOUBLE,
    status_badge          VARCHAR,
    PRIMARY KEY (ticker, snapshot_date)
);
