"""portfolio.py — CSV import, mark-to-market, and the hold-biased rules.

Budget-bound: PRD §11 caps the suite at 200 and Phase 4's share is 13, so cases
are merged and parametrized rather than given a test each. Anything cut here is
covered obliquely by a neighbour — the unpriced position reads `hold` inside the
valuation test, and both monitor mappings are split across `recommend` and
`latest_monitor_action`.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from src import config, db, portfolio

CSV = "ticker,shares,entry_price\nMELI,10,100\nITRN,5,200\n"

BAD_CSV = [
    ("ticker,shares\nMELI,10\n", "missing required column"),        # no entry_price
    ("ticker,shares,entry_price\nMELI,ten,100\n", "must be numbers"),
    ("ticker,shares,entry_price\nMELI,10,2024\nITRN,5,x\n", r"Row 3 \(ITRN\)"),
    ("ticker,shares,entry_price,entry_date\nMELI,10,1,15/01/24\n", "YYYY-MM-DD"),
    ("ticker,shares,entry_price\n\n", "no positions"),
]


def _held(rows: list[tuple]) -> pd.DataFrame:
    """A holdings frame shaped like db.open_positions: (ticker, shares, entry_price)."""
    return pd.DataFrame(rows, columns=["ticker", "shares", "entry_price"])


# --- CSV ---------------------------------------------------------------------


def test_parse_csv_reads_positions_aliases_dates_and_skips_blanks():
    """One happy path covering every accepted spelling. `quantity`/`buy_price`
    are the old tracker's names — its exports must still import."""
    rows = portfolio.parse_csv(
        " Ticker , QUANTITY ,Buy_Price, entry_date ,thesis\n"
        " meli ,10,100,2024-01-15,flywheel\n"
        "\n"
        "ITRN,5,200,,\n"
    )
    assert rows == [
        {"ticker": "MELI", "shares": 10.0, "entry_price": 100.0,
         "entry_date": dt.date(2024, 1, 15), "thesis": "flywheel"},
        {"ticker": "ITRN", "shares": 5.0, "entry_price": 200.0,
         "entry_date": None, "thesis": None},
    ]


def test_parse_csv_rejects_bad_input_naming_the_problem():
    """A portfolio that is quietly wrong is worse than one that fails to load.

    Looped rather than parametrized to stay inside the test budget; `match`
    identifies the failing case.
    """
    for text, message in BAD_CSV:
        with pytest.raises(ValueError, match=message):
            portfolio.parse_csv(text)


# --- valuation ---------------------------------------------------------------


def test_value_marks_to_market_counts_progress_to_100x_and_keeps_unpriced_rows():
    valued = portfolio.value(_held([("MELI", 10, 100), ("ITRN", 5, 200)]),
                             {"MELI": 300, "ITRN": None})
    meli, itrn = valued.iloc[0], valued.iloc[1]
    assert (meli.cost_basis, meli.market_value, meli.gain, meli.gain_pct) == (1000, 3000, 2000, 2.0)
    assert meli.multiple == 3.0  # 3x of the 100 the app is named for
    assert meli.weight == 1.0  # weights are taken against the *priced* value

    assert pd.isna(itrn.price) and pd.isna(itrn.weight)  # no quote
    assert itrn.cost_basis == 1000  # ... but still in the book, at cost
    assert itrn.action == portfolio.HOLD

    assert portfolio.totals(valued) == {
        "cost": 2000.0, "value": 3000.0, "gain": 1000.0, "gain_pct": 0.5, "priced": True,
    }


def test_totals_report_unpriced_rather_than_guessing_zero():
    valued = portfolio.value(_held([("MELI", 10, 100)]), {})
    assert portfolio.totals(valued) == {
        "cost": 1000.0, "value": None, "gain": None, "gain_pct": None, "priced": False,
    }


# --- the rules ---------------------------------------------------------------


@pytest.mark.parametrize(
    "gain_pct, weight, monitor, expected",
    [
        (5.0, 0.10, None, portfolio.HOLD),                # a winner is left alone
        (config.ADD_DIP_PCT, 0.10, None, portfolio.ADD),  # the dip rule, at its threshold
        # A clean monitor pass is not itself an action: it falls through to the
        # book's own rules, where concentration still outranks the dip.
        (-0.50, 0.30, "HOLD", portfolio.TRIM),
        (-0.50, 0.30, "SELL", portfolio.SELL),            # the monitor outranks both
    ],
)
def test_recommend_is_hold_biased_and_defers_to_the_monitor(gain_pct, weight, monitor, expected):
    assert portfolio.recommend(gain_pct, weight, monitor) == expected


# --- persistence + round trip ------------------------------------------------


def test_import_and_read_back_through_db(con):
    assert portfolio.import_csv(con, CSV) == 2
    held = db.open_positions(con)
    assert list(held["ticker"]) == ["ITRN", "MELI"]  # open_positions orders by ticker
    assert held["entry_date"].notna().all()  # defaulted to today

    portfolio.import_csv(con, "ticker,shares,entry_price\nMELI,1,1\n", replace=True)
    assert list(db.positions(con)["ticker"]) == ["MELI"]  # a re-import, not a merge


def test_latest_monitor_action_wins_and_unchecked_tickers_stay_unjudged(con):
    db.add_monitoring_log(con, "MELI", [], "SELL", check_date=dt.date(2024, 1, 1))
    db.add_monitoring_log(con, "MELI", ["ROIC_DETERIORATION"], "REVIEW",
                          check_date=dt.date(2024, 6, 1))
    assert db.latest_monitor_action(con) == {"MELI": "REVIEW"}  # the newer check

    valued = portfolio.value(_held([("MELI", 10, 100), ("ITRN", 5, 200)]),
                             {"MELI": 300, "ITRN": 200}, db.latest_monitor_action(con))
    # ITRN was never checked, so nothing judged it — that is not a clean bill.
    assert list(valued["action"]) == [portfolio.REVIEW, portfolio.HOLD]


def test_snapshot_csv_round_trips_back_through_parse_csv():
    valued = portfolio.value(_held([("MELI", 10, 100)]), {"MELI": 300})
    rows = portfolio.parse_csv(portfolio.snapshot_csv(valued))
    assert rows[0]["ticker"] == "MELI" and rows[0]["shares"] == 10.0


def test_fetch_prices_uppercases_dedupes_and_degrades_when_yfinance_breaks(monkeypatch):
    """Offline is this suite's norm; a failed quote is a blank cell, not a raise."""
    class Boom:
        def __init__(self, ticker):
            raise RuntimeError("network is down")

    monkeypatch.setattr(portfolio.yf, "Ticker", Boom)
    assert portfolio.fetch_prices(["meli", "MELI"]) == {"MELI": None}
