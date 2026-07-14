"""Phase 3 entry signals — cluster detection, valuation gates, strength, persistence.

No network: `signals.filings` and `signals.yf` are monkeypatched. The conftest
`no_network` fixture turns any real socket into a failure.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src import config, db, signals

TODAY = dt.date(2026, 7, 14)


def buy(day: int, insider: str, value: float = 60_000.0, code: str = "P") -> dict:
    """One Form 4 trade, `day` days before TODAY."""
    return {
        "filed_date": TODAY - dt.timedelta(days=day),
        "transaction_date": TODAY - dt.timedelta(days=day),
        "insider_name": insider,
        "insider_title": "Director",
        "transaction_type": code,
        "shares": 1000,
        "price": value / 1000,
        "value": value,
    }


# --- cluster ----------------------------------------------------------------


def test_three_insiders_inside_the_window_are_a_cluster():
    c = signals.cluster([buy(30, "A"), buy(20, "B"), buy(10, "C")])
    assert c["insiders"] == 3
    assert c["value"] == 180_000.0
    assert c["days"] == 20


def test_two_insiders_are_not_a_cluster_however_much_they_spend():
    assert signals.cluster([buy(10, "A", 5_000_000.0), buy(9, "B", 5_000_000.0)]) is None


def test_one_insider_filing_repeatedly_is_not_a_cluster():
    """Distinct *people*, not distinct filings — otherwise one director's
    conviction, split across four Form 4s, manufactures a cluster."""
    same = [buy(d, "A") for d in (30, 25, 20, 15)]
    assert signals.cluster(same) is None


def test_a_cluster_needs_the_aggregate_value_too():
    small = [buy(d, name, value=1_000.0) for d, name in ((30, "A"), (20, "B"), (10, "C"))]
    assert signals.cluster(small) is None


def test_buys_spread_beyond_the_window_are_not_a_cluster():
    spread = config.CLUSTER_WINDOW_DAYS + 40
    assert signals.cluster([buy(spread, "A"), buy(spread // 2, "B"), buy(0, "C")]) is None


def test_a_cluster_is_found_wherever_it_sits_in_the_lookback():
    """Not just in the most recent window: an old cluster is still a cluster."""
    old = [buy(d, n) for d, n in ((170, "A"), (165, "B"), (160, "C"))]
    c = signals.cluster([*old, buy(1, "D")])
    assert c is not None and c["insiders"] == 3


def test_in_window_marks_exactly_the_winning_windows_buys():
    buys = [buy(30, "A"), buy(20, "B"), buy(10, "C")]
    c = signals.cluster(buys)
    assert all(signals.in_window(b, c["start"]) for b in buys)
    assert not signals.in_window(buy(config.CLUSTER_WINDOW_DAYS + 31, "Z"), c["start"])


# --- valuation gates --------------------------------------------------------


def test_valuation_computes_the_three_ratios():
    v = signals.valuation({
        "marketCap": 100.0, "freeCashflow": 10.0,
        "enterpriseValue": 120.0, "ebitda": 12.0, "trailingPegRatio": 1.5,
    })
    assert v == {"p_fcf": 10.0, "ev_ebitda": 10.0, "peg": 1.5}


def test_negative_free_cash_flow_is_unknown_not_cheap():
    """A negative denominator would otherwise produce a low, passing P/FCF."""
    v = signals.valuation({"marketCap": 100.0, "freeCashflow": -10.0})
    assert v["p_fcf"] is None


def test_a_missing_ratio_is_never_a_passed_gate():
    assert signals.gates({"p_fcf": None, "ev_ebitda": None, "peg": None}) == {
        "p_fcf": None, "ev_ebitda": None, "peg": None,
    }


def test_gates_pass_and_fail_on_their_limits():
    g = signals.gates({"p_fcf": config.MAX_P_FCF, "ev_ebitda": 99.0, "peg": None})
    assert g == {"p_fcf": True, "ev_ebitda": False, "peg": None}


def test_price_zone_places_the_price_in_its_52_week_range():
    info = {"currentPrice": 25.0, "fiftyTwoWeekLow": 20.0, "fiftyTwoWeekHigh": 40.0}
    assert signals.price_zone(info) == pytest.approx(0.25)


def test_price_zone_is_none_without_a_range():
    assert signals.price_zone({"currentPrice": 25.0}) is None


# --- strength ---------------------------------------------------------------

CHEAP = {"p_fcf": True, "ev_ebitda": True, "peg": None}
EXPENSIVE = {"p_fcf": False, "ev_ebitda": True, "peg": None}
UNKNOWN = {"p_fcf": None, "ev_ebitda": None, "peg": None}


def test_cluster_plus_valuation_is_high():
    assert signals.strength(True, CHEAP, 0.3) == "HIGH"


def test_a_cluster_into_an_expensive_stock_is_only_medium():
    assert signals.strength(True, EXPENSIVE, 0.3) == "MEDIUM"


def test_cheapness_alone_never_earns_a_high():
    """Price is not a catalyst. Without insiders buying, the ceiling is MEDIUM."""
    assert signals.strength(False, CHEAP, 0.1) == "MEDIUM"


def test_cheap_but_near_the_52_week_high_is_only_low():
    assert signals.strength(False, CHEAP, 0.9) == "LOW"


def test_an_unmeasurable_valuation_is_not_a_pass():
    assert signals.strength(False, UNKNOWN, 0.1) is None


def test_one_failed_gate_sinks_the_valuation():
    assert signals.strength(False, EXPENSIVE, 0.1) is None


# --- check_ticker (end to end, mocked) --------------------------------------


class FakeYF:
    def __init__(self, info):
        self._info = info

    def Ticker(self, ticker):  # noqa: N802 — mirrors yfinance's API
        return type("T", (), {"info": self._info})()


@pytest.fixture
def cheap_info() -> dict:
    return {
        "marketCap": 100.0, "freeCashflow": 10.0,
        "enterpriseValue": 100.0, "ebitda": 12.0,
        "currentPrice": 22.0, "fiftyTwoWeekLow": 20.0, "fiftyTwoWeekHigh": 40.0,
    }


def _patch(monkeypatch, transactions: list[dict], info: dict) -> None:
    monkeypatch.setattr(signals.filings, "insider_transactions", lambda t, d: transactions)
    monkeypatch.setattr(signals, "yf", FakeYF(info))


def test_a_synthetic_cluster_buy_produces_a_high_signal(con, monkeypatch, cheap_info):
    """PRD §12 Phase 3 validation, verbatim."""
    _patch(monkeypatch, [buy(30, "A"), buy(20, "B"), buy(10, "C")], cheap_info)

    result = signals.check_ticker(con, "CRVL")

    assert result["strength"] == "HIGH"
    assert "cluster buy (3 insiders, $180,000, 20 days)" in result["message"]
    assert result["alerted"] is True

    alerts = db.alerts(con)
    assert len(alerts) == 1
    assert alerts.iloc[0]["alert_type"] == "buy"
    assert alerts.iloc[0]["severity"] == "HIGH"


def test_the_cluster_buys_are_flagged_on_the_persisted_events(con, monkeypatch, cheap_info):
    _patch(monkeypatch, [buy(30, "A"), buy(20, "B"), buy(10, "C")], cheap_info)
    signals.check_ticker(con, "CRVL")

    events = db.insider_events(con, "CRVL")
    assert len(events) == 3
    assert events["is_cluster_buy"].all()


def test_grants_and_option_exercises_are_not_buys(con, monkeypatch, cheap_info):
    """Three insiders 'acquiring' shares — all of it compensation, none of it conviction."""
    comp = [buy(30, "A", code="A"), buy(20, "B", code="M"), buy(10, "C", code="A")]
    _patch(monkeypatch, comp, cheap_info)

    result = signals.check_ticker(con, "CRVL")

    assert result["cluster"] is False
    assert result["buys"] == 0
    assert db.insider_events(con, "CRVL").empty


def test_a_low_signal_raises_no_alert(con, monkeypatch, cheap_info):
    """LOW is 'nothing broke', not news — alerting on it trains the user to ignore the feed."""
    _patch(monkeypatch, [], {**cheap_info, "currentPrice": 38.0})

    result = signals.check_ticker(con, "CRVL")

    assert result["strength"] == "LOW"
    assert result["alerted"] is False
    assert db.alerts(con).empty


def test_rechecking_a_ticker_does_not_duplicate_its_events_or_alerts(con, monkeypatch, cheap_info):
    _patch(monkeypatch, [buy(30, "A"), buy(20, "B"), buy(10, "C")], cheap_info)
    signals.check_ticker(con, "CRVL")
    signals.check_ticker(con, "CRVL")

    assert len(db.insider_events(con, "CRVL")) == 3
    assert len(db.alerts(con)) == 1
