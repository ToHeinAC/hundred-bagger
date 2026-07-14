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

# --- Stage gates ------------------------------------------------------------

STAGE_2_GATE = 8  # quant_score >= 8 / 14
STAGE_3_GATE = 6  # roic_score  >= 6 / 10
MOAT_TOTAL_GATE = 6  # moat_total >= 6 / 18
MOAT_DURABILITY_GATE = 3  # moat_durability >= 3 / 5
