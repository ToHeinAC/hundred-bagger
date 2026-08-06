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

CSV = "ticker,shares,entry_price,currency\nMELI,10,100,USD\nITRN,5,200,EUR\n"

BAD_CSV = [
    ("ticker,shares\nMELI,10\n", "missing required column"),        # no entry_price
    ("ticker,shares,entry_price\nMELI,ten,100\n", "must be numbers"),
    ("ticker,shares,entry_price\nMELI,10,2024\nITRN,5,x\n", r"Row 3 \(ITRN\)"),
    ("ticker,shares,entry_price,entry_date\nMELI,10,1,15/01/24\n", "YYYY-MM-DD"),
    ("ticker,shares,entry_price,currency\nMELI,10,1,GBP\n", "currency must be one of"),
    ("ticker,shares,entry_price\n\n", "no positions"),
]


def _held(rows: list[tuple]) -> pd.DataFrame:
    """A holdings frame shaped like db.open_positions: (ticker, shares, entry_price)
    and optionally its currency."""
    cols = ["ticker", "shares", "entry_price", "currency"][:len(rows[0])]
    return pd.DataFrame(rows, columns=cols)


# --- CSV ---------------------------------------------------------------------


def test_parse_csv_reads_positions_aliases_dates_and_skips_blanks():
    """One happy path covering every accepted spelling. `quantity`/`buy_price`
    are the old tracker's names — its exports must still import."""
    rows = portfolio.parse_csv(
        " Ticker , QUANTITY ,Buy_Price, currency , entry_date ,thesis\n"
        " meli ,10,100, eur ,2024-01-15,flywheel\n"
        "\n"
        "ITRN,5,200,,,\n"
    )
    assert rows == [
        {"ticker": "MELI", "shares": 10.0, "entry_price": 100.0, "currency": "EUR",
         "entry_date": dt.date(2024, 1, 15), "thesis": "flywheel"},
        # The column is optional; an omitted currency is USD, not a guess.
        {"ticker": "ITRN", "shares": 5.0, "entry_price": 200.0, "currency": "USD",
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

    # With nothing priced at all, the totals say so rather than guessing zero.
    assert portfolio.totals(portfolio.value(_held([("MELI", 10, 100)]), {})) == {
        "cost": 1000.0, "value": None, "gain": None, "gain_pct": None, "priced": False,
    }


# --- currency ----------------------------------------------------------------

MIXED = [("MELI", 10, 100, "USD"), ("1VW.F", 10, 100, "EUR")]
RATE = 0.80  # EUR per USD


def test_convert_states_a_mixed_book_in_one_currency_and_leaves_the_ratios_alone(monkeypatch):
    """A currency is a unit, not a return: only levels move.

    Both directions, the no-rate degradation and the `needs_fx` gate are one
    test because they are one behaviour, and the suite is budget-bound.
    """
    held, prices = portfolio.convert(_held(MIXED), {"MELI": 200, "1VW.F": 200},
                                     "EUR", RATE)
    assert list(held["entry_price"]) == [80.0, 100.0]  # the USD row, and only it
    assert prices == {"MELI": 160.0, "1VW.F": 200.0}
    assert list(held["native_currency"]) == ["USD", "EUR"]
    assert set(held["currency"]) == {"EUR"}

    valued = portfolio.value(held, prices)
    assert list(valued["multiple"]) == [2.0, 2.0]  # untouched by the conversion
    assert portfolio.totals(valued)["cost"] == 1800.0  # 800 EUR + 1000 EUR

    # The other direction is the inverse rate, not a second table.
    back, _ = portfolio.convert(_held(MIXED), {}, "USD", RATE)
    assert list(back["entry_price"]) == [100.0, 125.0]

    # No rate is not 1.0: a book that reads native is honest, one converted at
    # an assumed parity is quietly wrong.
    class Boom:
        def __init__(self, ticker):
            raise RuntimeError("network is down")

    monkeypatch.setattr(portfolio.yf, "Ticker", Boom)
    assert portfolio.fetch_fx_rate() is None

    native, unchanged = portfolio.convert(_held(MIXED), {"MELI": 200}, "EUR", None)
    assert list(native["entry_price"]) == [100.0, 100.0] and unchanged == {"MELI": 200}
    assert list(native["native_currency"]) == ["USD", "EUR"]

    # ... and a book already wholly in the display currency never asks for one.
    assert portfolio.needs_fx(_held(MIXED), "EUR") is True
    assert portfolio.needs_fx(_held([("MELI", 10, 100, "EUR")]), "EUR") is False


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
    assert list(held["currency"]) == ["EUR", "USD"]  # each row keeps its own
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
    held, prices = portfolio.convert(_held(MIXED), {"MELI": 300, "1VW.F": 300},
                                     "EUR", RATE)
    rows = portfolio.parse_csv(portfolio.snapshot_csv(portfolio.value(held, prices)))
    assert rows[0]["ticker"] == "MELI" and rows[0]["shares"] == 10.0
    # The snapshot is in the *display* currency, so re-importing it is not a
    # book that has silently changed value.
    assert [r["currency"] for r in rows] == ["EUR", "EUR"]


def test_the_yahoo_surface_degrades_and_its_summary_block_formats(monkeypatch):
    """Offline is this suite's norm; a failed fetch is a blank cell, not a raise.

    All four fetchers and the block they feed are one test because the suite is
    budget-bound (AGENTS §5.3) and they share one contract: never propagate.
    """
    class Boom:
        def __init__(self, ticker):
            raise RuntimeError("network is down")

    monkeypatch.setattr(portfolio.yf, "Ticker", Boom)
    assert portfolio.fetch_prices(["meli", "MELI"]) == {"MELI": None}
    assert portfolio.fetch_profile("MELI") == {} and portfolio.fetch_history("MELI") is None

    rows = dict(portfolio.profile_rows({
        "currency": "USD", "marketCap": 556_302_000_000, "beta": 2.31,
        "trailingEps": -1.23, "earningsTimestampStart": 1786320000,
        "dividendRate": 0.08, "dividendYield": 0.92,
    }))
    assert rows["Market cap (intraday)"] == "556.30B USD"
    assert rows["Beta (5Y monthly)"] == "2.31"
    assert rows["EPS (TTM)"] == "-1.2300"
    assert rows["Earnings date (est.)"] == "2026-08-10"
    assert rows["Forward dividend & yield"] == "0.08 (0.92%)"
    # An absent field is Yahoo's own `--`, never a zero that reads like a fact.
    assert rows["PE ratio (TTM)"] == rows["1-year target est."] == portfolio.NA
