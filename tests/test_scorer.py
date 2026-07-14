"""scorer.py — the quant rubric and the auto-exclusion rules."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from src import config, db, scorer
from tests.conftest import FakeTicker, frame

NO_METRICS = dict.fromkeys(
    ["revenue_cagr_3y", "gross_margin", "operating_margin", "fcf_margin",
     "debt_to_equity", "share_change_pct", "insider_pct", "_ocf", "_fcf"]
)


def m(**kw) -> dict:
    """A metrics dict with everything missing except what the test sets."""
    return {**NO_METRICS, **kw}


# --- _band ------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    (0.20, 3), (0.19, 2),          # at / below the top boundary
    (0.15, 2), (0.14, 1),
    (0.10, 1), (0.09, 0),
])
def test_band_higher_is_better_awards_points_at_each_boundary(value, expected):
    assert scorer._band(value, config.REVENUE_CAGR_BANDS) == expected


@pytest.mark.parametrize("value,expected", [
    (0.30, 2), (0.31, 1),          # at / above the top boundary
    (0.75, 1), (0.76, 0),
])
def test_band_lower_is_better_awards_points_at_each_boundary(value, expected):
    assert scorer._band(value, config.DEBT_TO_EQUITY_BANDS, lower_is_better=True) == expected


def test_band_of_missing_data_scores_zero():
    assert scorer._band(None, config.GROSS_MARGIN_BANDS) == 0
    assert scorer._band(None, config.DEBT_TO_EQUITY_BANDS, lower_is_better=True) == 0


# --- _cagr ------------------------------------------------------------------


def test_cagr_on_a_known_series():
    # 100 -> 200 over 2 years = 2 ** 0.5 - 1; a flat series is 0%
    assert scorer._cagr(pd.Series([200.0, 140.0, 100.0])) == pytest.approx(0.41421, abs=1e-4)
    assert scorer._cagr(pd.Series([100.0, 100.0])) == pytest.approx(0.0)


@pytest.mark.parametrize("series", [
    None,
    pd.Series([100.0], dtype=float),          # < 2 periods
    pd.Series([100.0, 0.0]),                  # non-positive oldest
    pd.Series([-50.0, 100.0]),                # non-positive newest
])
def test_cagr_returns_none_when_undefined(series):
    assert scorer._cagr(series) is None


# --- metrics / quant_score --------------------------------------------------


def test_metrics_flags_every_missing_field_as_a_warning(monkeypatch, empty_ticker):
    monkeypatch.setattr(scorer.yf, "Ticker", lambda t: empty_ticker)
    values, warnings = scorer.metrics("EMPTY")
    assert values["_fcf"] is None
    assert set(warnings) == {
        "REVENUE_CAGR_3Y", "GROSS_MARGIN", "OPERATING_MARGIN", "FCF_MARGIN",
        "DEBT_TO_EQUITY", "SHARE_CHANGE_PCT", "INSIDER_PCT",
    }


def test_metrics_derives_ratios_and_scales_debt_to_equity(monkeypatch, perfect_ticker):
    monkeypatch.setattr(scorer.yf, "Ticker", lambda t: perfect_ticker)
    values, warnings = scorer.metrics("AAA")
    assert warnings == []
    assert values["gross_margin"] == pytest.approx(0.60)
    assert values["operating_margin"] == pytest.approx(0.20)
    assert values["fcf_margin"] == pytest.approx(0.20)      # (50 - 10) / 200
    assert values["debt_to_equity"] == pytest.approx(0.20)  # yfinance reports percent
    assert values["_fcf"] == pytest.approx(40.0)


def test_quant_score_of_a_perfect_ticker_is_the_maximum(monkeypatch, perfect_ticker):
    monkeypatch.setattr(scorer.yf, "Ticker", lambda t: perfect_ticker)
    values, _ = scorer.metrics("AAA")
    assert scorer.quant_score(values) == config.QUANT_MAX_SCORE == 14


def test_quant_score_of_a_ticker_with_no_data_is_zero():
    assert scorer.quant_score(m()) == 0


def test_quant_score_sums_the_bands_of_a_partial_ticker():
    # 0.12 CAGR (1) + 0.55 gross (2) + 0.12 insider (1); the rest missing = 0
    assert scorer.quant_score(
        m(revenue_cagr_3y=0.12, gross_margin=0.55, insider_pct=0.12)
    ) == 4


# --- exclusions -------------------------------------------------------------


@pytest.mark.parametrize("metrics,expected", [
    (m(share_change_pct=0.0501), ["CHRONIC_DILUTER"]),          # just over 5%/yr
    (m(share_change_pct=config.CHRONIC_DILUTER_PCT), []),       # at the threshold: safe
    (m(_fcf=-1.0, _ocf=-1.0), ["CASH_BURNER"]),
    (m(_fcf=-1.0, _ocf=1.0), []),                               # OCF positive: not a burner
    (m(debt_to_equity=3.01), ["EXCESSIVE_LEVERAGE"]),
    (m(debt_to_equity=config.EXCESSIVE_LEVERAGE_DE), []),       # at the threshold: safe
    (m(revenue_cagr_3y=-0.01), ["REVENUE_DECLINE"]),
    (m(revenue_cagr_3y=config.REVENUE_DECLINE_CAGR), []),       # flat is not a decline
    (m(share_change_pct=0.2, debt_to_equity=4.0), ["CHRONIC_DILUTER", "EXCESSIVE_LEVERAGE"]),
])
def test_exclusions_fire_exactly_at_their_thresholds(metrics, expected):
    assert [r for r, _ in scorer.exclusions_for(metrics)] == expected


def test_missing_data_never_excludes_a_ticker():
    """PRD §2.4 — flag, don't auto-delete. All-None metrics must produce no exclusion."""
    assert scorer.exclusions_for(m()) == []


@pytest.mark.parametrize("metrics", [
    m(_fcf=-1.0),                 # OCF unknown
    m(_ocf=-1.0),                 # FCF unknown
])
def test_cash_burner_needs_both_fcf_and_ocf(metrics):
    assert scorer.exclusions_for(metrics) == []


# --- score_ticker -----------------------------------------------------------


def test_score_ticker_persists_and_advances_a_qualifying_ticker(con, monkeypatch, perfect_ticker):
    monkeypatch.setattr(scorer.yf, "Ticker", lambda t: perfect_ticker)
    db.replace_universe(con, [{"ticker": "AAA"}])
    result = scorer.score_ticker(con, "AAA", dt.date(2026, 1, 5))

    assert result == {"ticker": "AAA", "score": 14, "warnings": [], "exclusions": []}
    row = con.execute(
        "SELECT quant_score, gross_margin, data_warnings, total_score FROM scores"
    ).fetchone()
    assert row == (14, pytest.approx(0.60), None, 14)
    assert int(db.get_universe(con).loc[0, "stage"]) == 2


def test_score_ticker_is_idempotent_on_a_rerun(con, monkeypatch, perfect_ticker):
    monkeypatch.setattr(scorer.yf, "Ticker", lambda t: perfect_ticker)
    db.replace_universe(con, [{"ticker": "AAA"}])
    scorer.score_ticker(con, "AAA", dt.date(2026, 1, 5))
    scorer.score_ticker(con, "AAA", dt.date(2026, 1, 5))
    assert con.execute("SELECT count(*) FROM scores").fetchone()[0] == 1


def test_score_ticker_records_warnings_and_does_not_advance_an_empty_ticker(
    con, monkeypatch, empty_ticker
):
    monkeypatch.setattr(scorer.yf, "Ticker", lambda t: empty_ticker)
    db.replace_universe(con, [{"ticker": "EMPTY"}])
    result = scorer.score_ticker(con, "EMPTY", dt.date(2026, 1, 5))

    assert result["score"] == 0 and result["exclusions"] == []
    warnings = con.execute("SELECT data_warnings FROM scores").fetchone()[0]
    assert "GROSS_MARGIN" in warnings
    assert int(db.get_universe(con).loc[0, "stage"]) == 1  # never advanced, never excluded


def test_score_ticker_excludes_a_chronic_diluter_and_leaves_it_at_stage_1(con, monkeypatch):
    diluter = FakeTicker(
        info={"debtToEquity": 10.0, "heldPercentInsiders": 0.20},
        income=frame({"Total Revenue": [200.0, 140.0, 100.0], "Gross Profit": [120.0, 84.0, 60.0],
                      "Operating Income": [40.0, 28.0, 20.0]}),
        cashflow=frame({"Operating Cash Flow": [50.0, 35.0, 25.0],
                        "Capital Expenditure": [-10.0, -7.0, -5.0]}),
        balance=frame({"Ordinary Shares Number": [121.0, 110.0, 100.0]}),  # +10%/yr
    )
    monkeypatch.setattr(scorer.yf, "Ticker", lambda t: diluter)
    db.replace_universe(con, [{"ticker": "PLTX"}])
    result = scorer.score_ticker(con, "PLTX", dt.date(2026, 1, 5))

    assert result["exclusions"] == ["CHRONIC_DILUTER"]
    assert db.get_universe(con).loc[0, "status"] == "excluded"
    assert int(db.get_universe(con).loc[0, "stage"]) == 1
    assert con.execute("SELECT reason, stage FROM exclusions").fetchone() == ("CHRONIC_DILUTER", 2)
