"""Pipeline overview — the funnel's drop-off and why tickers were excluded.

Showing the exclusion breakdown next to the funnel is the point: every stage
records *why* a ticker left, and exclusions are reversible (PRD §14).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import db  # noqa: E402

BUSY_MSG = "A skill is writing to the database right now. Reload in a moment."

STAGE_LABELS = {
    1: "Stage 1 — Universe",
    2: "Stage 2 — Quant",
    3: "Stage 3 — ROIC",
    4: "Stage 4 — Moat",
}
HUE = "#4a7fb5"  # one hue: both charts are single-series magnitude, not identity

st.set_page_config(page_title="Pipeline", page_icon="📈", layout="wide")


@st.cache_data(ttl=30)
def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    with db.connect(read_only=True) as con:
        return db.funnel(con), db.exclusion_counts(con)


def render_funnel(funnel: pd.DataFrame) -> None:
    st.subheader("Funnel")
    if funnel.empty:
        st.info("No universe yet. Run `/hunt-universe`, then `/hunt-score`.")
        return
    labels = [STAGE_LABELS.get(int(s), f"Stage {int(s)}") for s in funnel["stage"]]
    fig = go.Figure(
        go.Funnel(
            y=labels,
            x=funnel["n"],
            marker_color=HUE,
            textinfo="value+percent initial",
        )
    )
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=10), showlegend=False)
    st.plotly_chart(fig, width="stretch")
    st.caption("Stage is a high-water mark; a ticker can be Stage 4 *and* excluded.")


def render_exclusions(exclusions: pd.DataFrame) -> None:
    st.subheader("Exclusion reasons")
    if exclusions.empty:
        st.info("Nothing excluded yet.")
        return
    ordered = exclusions.sort_values("n")  # ascending: largest bar ends up on top
    fig = go.Figure(
        go.Bar(
            x=ordered["n"],
            y=ordered["reason"],
            orientation="h",
            marker_color=HUE,
            text=ordered["n"],
            textposition="outside",
        )
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=10),
        showlegend=False,
        xaxis_title="tickers",
        yaxis_title=None,
    )
    fig.update_xaxes(showgrid=False)
    st.plotly_chart(fig, width="stretch")
    st.dataframe(exclusions, hide_index=True, width="stretch")


st.title("Pipeline")

try:
    funnel_df, exclusions_df = load()
except FileNotFoundError:
    st.info("No database yet. Run `/hunt-universe` to create it.")
    st.stop()
except duckdb.IOException:
    st.warning(BUSY_MSG)
    st.stop()

left, right = st.columns(2)
with left:
    render_funnel(funnel_df)
with right:
    render_exclusions(exclusions_df)
