"""Alert feed — buy signals, sell triggers, and 8-K red flags, with an acknowledge flow.

**The one place the dashboard writes.** Every other page opens DuckDB read-only
(PRD §6). Acknowledging is the exception because the user is the author of the
fact being recorded — "I have seen this" is not screening state, and it cannot be
produced by a skill. The write is exactly one statement, on exactly one column;
nothing else on this page can reach the database with a write handle.
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

SEVERITY_ICON = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "⚪"}
TYPE_LABEL = {"buy": "🟢 Buy", "sell": "🔻 Sell", "red_flag": "🚩 Red flag", "tam": "🎯 TAM"}

st.set_page_config(page_title="Alerts", page_icon="🔔", layout="wide")


@st.cache_data(ttl=30)
def load_alerts() -> pd.DataFrame:
    with db.connect(read_only=True) as con:
        return db.alerts(con)


@st.cache_data(ttl=30)
def load_monitoring_notes() -> pd.DataFrame:
    """Latest monitoring note per ticker. These qualitative `/hunt-monitor` findings
    live in `monitoring_log.notes` and never become a red-flag alert — a regulatory
    action such as an FDA warning letter has no code in the closed red-flag
    vocabulary, so by design it stays in notes. Surfaced here so it is not missed."""
    with db.connect(read_only=True) as con:
        log = db.monitoring_log(con)
    if log.empty:
        return log
    log = log[log["notes"].fillna("").str.strip().astype(bool)]
    return log.drop_duplicates(subset="ticker", keep="first")  # log is check_date DESC


def acknowledge(ids: list[int]) -> None:
    """The write. Opened read-write, used, closed — the handle never outlives it."""
    with db.connect() as con:
        db.acknowledge_alerts(con, ids)
    st.cache_data.clear()


def render(feed: pd.DataFrame, *, ackable: bool) -> None:
    shown = feed.assign(
        severity=feed["severity"].map(lambda s: f"{SEVERITY_ICON.get(s, '')} {s or ''}".strip()),
        alert_type=feed["alert_type"].map(lambda t: TYPE_LABEL.get(t, t)),
    )
    if not ackable:
        st.dataframe(
            shown[["created_date", "ticker", "alert_type", "severity", "message"]],
            hide_index=True, width="stretch",
        )
        return

    edited = st.data_editor(
        shown.assign(ack=False)[
            ["ack", "created_date", "ticker", "alert_type", "severity", "message"]
        ],
        hide_index=True,
        width="stretch",
        disabled=["created_date", "ticker", "alert_type", "severity", "message"],
        column_config={"ack": st.column_config.CheckboxColumn("✓", help="Mark as seen")},
        key="alert_editor",
    )
    selected = shown.loc[edited.index[edited["ack"]], "id"].tolist()
    if st.button(f"Acknowledge {len(selected)} alert(s)", disabled=not selected, type="primary"):
        acknowledge(selected)
        st.rerun()


st.title("Alerts")
st.caption(
    "Buy signals come from `/hunt-signals`; sell triggers and red flags from `/hunt-monitor`; "
    "🎯 TAM alerts from `/hunt-moat`, where the 100x arithmetic does not fit the market. "
    "Acknowledging only records that you have seen an alert — it never changes screening state."
)

try:
    feed = load_alerts()
except FileNotFoundError:
    st.info("No database yet. Run `/hunt-universe` to create it.")
    st.stop()
except duckdb.IOException:
    st.warning(BUSY_MSG)
    st.stop()

if feed.empty:
    st.info(
        "No alerts. Run `/hunt-signals` to check the watchlist for entry signals, "
        "or `/hunt-monitor` to check open positions against the sell triggers."
    )
    st.stop()

open_alerts = feed[~feed["acknowledged"]]
cols = st.columns(5)
cols[0].metric("Unacknowledged", len(open_alerts))
for col, kind in zip(cols[1:], ("buy", "sell", "red_flag", "tam")):
    col.metric(TYPE_LABEL[kind], len(open_alerts[open_alerts["alert_type"] == kind]))

st.subheader("Open")
if open_alerts.empty:
    st.success("Everything is acknowledged.")
else:
    render(open_alerts, ackable=True)

st.subheader("Monitoring notes")
try:
    notes = load_monitoring_notes()
except duckdb.IOException:
    st.warning(BUSY_MSG)
    notes = pd.DataFrame()

if notes.empty:
    st.caption(
        "None yet. `/hunt-monitor` records qualitative 8-K findings here — including "
        "material events with no red-flag code, such as a regulatory action."
    )
else:
    st.warning(
        "Qualitative findings from `/hunt-monitor`'s 8-K review. A regulatory action "
        "(e.g. an FDA warning letter) has **no code** in the closed red-flag vocabulary, "
        "so it raises no red-flag alert — read it here."
    )
    st.dataframe(
        notes[["check_date", "ticker", "recommended_action", "notes"]].rename(
            columns={"recommended_action": "action"}
        ),
        hide_index=True, width="stretch",
    )

with st.expander(f"Acknowledged ({len(feed) - len(open_alerts)})"):
    acked = feed[feed["acknowledged"]]
    if acked.empty:
        st.caption("Nothing acknowledged yet.")
    else:
        render(acked, ackable=False)
