"""Ranked candidate list — the funnel's output, filterable and sortable.

data_warnings is shown, not hidden: yfinance is unreliable on microcaps, and a
flagged ticker is a review item rather than a silent exclusion (PRD §2.4).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import db  # noqa: E402

BUSY_MSG = "A skill is writing to the database right now. Reload in a moment."

COLUMNS = [
    "ticker", "name", "sector", "market_cap", "stage",
    "quant_score", "total_score", "status", "data_warnings",
]

st.set_page_config(page_title="Watchlist", page_icon="📈", layout="wide")


@st.cache_data(ttl=30)
def load_scores() -> pd.DataFrame:
    with db.connect(read_only=True) as con:
        return db.latest_scores(con)[COLUMNS]


def format_market_cap(value: float | None) -> str:
    if pd.isna(value):
        return "—"
    if value >= 1e9:
        return f"${value / 1e9:.1f}B"
    return f"${value / 1e6:.0f}M"


def apply_filters(scores: pd.DataFrame) -> pd.DataFrame:
    """Filters live in the sidebar so the table gets the full width."""
    with st.sidebar:
        st.subheader("Filters")
        sectors = st.multiselect("Sector", sorted(scores["sector"].dropna().unique()))
        statuses = st.multiselect("Status", sorted(scores["status"].dropna().unique()))
        stages = st.multiselect("Stage", sorted(scores["stage"].dropna().unique()))
        min_total = st.slider("Min total score", 0, 34, 0)

    filtered = scores[scores["total_score"].fillna(0) >= min_total]
    if sectors:
        filtered = filtered[filtered["sector"].isin(sectors)]
    if statuses:
        filtered = filtered[filtered["status"].isin(statuses)]
    if stages:
        filtered = filtered[filtered["stage"].isin(stages)]
    return filtered.sort_values("total_score", ascending=False)


st.title("Watchlist")

try:
    scores = load_scores()
except FileNotFoundError:
    st.info("No database yet. Run `/hunt-universe`, then `/hunt-score`.")
    st.stop()
except duckdb.IOException:
    st.warning(BUSY_MSG)
    st.stop()

if scores.empty:
    st.info("No scored candidates yet. Run `/hunt-universe`, then `/hunt-score`.")
    st.stop()

ranked = apply_filters(scores)
st.caption(f"{len(ranked)} of {len(scores)} scored candidates")
st.dataframe(
    ranked.assign(market_cap=ranked["market_cap"].map(format_market_cap)),
    hide_index=True,
    width="stretch",
    column_config={
        "quant_score": st.column_config.NumberColumn("quant", help="0–14"),
        "total_score": st.column_config.NumberColumn("total", help="0–34"),
        "data_warnings": st.column_config.TextColumn("warnings"),
    },
)
