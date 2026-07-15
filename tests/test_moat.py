"""Stage 4 — validation, the arithmetic Python owns, and the gate.

The judgement itself is Claude's and lives in SKILL.md; what is testable here is
that a malformed judgement is rejected and a well-formed one lands in the right
columns with the right derived numbers.
"""

from __future__ import annotations

import pytest

from src import config, db, moat

VALID = {
    "distribution": 2,
    "brand": 1,
    "network": 0,
    "regulatory": 2,
    "switching": 3,
    "cost": 1,
    "durability": 4,
    "founder_led": False,
    "reinvest_runway": "medium",
    "notes": "Embedded in customer workflows; 95% stated retention.",
    "key_risks": ["Top ten clients are 38% of revenue"],
}


@pytest.fixture
def universe(con):
    """moat.save_ticker moves a ticker through `universe`, so it has to exist."""
    db.replace_universe(con, [{"ticker": "CRVL", "name": "CorVel"}])
    db.set_stage(con, ["CRVL"], 3)
    return con


def saved(con, ticker: str = "CRVL"):
    return db.latest_scores(con).set_index("ticker").loc[ticker]


# --- save: the happy path ---------------------------------------------------


def test_save_persists_dimensions_and_prose(universe):
    moat.save_ticker(universe, "CRVL", VALID)
    row = saved(universe)
    assert (row["moat_distribution"], row["moat_switching"], row["moat_network"]) == (2, 3, 0)
    assert row["moat_durability"] == 4
    assert row["reinvest_runway"] == "medium"
    assert bool(row["founder_led"]) is False
    assert "95% stated retention" in row["moat_notes"]


def test_moat_total_is_summed_by_python_not_supplied_by_claude(universe):
    """A moat_total in the payload is ignored — the judge does not do the sums."""
    moat.save_ticker(universe, "CRVL", {**VALID, "moat_total": 18})
    assert saved(universe)["moat_total"] == 9  # 2+1+0+2+3+1


def test_moat_score_derivation_matches_config(universe):
    moat.save_ticker(universe, "CRVL", VALID)
    row = saved(universe)
    assert row["moat_score"] == config.moat_score(9, 4) == 6


def test_key_risks_accepts_a_list_and_stores_it_flat(universe):
    moat.save_ticker(universe, "CRVL", {**VALID, "key_risks": ["one", "two"]})
    assert saved(universe)["key_risks"] == "one; two"


# --- save: the gate ---------------------------------------------------------


def test_gate_advances_to_stage_4_and_watchlist_b(universe):
    result = moat.save_ticker(universe, "CRVL", VALID)
    row = saved(universe)
    assert result["passed"] is True
    assert (row["stage"], row["status"]) == (4, "watchlist")


def test_below_durability_gate_does_not_advance_and_does_not_exclude(universe):
    """A moat miss is a flag, not an exclusion (PRD §2.4)."""
    result = moat.save_ticker(universe, "CRVL", {**VALID, "durability": 2})
    row = saved(universe)
    assert result["passed"] is False
    assert (row["stage"], row["status"]) == (3, "active")
    assert db.exclusion_counts(universe).empty


def test_below_total_gate_does_not_advance(universe):
    thin = {**VALID, "distribution": 1, "switching": 0, "regulatory": 0, "cost": 0, "brand": 0}
    result = moat.save_ticker(universe, "CRVL", thin)  # moat_total = 1
    assert result["passed"] is False
    assert saved(universe)["stage"] == 3


# --- save: validation is real, not decorative -------------------------------


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"switching": 4}, "out of range"),
        ({"durability": 9}, "out of range"),
        ({"brand": None}, "expected int"),
        ({"founder_led": "true"}, "expected true or false"),
        ({"reinvest_runway": "enormous"}, "reinvest_runway"),
        ({"notes": ""}, "non-empty string"),
    ],
)
def test_malformed_payload_raises(payload, expected):
    with pytest.raises(ValueError, match=expected):
        moat.validate({**VALID, **payload})


def test_missing_dimension_raises():
    payload = {k: v for k, v in VALID.items() if k != "network"}
    with pytest.raises(ValueError, match="network"):
        moat.validate(payload)


# --- fetch ------------------------------------------------------------------


class FakeFiling:
    company, form, filing_date, accession_no = "CorVel", "10-K", "2024-06-07", "0000874866-24"

    def __init__(self, business: str):
        self._business = business

    def obj(self):
        return type("TenK", (), {"business": self._business})()


def _fake_company(business: str):
    filing = FakeFiling(business)
    filings = type("Filings", (), {"latest": lambda self: filing})()
    return lambda ticker: type("Company", (), {"get_filings": lambda self, form: filings})()


def test_fetch_writes_item1_with_a_header(monkeypatch, tmp_path):
    monkeypatch.setattr(moat, "Company", _fake_company("We sell claims software."))
    path = moat.fetch_ticker("CRVL", tmp_path)
    text = path.read_text()
    assert path.name == "CRVL.txt"
    assert "# accession:    0000874866-24" in text
    assert text.endswith("We sell claims software.")
    assert "TRUNCATED" not in text


def test_fetch_truncates_a_long_item1_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(moat, "Company", _fake_company("x" * (moat.ITEM1_CHAR_CAP + 500)))
    text = moat.fetch_ticker("CRVL", tmp_path).read_text()
    assert f"first {moat.ITEM1_CHAR_CAP:,} of" in text
    assert text.count("x") == moat.ITEM1_CHAR_CAP


def test_fetch_raises_when_item1_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(moat, "Company", _fake_company("   "))
    with pytest.raises(ValueError, match="empty"):
        moat.fetch_ticker("CRVL", tmp_path)


class Fake20F:
    """A foreign private issuer's annual report: 20-F, Business in Item 4."""
    company, form, filing_date, accession_no = "GRAVITY", "20-F", "2026-04-24", "0001628280-26"

    def __init__(self, business: str):
        self._business = business

    def obj(self):
        return type("TwentyF", (), {"business": self._business})()


def test_fetch_reads_the_20f_business_section_for_a_foreign_filer(monkeypatch, tmp_path):
    filing = Fake20F("We publish mobile games.")
    filings = type("Filings", (), {"latest": lambda self: filing})()
    monkeypatch.setattr(
        moat, "Company",
        lambda ticker: type("Company", (), {"get_filings": lambda self, form: filings})(),
    )
    text = moat.fetch_ticker("GRVY", tmp_path).read_text()
    assert "# form:         20-F" in text
    assert "# item:         4 (Information on the Company)" in text
    assert text.endswith("We publish mobile games.")
