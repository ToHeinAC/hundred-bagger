"""One stock, end to end — the answer to "why is this on my watchlist?".

The PRD's UX goal (§11) is that this question is answerable from this page alone,
without re-running anything. So every stage's inputs are shown next to its
subscore, and the audit trail — data warnings, exclusions — is shown next to the
number it undermines, not buried a page away.

A stage that has not run yet is *unmeasured*, not zero. It renders as a prompt to
run the skill, never as a grid of dashes that reads like a bad result.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import db  # noqa: E402
from src.metric_help import METRIC_HELP  # noqa: E402

BUSY_MSG = "A skill is writing to the database right now. Reload in a moment."

st.set_page_config(page_title="Stock Detail", page_icon="📈", layout="wide")


def _pct(v) -> str:
    return "—" if pd.isna(v) else f"{v:.1%}"


def _num(v) -> str:
    return "—" if pd.isna(v) else f"{v:.2f}"


def _int(v) -> str:
    return "—" if pd.isna(v) else f"{int(v)}"


def _bool(v) -> str:
    return "—" if pd.isna(v) else ("yes" if v else "no")


def _text(v) -> str:
    return "—" if pd.isna(v) else str(v)


# (label, column, formatter) — the inputs to each stage's subscore, in rubric order.
QUANT_FIELDS = [
    ("Revenue CAGR 3y", "revenue_cagr_3y", _pct),
    ("Gross margin", "gross_margin", _pct),
    ("Operating margin", "operating_margin", _pct),
    ("FCF margin", "fcf_margin", _pct),
    ("Debt / equity", "debt_to_equity", _num),
    ("Share change", "share_change_pct", _pct),
    ("Insider held", "insider_pct", _pct),
]
ROIC_FIELDS = [
    ("ROIC 3y median", "roic_3y_median", _pct),
    ("Piotroski F", "piotroski_f", _int),
    ("Altman Z", "altman_z", _num),
    ("Asset CAGR", "asset_cagr", _pct),
    ("EBITDA CAGR", "ebitda_cagr", _pct),
]
MOAT_FIELDS = [
    ("Distribution", "moat_distribution", _int),
    ("Brand", "moat_brand", _int),
    ("Network", "moat_network", _int),
    ("Regulatory", "moat_regulatory", _int),
    ("Switching", "moat_switching", _int),
    ("Cost", "moat_cost", _int),
    ("Durability", "moat_durability", _int),
    ("Founder-led", "founder_led", _bool),
    ("Reinvest runway", "reinvest_runway", _text),
]


@st.cache_data(ttl=30)
def load_scores() -> pd.DataFrame:
    with db.connect(read_only=True) as con:
        return db.latest_scores(con)


@st.cache_data(ttl=30)
def load_exclusions(ticker: str) -> pd.DataFrame:
    with db.connect(read_only=True) as con:
        return db.exclusions_for_ticker(con, ticker)


@st.cache_data(ttl=3600)
def load_prices(ticker: str) -> pd.Series | None:
    """The only network call in the dashboard. A failure degrades, never breaks."""
    try:
        history = yf.Ticker(ticker).history(period="1y")
        return None if history.empty else history["Close"]
    except Exception:
        return None


def render_metrics(row: pd.Series, fields: list[tuple]) -> None:
    for chunk in [fields[i : i + 4] for i in range(0, len(fields), 4)]:
        for col, (label, name, fmt) in zip(st.columns(4), chunk):
            col.metric(label, fmt(row[name]), help=METRIC_HELP.get(name))


def render_stage(row: pd.Series, title: str, subscore: str, fields: list[tuple],
                 skill: str, extra: str = "") -> None:
    st.subheader(title)
    if pd.isna(row[subscore]):
        st.info(f"Not yet scored — run `{skill}`.")
        return
    st.caption(f"{extra}Subscore **{int(row[subscore])}**")
    render_metrics(row, fields)


def render_caveats(row: pd.Series, exclusions: pd.DataFrame) -> None:
    if not pd.isna(row["data_warnings"]) and row["data_warnings"]:
        st.warning(
            f"**Incomplete data:** `{row['data_warnings']}`. Each missing metric scored "
            "0 points. A low score here is a statement about Yahoo's coverage, not "
            "about the company — treat it as unmeasured, not bad."
        )
    if not exclusions.empty:
        live = exclusions[~exclusions["reversed"]]
        verb = "Excluded" if not live.empty else "Previously excluded (reversed)"
        st.error(f"**{verb}.** Exclusions are reversible and recorded in full:")
        st.dataframe(exclusions, hide_index=True, width="stretch")


st.title("Stock Detail")

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

ranked = scores.sort_values("total_score", ascending=False)
ticker = st.sidebar.selectbox("Ticker", ranked["ticker"].tolist())
row = ranked[ranked["ticker"] == ticker].iloc[0]

st.header(f"{ticker} — {_text(row['name'])}")
head = st.columns(4)
head[0].metric("Total score", _int(row["total_score"]), help=METRIC_HELP["total_score"])
head[1].metric("Stage", _int(row["stage"]), help=METRIC_HELP["stage"])
head[2].metric("Status", _text(row["status"]), help=METRIC_HELP["status"])
head[3].metric("Sector", _text(row["sector"]), help=METRIC_HELP["sector"])

render_caveats(row, load_exclusions(ticker))

prices = load_prices(ticker)
if prices is None:
    st.caption("Price chart unavailable — yfinance returned nothing for this ticker.")
else:
    st.line_chart(prices, y_label="close", height=220)

render_stage(row, "Stage 2 — Quant (0–14)", "quant_score", QUANT_FIELDS, "/hunt-score")
render_stage(row, "Stage 3 — ROIC (0–10)", "roic_score", ROIC_FIELDS, "/hunt-roic")

moat_total = "" if pd.isna(row["moat_total"]) else f"Moat total **{int(row['moat_total'])}**/18 · "
render_stage(row, "Stage 4 — Moat (0–10)", "moat_score", MOAT_FIELDS, "/hunt-moat", moat_total)

if not pd.isna(row["moat_score"]):
    st.markdown("**Moat notes**")
    st.markdown(_text(row["moat_notes"]))
    st.markdown("**Key risks**")
    st.markdown(_text(row["key_risks"]))

with st.expander("Metric glossary"):
    for title, fields in [
        ("Stage 2 — Quant", QUANT_FIELDS),
        ("Stage 3 — ROIC", ROIC_FIELDS),
        ("Stage 4 — Moat", MOAT_FIELDS),
    ]:
        st.markdown(f"**{title}**")
        for label, name, _fmt in fields:
            st.markdown(f"- **{label}** — {METRIC_HELP[name]}")
