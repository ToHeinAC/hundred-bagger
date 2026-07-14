"""Stage 3 — XBRL extraction, ROIC/Piotroski/Altman arithmetic, scoring, persistence.

No network: `xbrl.company_facts` is monkeypatched everywhere. The conftest
`no_network` fixture turns any real socket into a failure.
"""

from __future__ import annotations

import pytest

from src import config, db, fundamentals, roic, xbrl

# --- xbrl extraction --------------------------------------------------------


def _fact(val: float, end: str, form: str = "10-K", start: str | None = None, filed: str = "2024-01-01") -> dict:
    row = {"val": val, "end": end, "form": form, "filed": filed}
    if start:
        row["start"] = start
    return row


def _facts(**tags: list[dict]) -> dict:
    return {"facts": {"us-gaap": {t: {"units": {"USD": rows}} for t, rows in tags.items()}}}


def test_annual_falls_back_to_a_later_tag_in_the_chain():
    facts = _facts(Revenues=[_fact(500.0, "2023-12-31")])
    # RevenueFromContractWithCustomer... is first in the chain but absent here.
    assert xbrl.annual(facts, xbrl.REVENUE) == {2023: 500.0}


def test_annual_ignores_a_retired_tag_the_company_migrated_away_from():
    """The AMPH trap: a stale series is worse than no series, because it looks fine.

    AMPH moved to the NCI-inclusive equity tag in 2022 but still reports the
    retired `StockholdersEquity` for 2011-2021. Taking the first chain entry with
    data would compute today's ROIC from 2021 numbers and say nothing about it.
    """
    facts = _facts(
        StockholdersEquity=[_fact(100.0, "2020-12-31"), _fact(110.0, "2021-12-31")],
        StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest=[
            _fact(110.0, "2021-12-31"), _fact(200.0, "2023-12-31"),
        ],
    )
    assert max(xbrl.annual(facts, xbrl.EQUITY)) == 2023


def test_annual_ignores_quarterly_durations_and_non_annual_forms():
    facts = _facts(
        Revenues=[
            _fact(100.0, "2023-03-31", start="2023-01-01"),  # a quarter
            _fact(400.0, "2023-12-31", start="2023-01-01"),  # a full year
            _fact(999.0, "2023-12-31", form="10-Q"),
        ]
    )
    assert xbrl.annual(facts, xbrl.REVENUE) == {2023: 400.0}


def test_annual_prefers_the_latest_filed_restatement():
    facts = _facts(
        Assets=[
            _fact(100.0, "2023-12-31", filed="2024-02-01"),
            _fact(120.0, "2023-12-31", filed="2025-02-01"),  # restated, filed later
        ]
    )
    assert xbrl.annual(facts, xbrl.ASSETS) == {2023: 120.0}


def test_annual_returns_empty_when_no_tag_in_the_chain_reports():
    assert xbrl.annual(_facts(), xbrl.EBIT) == {}


def test_missing_sec_user_agent_fails_loudly(monkeypatch):
    monkeypatch.setattr(config, "SEC_USER_AGENT", "")
    with pytest.raises(xbrl.SecError, match="SEC_USER_AGENT"):
        xbrl._headers()


# --- ROIC -------------------------------------------------------------------


@pytest.fixture
def table() -> dict:
    """A clean compounder: EBIT 200 on invested capital of 1000 -> ROIC 15.8%."""
    return {
        "ebit": {2021: 100.0, 2022: 150.0, 2023: 200.0},
        "equity": {2021: 700.0, 2022: 800.0, 2023: 900.0},
        "long_term_debt": {2021: 200.0, 2022: 200.0, 2023: 200.0},
        "cash": {2021: 100.0, 2022: 100.0, 2023: 100.0},
        "pretax": {2021: 100.0, 2022: 150.0, 2023: 200.0},
        "tax": {2021: 20.0, 2022: 30.0, 2023: 40.0},
        "assets": {2021: 1000.0, 2022: 1100.0, 2023: 1200.0},
        "liabilities": {2021: 300.0, 2022: 300.0, 2023: 300.0},
        "assets_current": {2021: 400.0, 2022: 500.0, 2023: 600.0},
        "liabilities_current": {2021: 200.0, 2022: 200.0, 2023: 200.0},
        "retained_earnings": {2021: 300.0, 2022: 400.0, 2023: 500.0},
        "revenue": {2021: 800.0, 2022: 1000.0, 2023: 1400.0},
        "gross_profit": {2021: 400.0, 2022: 520.0, 2023: 770.0},
        "net_income": {2021: 80.0, 2022: 120.0, 2023: 160.0},
        "cfo": {2021: 100.0, 2022: 150.0, 2023: 200.0},
        "depreciation": {2021: 20.0, 2022: 25.0, 2023: 30.0},
        "shares": {2021: 100.0, 2022: 100.0, 2023: 99.0},
    }


def test_roic_is_nopat_over_invested_capital(table):
    # invested = equity 900 + debt 200 - cash 100 = 1000; tax rate 40/200 = 20%
    assert fundamentals.roic(table, 2023) == pytest.approx(200.0 * 0.8 / 1000.0)


def test_roic_falls_back_to_the_statutory_tax_rate_when_the_effective_one_is_absurd(table):
    table["tax"][2023] = -500.0  # a huge one-off credit; rate would be negative
    assert fundamentals.roic(table, 2023) == pytest.approx(200.0 * (1 - config.DEFAULT_TAX_RATE) / 1000.0)


def test_roic_is_none_when_invested_capital_is_negative(table):
    table["equity"][2023] = -500.0
    assert fundamentals.roic(table, 2023) is None


def test_roic_median_uses_the_last_three_years(table):
    assert fundamentals.roic_median(table) == pytest.approx(fundamentals.roic(table, 2022))


def test_piotroski_scores_nine_for_an_improving_company(table):
    assert fundamentals.piotroski_f(table, 2023) == 9


def test_piotroski_is_none_without_a_comparison_year(table):
    assert fundamentals.piotroski_f(table, 2021) is None


def test_piotroski_signal_is_not_awarded_on_missing_data(table):
    del table["cfo"][2023]  # CFO-positive and accrual-quality signals both fail
    assert fundamentals.piotroski_f(table, 2023) == 7


def test_altman_z_needs_a_market_cap(table):
    assert fundamentals.altman_z(table, 2023, None) is None
    assert fundamentals.altman_z(table, 2023, 900_000_000) > config.ALTMAN_Z_DISTRESS


# --- scoring and exclusions -------------------------------------------------


def test_roic_score_sums_the_three_bands():
    m = {"roic_3y_median": 0.16, "piotroski_f": 7, "altman_z": 3.5}  # 4 + 3 + 2
    assert roic.roic_score(m) == 9


def test_roic_score_of_an_uncomputable_ticker_is_zero_not_an_error():
    assert roic.roic_score({"roic_3y_median": None, "piotroski_f": None, "altman_z": None}) == 0


def test_asset_bloat_fires_when_assets_outrun_ebitda():
    m = {"asset_cagr": 0.30, "ebitda_cagr": 0.05, "altman_z": 5.0}
    assert [r for r, _ in roic.exclusions_for(m)] == ["ASSET_BLOAT"]


def test_distress_zone_fires_below_the_altman_threshold():
    m = {"asset_cagr": None, "ebitda_cagr": None, "altman_z": 1.2}
    assert [r for r, _ in roic.exclusions_for(m)] == ["DISTRESS_ZONE"]


def test_no_exclusion_ever_fires_on_a_missing_metric():
    assert roic.exclusions_for({"asset_cagr": None, "ebitda_cagr": None, "altman_z": None}) == []


# --- persistence ------------------------------------------------------------


def _stage_2_ticker(con, ticker: str = "CRVL") -> None:
    db.replace_universe(con, [{"ticker": ticker, "name": "Test", "market_cap": 900_000_000}])
    db.set_stage(con, [ticker], 2)


def test_score_ticker_persists_and_advances_at_the_gate(con, table, monkeypatch):
    _stage_2_ticker(con)
    monkeypatch.setattr(roic, "metrics", lambda t, cap: ({
        "roic_3y_median": 0.22, "piotroski_f": 8, "altman_z": 4.0,
        "asset_cagr": 0.10, "ebitda_cagr": 0.20,
    }, []))

    result = roic.score_ticker(con, "CRVL", 900_000_000)

    assert result["score"] == 10  # 5 + 3 + 2
    row = db.latest_scores(con).iloc[0]
    assert row["roic_score"] == 10
    assert row["roic_3y_median"] == pytest.approx(0.22)
    assert row["stage"] == 3


def test_incomplete_xbrl_flags_but_never_excludes_and_keeps_stage_2_warnings(con, monkeypatch):
    _stage_2_ticker(con)
    db.upsert_score(con, "CRVL", quant_score=9, data_warnings="INSIDER_PCT")
    monkeypatch.setattr(roic, "metrics", lambda t, cap: ({
        "roic_3y_median": None, "piotroski_f": None, "altman_z": None,
        "asset_cagr": None, "ebitda_cagr": None,
    }, ["XBRL_INCOMPLETE"]))

    roic.score_ticker(con, "CRVL", 900_000_000)

    row = db.latest_scores(con).iloc[0]
    assert row["roic_score"] == 0  # ran and found nothing — not the same as NULL
    assert row["data_warnings"] == "INSIDER_PCT,XBRL_INCOMPLETE"  # Stage 2's warning survives
    assert row["status"] == "active"  # flagged, never excluded
    assert row["stage"] == 2  # not advanced
