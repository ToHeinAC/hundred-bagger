"""Screening thresholds (code-as-config) and .env scalars.

Thresholds live here, under version control, so a change to the screen shows up
in a diff. Only environment-specific scalars come from .env.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- .env scalars -----------------------------------------------------------

DUCKDB_PATH = Path(os.getenv("DUCKDB_PATH", REPO_ROOT / "data" / "100baggers.duckdb"))
STREAMLIT_PORT = int(os.getenv("STREAMLIT_PORT", "8501"))
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "")

# --- Stage 1: universe hard filters -----------------------------------------

MIN_MARKET_CAP = 50_000_000
MAX_MARKET_CAP = 1_000_000_000
MIN_AVG_VOLUME = 50_000
MIN_REVENUE_TTM = 10_000_000
REGION = "us"

# Yahoo sectors kept in the universe. The complement (Financial Services,
# Utilities, Real Estate, Energy, Basic Materials) is excluded: balance-sheet
# driven or commodity-priced businesses do not compound the way Mayer's
# framework assumes.
INCLUDED_SECTORS = (
    "Technology",
    "Healthcare",
    "Consumer Cyclical",
    "Industrials",
    "Communication Services",
    "Consumer Defensive",
)

# Yahoo `exchange` codes for real US listings. Everything else (PNK, OQB, OQX,
# ...) is OTC/pink-sheet and out of scope per PRD §4.
ALLOWED_EXCHANGES = frozenset({"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS"})

# --- Stage 2: quantitative rubric (0-14) ------------------------------------
# Each entry maps a metric to (threshold, points) bands, evaluated best-first.
# Bands are (min_value, points) for "higher is better" metrics and
# (max_value, points) for "lower is better" ones. See docs/scoring.md.

REVENUE_CAGR_BANDS = ((0.20, 3), (0.15, 2), (0.10, 1))  # 3 pts
GROSS_MARGIN_BANDS = ((0.50, 2), (0.35, 1))  # 2 pts
OPERATING_MARGIN_BANDS = ((0.15, 2), (0.05, 1))  # 2 pts
FCF_MARGIN_BANDS = ((0.10, 2), (0.0, 1))  # 2 pts
DEBT_TO_EQUITY_BANDS = ((0.30, 2), (0.75, 1))  # 2 pts, lower is better
SHARE_CHANGE_BANDS = ((0.0, 2), (0.02, 1))  # 2 pts, lower is better
INSIDER_OWNERSHIP_BANDS = ((0.10, 1),)  # 1 pt

QUANT_MAX_SCORE = 14

# --- Auto-exclusion rules (Stage 2) -----------------------------------------

CHRONIC_DILUTER_PCT = 0.05  # >5%/yr share growth
EXCESSIVE_LEVERAGE_DE = 3.0  # debt/equity > 3
REVENUE_DECLINE_CAGR = 0.0  # 3y revenue CAGR < 0

# --- SEC EDGAR (Stage 3 + Stage 4 source) ------------------------------------

SEC_BASE = "https://data.sec.gov"
SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SLEEP = 0.11  # <= 10 req/s, enforced in code — exceeding it gets the IP blocked
SEC_TIMEOUT = 30

# --- Stage 3: ROIC + avoidance rubric (0-10) --------------------------------
# Same first-band-wins evaluation as Stage 2 (scorer.band).

ROIC_BANDS = ((0.20, 5), (0.15, 4), (0.12, 3), (0.10, 2), (0.07, 1))  # 5 pts
PIOTROSKI_BANDS = ((7, 3), (5, 2), (4, 1))  # 3 pts
ALTMAN_Z_BANDS = ((3.0, 2), (1.8, 1))  # 2 pts

ROIC_MAX_SCORE = 10
ROIC_MEDIAN_YEARS = 3  # median of the last 3 fiscal years
DEFAULT_TAX_RATE = 0.21  # used when the effective rate is unavailable or absurd

# --- Auto-exclusion rules (Stage 3) -----------------------------------------

ASSET_BLOAT_GAP = 0.10  # asset CAGR outruns EBITDA CAGR by > 10pp
ALTMAN_Z_DISTRESS = 1.8  # classic bankruptcy-risk zone

# --- Stage 4: moat ----------------------------------------------------------
# The *rubric* lives in .claude/skills/hunt-moat/SKILL.md — Claude applies it.
# Only the shape of the answer and the arithmetic on it live here.

MOAT_DIMENSIONS = ("distribution", "brand", "network", "regulatory", "switching", "cost")
MOAT_DIMENSION_MAX = 3  # six dimensions x 0-3 -> moat_total 0-18
MOAT_DURABILITY_MAX = 5
REINVEST_RUNWAYS = ("narrow", "medium", "wide")

# How the 0-18 total and the 0-5 durability collapse into the 0-10 moat_score
# that feeds total_score. Durability carries 40%: a wide but eroding moat is
# worth less over a ten-year hold than a narrow durable one.
MOAT_TOTAL_WEIGHT = 6
MOAT_DURABILITY_WEIGHT = 4


def moat_score(moat_total: int, moat_durability: int) -> int:
    """Derive the 0-10 moat_score. The one place 18 + 5 becomes 10."""
    breadth = MOAT_TOTAL_WEIGHT * moat_total / (len(MOAT_DIMENSIONS) * MOAT_DIMENSION_MAX)
    durability = MOAT_DURABILITY_WEIGHT * moat_durability / MOAT_DURABILITY_MAX
    return round(breadth + durability)


# --- Stage gates ------------------------------------------------------------

STAGE_2_GATE = 8  # quant_score >= 8 / 14
STAGE_3_GATE = 6  # roic_score  >= 6 / 10
MOAT_TOTAL_GATE = 6  # moat_total >= 6 / 18
MOAT_DURABILITY_GATE = 3  # moat_durability >= 3 / 5

# --- Phase 3: entry signals (Form 4 + valuation) ----------------------------
# A cluster buy is the signal Mayer rates highest: several insiders, buying with
# their own money, on the open market, at the same time. Only transaction code
# "P" counts — a grant or an option exercise is compensation, not conviction.

INSIDER_LOOKBACK_DAYS = 180  # how far back to pull Form 4s
CLUSTER_WINDOW_DAYS = 90  # insiders must buy within this rolling window
CLUSTER_MIN_INSIDERS = 3  # distinct people, not distinct filings
CLUSTER_MIN_VALUE = 100_000  # aggregate USD across the window

# Valuation gates. Not a scoring rubric — three independent yes/no tests. A
# missing ratio is an ungated (unknown) test, never a passed one.
MAX_P_FCF = 20.0
MAX_EV_EBITDA = 15.0
MAX_PEG = 2.0

# Price zone: position in the 52-week range, 0.0 at the low, 1.0 at the high.
BUY_ZONE_MAX = 0.50  # lower half of the range

# --- Phase 3: sell triggers (mechanical, evaluated in Python) ---------------
# The thesis-break table. Each rule needs TWO consecutive bad years, or a move
# large enough that one year is not noise — a single soft quarter is not a sell.

SELL_ROIC_FLOOR = 0.10  # ROIC below this for SELL_TREND_YEARS running
SELL_TREND_YEARS = 2
SELL_MARGIN_DROP = 0.05  # operating margin down >5pp vs 2 years ago
SELL_DILUTION_PCT = 0.05  # share count up >5% year-over-year

# Red flags Claude reads out of recent 8-Ks. Any one of them is a SELL.
RED_FLAGS = (
    "RESTATEMENT",
    "GOING_CONCERN",
    "AUDITOR_RESIGNATION",
    "SEC_INVESTIGATION",
    "KEY_MAN_DEPARTURE",
    "MATERIAL_IMPAIRMENT",
)

EIGHTK_LOOKBACK_DAYS = 90
EIGHTK_CHAR_CAP = 20_000

# --- Paths ------------------------------------------------------------------

MOAT_INPUT_DIR = REPO_ROOT / "data" / "moat_input"
MONITOR_INPUT_DIR = REPO_ROOT / "data" / "monitor_input"
