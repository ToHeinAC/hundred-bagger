"""Phase 3 monitoring — the sell-trigger table, the recommendation, and the 8-K round-trip.

PRD §12: "each sell trigger in the table has a test that fires it". Each one also
has a test that it does *not* fire on absent data — the invariant that holds at
every other stage holds here too (PRD §2.4).
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from src import config, db, monitor, triggers

YEARS = (2023, 2024, 2025)


def table(**series: dict[int, float]) -> dict:
    """A year-keyed XBRL fact table, as `roic.CHAINS` builds it."""
    return series


def healthy() -> dict:
    """20% ROIC, growing, no dilution, solvent. Trips nothing."""
    return table(
        revenue={2023: 100.0, 2024: 120.0, 2025: 150.0},
        ebit={2023: 20.0, 2024: 25.0, 2025: 32.0},
        equity={2023: 80.0, 2024: 90.0, 2025: 100.0},
        assets={2023: 100.0, 2024: 110.0, 2025: 130.0},
        assets_current={2023: 60.0, 2024: 70.0, 2025: 80.0},
        liabilities={2023: 20.0, 2024: 20.0, 2025: 30.0},
        liabilities_current={2023: 10.0, 2024: 10.0, 2025: 15.0},
        retained_earnings={2023: 40.0, 2024: 50.0, 2025: 60.0},
        shares={2023: 1000.0, 2024: 1000.0, 2025: 1000.0},
    )


def codes(fired: list[tuple[str, str]]) -> list[str]:
    return [code for code, _ in fired]


# --- the triggers fire ------------------------------------------------------


def test_a_healthy_company_trips_nothing():
    assert triggers.fired(healthy(), market_cap=500.0) == []


def test_roic_deterioration_fires_after_two_years_below_the_floor():
    t = healthy() | {"ebit": {2023: 20.0, 2024: 2.0, 2025: 2.0}}
    assert "ROIC_DETERIORATION" in codes(triggers.fired(t))


def test_one_bad_year_is_not_a_sell():
    """Selling a compounder on a single soft year is how you lose the 100-bagger."""
    t = healthy() | {"ebit": {2023: 20.0, 2024: 25.0, 2025: 2.0}}
    assert "ROIC_DETERIORATION" not in codes(triggers.fired(t))


def test_revenue_decline_fires_after_two_falling_years():
    t = healthy() | {"revenue": {2023: 150.0, 2024: 120.0, 2025: 100.0}}
    assert "REVENUE_DECLINE" in codes(triggers.fired(t))


def test_a_single_down_year_is_not_a_revenue_decline():
    t = healthy() | {"revenue": {2023: 100.0, 2024: 150.0, 2025: 120.0}}
    assert "REVENUE_DECLINE" not in codes(triggers.fired(t))


def test_margin_compression_fires_on_a_large_drop():
    t = healthy() | {
        "revenue": {2023: 100.0, 2024: 110.0, 2025: 120.0},
        "ebit": {2023: 20.0, 2024: 15.0, 2025: 6.0},  # 20% -> 5%
    }
    assert "MARGIN_COMPRESSION" in codes(triggers.fired(t))


def test_a_small_margin_slip_is_not_compression():
    t = healthy() | {
        "revenue": {2023: 100.0, 2024: 110.0, 2025: 120.0},
        "ebit": {2023: 20.0, 2024: 21.0, 2025: 22.0},  # 20% -> 18.3%
    }
    assert "MARGIN_COMPRESSION" not in codes(triggers.fired(t))


def test_dilution_fires_on_a_share_count_spike():
    t = healthy() | {"shares": {2024: 1000.0, 2025: 1200.0}}
    assert "DILUTION" in codes(triggers.fired(t))


def test_a_buyback_is_not_dilution():
    t = healthy() | {"shares": {2024: 1000.0, 2025: 900.0}}
    assert "DILUTION" not in codes(triggers.fired(t))


def test_distress_zone_fires_on_a_low_altman_z():
    t = healthy() | {"liabilities": {2025: 5000.0}, "retained_earnings": {2025: -500.0}}
    assert "DISTRESS_ZONE" in codes(triggers.fired(t, market_cap=1.0))


def test_distress_needs_a_market_cap_xbrl_cannot_supply():
    t = healthy() | {"liabilities": {2025: 5000.0}, "retained_earnings": {2025: -500.0}}
    assert "DISTRESS_ZONE" not in codes(triggers.fired(t, market_cap=None))


# --- no trigger fires on missing data ---------------------------------------


def test_an_empty_fact_table_trips_nothing():
    """The invariant, in the one place it would be most tempting to break: a
    company we could not measure is not a company that failed."""
    assert triggers.fired(table(), market_cap=500.0) == []


def test_a_single_year_of_history_trips_no_trend_rule():
    single = table(revenue={2025: 100.0}, ebit={2025: 1.0}, equity={2025: 100.0},
                   assets={2025: 100.0}, shares={2025: 1000.0})
    assert triggers.fired(single) == []


def test_a_fired_trigger_reports_the_number_that_fired_it():
    t = healthy() | {"ebit": {2023: 20.0, 2024: 2.0, 2025: 2.0}}
    detail = dict(triggers.fired(t))["ROIC_DETERIORATION"]
    assert "%" in detail and "floor" in detail


# --- recommendation ---------------------------------------------------------


@pytest.mark.parametrize(("flags", "expected"), [
    ([], "HOLD"),
    (["DILUTION"], "REVIEW"),
    (["DILUTION", "REVENUE_DECLINE"], "TRIM"),
    (["DILUTION", "REVENUE_DECLINE", "MARGIN_COMPRESSION"], "SELL"),
    (["A", "B", "C", "D", "E"], "SELL"),
])
def test_mechanical_flags_accumulate(flags, expected):
    assert triggers.recommend(flags, []) == expected


def test_one_red_flag_is_categorical():
    """A restatement is a sell however healthy the arithmetic looks."""
    assert triggers.recommend([], ["RESTATEMENT"]) == "SELL"


# --- validate ---------------------------------------------------------------


def test_validate_accepts_known_codes_and_dedupes_them():
    flags, notes = monitor.validate(
        {"red_flags": ["RESTATEMENT", "RESTATEMENT", "GOING_CONCERN"], "notes": " x "}
    )
    assert flags == ["RESTATEMENT", "GOING_CONCERN"]
    assert notes == "x"


def test_validate_accepts_an_empty_verdict():
    assert monitor.validate({"red_flags": [], "notes": "nothing material"})[0] == []


def test_validate_rejects_an_invented_code():
    """A code outside the vocabulary would land in `flags` and match nothing the
    user ever greps for."""
    with pytest.raises(ValueError, match="BAD_VIBES"):
        monitor.validate({"red_flags": ["BAD_VIBES"]})


def test_validate_rejects_a_bare_string():
    with pytest.raises(ValueError, match="expected a list"):
        monitor.validate({"red_flags": "RESTATEMENT"})


# --- check → save round-trip ------------------------------------------------


@pytest.fixture
def checked(con, monkeypatch):
    """A ticker whose mechanical check has already run, with one trigger fired."""
    diluting = healthy() | {"shares": {2024: 1000.0, 2025: 1200.0}}
    monkeypatch.setattr(monitor, "_table", lambda ticker: diluting)
    monkeypatch.setattr(monitor.filings, "write_recent_8k", lambda t, d: None)
    monitor.check_ticker(con, "CRVL", market_cap=500.0)
    return con


def test_check_writes_the_log_and_raises_a_sell_alert(checked):
    log = db.monitoring_log(checked, "CRVL")
    assert json.loads(log.iloc[0]["flags"]) == ["DILUTION"]
    assert log.iloc[0]["recommended_action"] == "REVIEW"

    alerts = db.alerts(checked)
    assert len(alerts) == 1
    assert alerts.iloc[0]["alert_type"] == "sell"


def test_save_merges_red_flags_into_the_mechanical_verdict(checked):
    result = monitor.save_ticker(
        checked, "CRVL", {"red_flags": ["GOING_CONCERN"], "notes": "auditor doubt"}
    )
    assert result["action"] == "SELL"
    assert result["flags"] == ["DILUTION", "GOING_CONCERN"]

    log = db.monitoring_log(checked, "CRVL")
    assert len(log) == 1  # merged into today's row, not appended
    assert "auditor doubt" in log.iloc[0]["notes"]
    assert "DILUTION" in log.iloc[0]["notes"]


def test_save_raises_a_high_severity_red_flag_alert(checked):
    monitor.save_ticker(checked, "CRVL", {"red_flags": ["RESTATEMENT"], "notes": "Item 4.02"})
    red = db.alerts(checked)
    red = red[red["alert_type"] == "red_flag"]
    assert len(red) == 1
    assert red.iloc[0]["severity"] == "HIGH"


def test_a_clean_8k_leaves_the_mechanical_verdict_standing(checked):
    result = monitor.save_ticker(checked, "CRVL", {"red_flags": [], "notes": "routine"})
    assert result["action"] == "REVIEW"
    assert result["flags"] == ["DILUTION"]


def test_saving_twice_does_not_double_count_the_red_flags(checked):
    monitor.save_ticker(checked, "CRVL", {"red_flags": ["RESTATEMENT"], "notes": "x"})
    result = monitor.save_ticker(checked, "CRVL", {"red_flags": ["RESTATEMENT"], "notes": "x"})
    assert result["flags"] == ["DILUTION", "RESTATEMENT"]


def test_save_without_a_check_refuses_rather_than_writing_half_a_verdict(con):
    with pytest.raises(ValueError, match="Run `check`"):
        monitor.save_ticker(con, "CRVL", {"red_flags": ["RESTATEMENT"]})
