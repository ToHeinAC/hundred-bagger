"""Dashboard home — pipeline overview, data freshness, safe exit.

Read-only: Phase 1 has no write path from the UI (PRD §6). Every page opens the
database with read_only=True, so a dashboard bug can never corrupt screening state.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import duckdb
import streamlit as st

# Streamlit puts only the entrypoint's folder (src/) on sys.path, so the repo
# root — where the `src` package lives — has to be added before `src.db` imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import db  # noqa: E402

# A running skill holds DuckDB's write lock, which blocks even a read-only open.
# That is the normal case (open the dashboard while /hunt-score runs), not an error.
BUSY_MSG = "A skill is writing to the database right now. Reload in a moment."

st.set_page_config(page_title="100-Bagger Hunter", page_icon="📈", layout="wide")


@st.cache_data(ttl=30)
def load_summary() -> dict:
    """Raises FileNotFoundError (no DB yet) or duckdb.IOException (skill running).

    st.cache_data caches returns, not exceptions, so both stay live on rerun.
    """
    with db.connect(read_only=True) as con:
        return db.status_summary(con)


def safe_exit_button() -> None:
    """SIGTERM to our own PID only — never a port-kill, which would take down
    SSH or forwarded connections sharing the port (PRD §9)."""
    with st.sidebar:
        st.divider()
        st.caption("Shut down the dashboard")
        confirm = st.checkbox("I want to stop the app", key="confirm_exit")
        if st.button("Exit", disabled=not confirm, width="stretch"):
            st.warning("Shutting down. You can close this tab.")
            os.kill(os.getpid(), signal.SIGTERM)


def render_empty_state(summary: dict | None) -> None:
    st.info("The pipeline is empty. Run the screening skills to populate it.")
    st.markdown(
        "1. `/hunt-universe` — build the Stage 1 universe (hard filters)\n"
        "2. `/hunt-score` — score each ticker 0–14 on quantitative fundamentals\n"
        "3. `/hunt-status` — check the funnel, then reload this page"
    )
    if summary is None:
        st.caption("No database file yet — `/hunt-universe` creates it.")


def render_freshness(summary: dict) -> None:
    st.subheader("Data freshness")
    cols = st.columns(3)
    fields = [
        ("Universe last built", "universe_last_built"),
        ("Scores last run", "scores_last_run"),
        ("Monitor last run", "monitor_last_run"),
    ]
    for col, (label, key) in zip(cols, fields):
        value = summary[key]
        col.metric(label, str(value) if value else "never")


def render_overview(summary: dict) -> None:
    st.subheader("Pipeline")
    cols = st.columns(4)
    cols[0].metric("Universe", summary["universe_total"])
    cols[1].metric("Active", summary["active"])
    cols[2].metric("Excluded", summary["excluded"])
    cols[3].metric("Watchlist", summary["watchlist"])

    by_stage = summary["by_stage"]
    if by_stage:
        st.caption("Tickers per stage (high-water mark)")
        st.bar_chart(
            {"stage": list(by_stage), "tickers": list(by_stage.values())},
            x="stage",
            y="tickers",
        )


st.title("100-Bagger Hunter")
st.caption("Screening state is written by the `hunt-*` skills. This dashboard only reads it.")

safe_exit_button()

try:
    summary = load_summary()
except FileNotFoundError:
    render_empty_state(None)
    st.stop()
except duckdb.IOException:
    st.warning(BUSY_MSG)
    st.stop()

if summary["universe_total"] == 0:
    render_empty_state(summary)
    render_freshness(summary)
else:
    render_overview(summary)
    render_freshness(summary)
    st.caption("Use the sidebar to open the Pipeline and Watchlist pages.")
