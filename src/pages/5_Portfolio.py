"""Portfolio — what is held, how far it has come, and what to do next.

**The dashboard's second write path, and the one PRD §6 always allowed.** The
positions are the user's own facts: no skill can derive them, so this page is
where they enter. The write is an import and a clear, nothing else; every other
page stays read_only=True.

It is also the dashboard's second network call (Stock Detail's chart is the
first). Quotes are fetched only when the button is pressed — never on load — so
opening this page costs nothing and works offline, just without prices. The one
exception is the EUR=X rate, and only for a book that actually holds two
currencies: the cost basis cannot be stated in one currency without it, and it
is cached for an hour. Both degrade to None rather than raising.

The recommendation column is hold-biased by design: see `src/portfolio.py` and
the thresholds in `src/config.py`. Nothing here is investment advice.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import config, db, portfolio  # noqa: E402

BUSY_MSG = "A skill is writing to the database right now. Reload in a moment."

SYMBOL = {"EUR": "€", "USD": "$"}
HUE = "#4a7fb5"  # the dashboard's one chart hue, as on the Pipeline page

ACTION_LABEL = {
    portfolio.HOLD: "🟢 Hold",
    portfolio.ADD: "🔵 Add",
    portfolio.TRIM: "🟠 Trim",
    portfolio.SELL: "🔻 Sell",
    portfolio.REVIEW: "🔴 Review",
}

st.set_page_config(page_title="Portfolio", page_icon="💼", layout="wide")


@st.cache_data(ttl=30)
def load_positions() -> pd.DataFrame:
    with db.connect(read_only=True) as con:
        return db.open_positions(con)


@st.cache_data(ttl=30)
def load_monitor_actions() -> dict[str, str]:
    """The monitor's verdict per ticker — how a position learns its thesis broke."""
    with db.connect(read_only=True) as con:
        return db.latest_monitor_action(con)


@st.cache_data(ttl=3600)
def load_fx_rate() -> float | None:
    """EUR per USD. Cached for an hour: a book is not re-valued by the tick, and
    this is the only call the page makes without the user pressing a button."""
    return portfolio.fetch_fx_rate()


def do_import(text: str, replace: bool) -> int:
    """The write. Opened read-write, used, closed — the handle never outlives it."""
    with db.connect() as con:
        n = portfolio.import_csv(con, text, replace=replace)
    st.cache_data.clear()
    return n


def render_import() -> None:
    st.download_button(
        "Download CSV template",
        data=portfolio.CSV_TEMPLATE,
        file_name="portfolio_template.csv",
        mime="text/csv",
    )
    uploaded = st.file_uploader(
        "Upload positions (CSV)", type=["csv"],
        help="Columns: ticker, shares, entry_price. "
             "Optional: currency (USD/EUR, default USD), entry_date, thesis.",
    )
    if uploaded is None:
        return
    replace = st.checkbox("Replace the current book", value=False,
                          help="Off: append these rows. On: empty the table first.")
    if st.button("Import", type="primary"):
        try:
            n = do_import(uploaded.getvalue().decode("utf-8", errors="replace"), replace)
        except ValueError as e:  # a named bad row — never a half-imported book
            st.error(f"Could not import: {e}")
        except duckdb.IOException:
            st.warning(BUSY_MSG)
        else:
            st.success(f"Imported {n} position(s).")
            st.rerun()


@st.cache_data(ttl=3600)
def load_profile(ticker: str) -> dict:
    return portfolio.fetch_profile(ticker)


@st.cache_data(ttl=3600)
def load_history(ticker: str) -> pd.DataFrame | None:
    return portfolio.fetch_history(ticker)


def render_ticker(ticker: str) -> None:
    """The company behind a selected position, straight from Yahoo.

    Deliberately in the ticker's *own* currency, not the book's: a market cap or
    an EPS is a fact about the company, and a chart spanning years cannot be
    honestly rescaled by today's spot rate. The label says which currency it is.
    """
    st.divider()
    info = load_profile(ticker)
    name = info.get("longName") or info.get("shortName") or ""
    st.subheader(f"{ticker} — {name}" if name else ticker)
    if not info:
        st.caption("Yahoo returned nothing for this ticker — no profile to show.")
        return

    history = load_history(ticker)
    if history is None:
        st.caption("Price chart unavailable — yfinance returned no history.")
    else:
        fig = go.Figure(go.Scatter(x=history.index, y=history["Close"],
                                   mode="lines", line_color=HUE, name="Close"))
        fig.update_layout(
            margin=dict(l=0, r=0, t=10, b=10), showlegend=False,
            yaxis_title=f"Close ({info.get('currency') or ''})".strip(),
        )
        fig.update_xaxes(rangeslider_visible=True)  # full range, and zoomable
        st.plotly_chart(fig, width="stretch")
        st.caption(
            f"Full listed history — {history.index[0]:%Y-%m-%d} to "
            f"{history.index[-1]:%Y-%m-%d}, in the ticker's own currency."
        )

    grid = portfolio.profile_rows(info)
    for chunk in [grid[i:i + 4] for i in range(0, len(grid), 4)]:
        for col, (label, value) in zip(st.columns(4), chunk):
            col.metric(label, value)

    summary = info.get("longBusinessSummary")
    if summary:
        with st.expander("Description", expanded=False):
            st.write(summary)


def render_book(held: pd.DataFrame) -> None:
    prices = st.session_state.get("pf_prices", {})
    cols = st.columns([1, 1, 2])
    if cols[0].button("Refresh prices", type="primary"):
        with st.spinner("Fetching quotes…"):
            st.session_state["pf_prices"] = portfolio.fetch_prices(list(held["ticker"]))
        st.rerun()
    # Display only: quotes stay in session_state in their native currency, so
    # flipping this converts what is already there rather than refetching.
    display = cols[1].radio(
        "Currency", list(SYMBOL), horizontal=True, key="pf_currency",
        help="The whole book, stated in one currency. Multiple and Gain % do not move.",
    )
    if not prices:
        cols[2].caption("No quotes yet — press **Refresh prices** (the one network call).")

    rate = load_fx_rate() if portfolio.needs_fx(held, display) else None
    held, prices = portfolio.convert(held, prices, display, rate)
    mixed = set(held["native_currency"]) - {display}
    if mixed and not rate:
        st.warning(
            f"No `{portfolio.FX_TICKER}` quote — {', '.join(sorted(mixed))} positions are "
            f"shown in their own currency, so the totals below mix currencies."
        )

    try:
        monitor_actions = load_monitor_actions()
    except duckdb.IOException:
        st.warning(BUSY_MSG)
        monitor_actions = {}

    valued = portfolio.value(held, prices, monitor_actions)
    t = portfolio.totals(valued)
    sym = SYMBOL[display]

    m = st.columns(4)
    m[0].metric(f"Cost ({sym})", f"{t['cost']:,.0f}")
    m[1].metric(f"Value ({sym})", f"{t['value']:,.0f}" if t["priced"] else "—")
    m[2].metric(
        f"Gain ({sym})",
        f"{t['gain']:+,.0f}" if t["priced"] else "—",
        f"{t['gain_pct']:+.1%}" if t["priced"] and t["gain_pct"] is not None else None,
    )
    best = valued["multiple"].max()
    m[3].metric(
        f"Best multiple (goal {config.MOONSHOT_MULTIPLE}x)",
        f"{best:.2f}x" if pd.notna(best) else "—",
    )

    shown = valued.assign(action=valued["action"].map(ACTION_LABEL))
    event = st.dataframe(
        shown[["ticker", "shares", "entry_price", "price", "native_currency",
               "multiple", "gain_pct", "weight", "action", "thesis"]],
        hide_index=True,
        width="stretch",
        key="pf_table",
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "entry_price": st.column_config.NumberColumn(f"Entry ({sym})", format="%.2f"),
            "price": st.column_config.NumberColumn(f"Price ({sym})", format="%.2f"),
            "native_currency": st.column_config.TextColumn(
                "Native", width="small",
                help="The currency the position is quoted in, before conversion."),
            "multiple": st.column_config.NumberColumn(
                "Multiple", format="%.2fx", help=f"Price / entry — progress toward "
                f"{config.MOONSHOT_MULTIPLE}x."),
            "gain_pct": st.column_config.NumberColumn("Gain %", format="percent"),
            "weight": st.column_config.NumberColumn(
                "Weight", format="percent", help="Share of the priced book."),
            "action": st.column_config.TextColumn("Action"),
            "thesis": st.column_config.TextColumn("Thesis", width="medium"),
        },
    )
    st.caption(
        f"🟢 **Hold** is the default and the point — the enemy of a 100-bagger is selling "
        f"too soon. 🔵 **Add** on a dip past {config.ADD_DIP_PCT:.0%}. "
        f"🟠 **Trim** only past {config.CONCENTRATION_CAP:.0%} of the book — risk "
        f"management, never profit-taking. 🔻 **Sell** / 🔴 **Review** come from "
        f"`/hunt-monitor`'s evidenced verdict, not from price. Not investment advice."
    )
    if rate:
        st.caption(
            f"Everything above is in {display}: 1 USD = {rate:.4f} EUR "
            f"(Yahoo `{portfolio.FX_TICKER}`, cached hourly)."
        )
    st.download_button(
        "Download snapshot (CSV)",
        data=portfolio.snapshot_csv(valued),
        file_name="portfolio.csv",
        mime="text/csv",
        help="Re-imports cleanly.",
    )

    rows = event.selection.rows
    if rows:
        render_ticker(str(valued.iloc[rows[0]]["ticker"]))
    else:
        st.caption("Select a row to see the company behind the position.")


st.title("Portfolio")
st.caption(
    "Your open positions, marked to market. Sell triggers come from `/hunt-monitor` — "
    "this page never re-decides a thesis from price. Positions are yours to enter: "
    "this is the one dashboard page that writes."
)

try:
    held = load_positions()
except FileNotFoundError:
    st.info("No database yet. Run `/hunt-universe` to create it.")
    st.stop()
except duckdb.IOException:
    st.warning(BUSY_MSG)
    st.stop()

if held.empty:
    st.info("No open positions yet. Import a CSV to start tracking the book.")
    render_import()
    st.stop()

render_book(held)
st.divider()
with st.expander("Import more positions"):
    render_import()
