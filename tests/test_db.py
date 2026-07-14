"""db.py — the only SQL surface. If these break, every stage writes garbage."""

from __future__ import annotations

import datetime as dt

import pytest

from src import db

TABLES = {
    "universe", "scores", "exclusions", "insider_events", "alerts",
    "monitoring_log", "portfolio", "portfolio_actions", "portfolio_snapshots",
}


def _rows(con, ticker: str = "AAA") -> dict:
    return {"ticker": ticker, "name": "Alpha", "sector": "Technology",
            "exchange": "NMS", "market_cap": 500_000_000,
            "avg_volume": 100_000, "revenue_ttm": 20_000_000}


def test_init_db_creates_all_nine_tables(con):
    found = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables"
    ).fetchall()}
    assert TABLES <= found
    assert len(TABLES) == 9


def test_init_db_is_idempotent(tmp_path):
    path = tmp_path / "twice.duckdb"
    db.init_db(path)
    db.init_db(path)  # must not raise on re-run
    with db.connect(path) as con:
        assert con.execute("SELECT count(*) FROM universe").fetchone()[0] == 0


def test_connect_read_only_on_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        db.connect(tmp_path / "nope.duckdb", read_only=True)


# --- universe ---------------------------------------------------------------


def test_replace_universe_upserts_rather_than_duplicating(con):
    db.replace_universe(con, [_rows(con)])
    db.replace_universe(con, [{**_rows(con), "name": "Alpha Renamed"}])
    df = db.get_universe(con)
    assert len(df) == 1
    assert df.loc[0, "name"] == "Alpha Renamed"


def test_replace_universe_preserves_stage_and_status_of_known_tickers(con):
    db.replace_universe(con, [_rows(con)])
    db.set_stage(con, ["AAA"], 3)
    db.set_status(con, ["AAA"], "watchlist")
    db.replace_universe(con, [_rows(con)])
    df = db.get_universe(con)
    assert int(df.loc[0, "stage"]) == 3
    assert df.loc[0, "status"] == "watchlist"


@pytest.mark.parametrize("sequence,expected", [
    ([2, 3], 3),
    ([3, 2], 3),      # high-water mark: never lowered
    ([4, 1, 2], 4),
])
def test_set_stage_is_a_high_water_mark(con, sequence, expected):
    db.replace_universe(con, [_rows(con)])
    for stage in sequence:
        db.set_stage(con, ["AAA"], stage)
    assert int(db.get_universe(con).loc[0, "stage"]) == expected


def test_get_universe_filters_by_stage_and_status(con):
    db.replace_universe(con, [_rows(con, "AAA"), _rows(con, "BBB")])
    db.set_stage(con, ["BBB"], 3)
    db.set_status(con, ["AAA"], "excluded")
    assert db.get_universe(con, stage=2)["ticker"].tolist() == ["BBB"]
    assert db.get_universe(con, status="active")["ticker"].tolist() == ["BBB"]


# --- scores -----------------------------------------------------------------


def test_upsert_score_is_idempotent_for_the_same_ticker_and_date(con):
    day = dt.date(2026, 1, 5)
    db.upsert_score(con, "AAA", day, quant_score=8)
    db.upsert_score(con, "AAA", day, quant_score=11)
    n, score = con.execute(
        "SELECT count(*), max(quant_score) FROM scores WHERE ticker = 'AAA'"
    ).fetchone()
    assert (n, score) == (1, 11)


def test_upsert_score_keeps_one_row_per_date(con):
    db.upsert_score(con, "AAA", dt.date(2026, 1, 5), quant_score=8)
    db.upsert_score(con, "AAA", dt.date(2026, 2, 5), quant_score=9)
    assert con.execute("SELECT count(*) FROM scores").fetchone()[0] == 2


def test_upsert_score_rejects_an_unknown_column(con):
    with pytest.raises(ValueError, match="Unknown scores columns"):
        db.upsert_score(con, "AAA", quant_score=8, moat_scorre=5)


@pytest.mark.parametrize("cols,expected", [
    ({"quant_score": 10}, 10),                                   # NULLs count as 0
    ({"quant_score": 10, "roic_score": 7}, 17),
    ({"quant_score": 10, "roic_score": 7, "moat_score": 9}, 26),
    ({"moat_score": 4}, 4),
])
def test_upsert_score_recomputes_total_from_the_three_subscores(con, cols, expected):
    db.upsert_score(con, "AAA", dt.date(2026, 1, 5), **cols)
    total = con.execute("SELECT total_score FROM scores WHERE ticker = 'AAA'").fetchone()[0]
    assert total == expected


def test_upsert_score_merges_later_stage_columns_into_the_same_row(con):
    day = dt.date(2026, 1, 5)
    db.upsert_score(con, "AAA", day, quant_score=10, gross_margin=0.6)
    db.upsert_score(con, "AAA", day, roic_score=7)
    row = con.execute(
        "SELECT quant_score, gross_margin, roic_score, total_score FROM scores"
    ).fetchone()
    assert row == (10, 0.6, 7, 17)


def test_latest_scores_returns_only_the_newest_row_per_ticker(con):
    db.replace_universe(con, [_rows(con, "AAA")])
    db.upsert_score(con, "AAA", dt.date(2026, 1, 5), quant_score=8)
    db.upsert_score(con, "AAA", dt.date(2026, 6, 5), quant_score=12)
    df = db.latest_scores(con)
    assert len(df) == 1
    assert int(df["quant_score"].iloc[0]) == 12


def test_latest_scores_on_an_empty_db_is_empty_not_an_error(con):
    assert db.latest_scores(con).empty


# --- exclusions -------------------------------------------------------------


def test_add_exclusion_marks_the_ticker_excluded(con):
    db.replace_universe(con, [_rows(con)])
    db.add_exclusion(con, "AAA", "CASH_BURNER", "burning", stage=2)
    assert db.get_universe(con).loc[0, "status"] == "excluded"


def test_add_exclusion_is_idempotent_for_the_same_reason_and_date(con):
    db.replace_universe(con, [_rows(con)])
    db.add_exclusion(con, "AAA", "CASH_BURNER", "first")
    db.add_exclusion(con, "AAA", "CASH_BURNER", "second")
    n, detail = con.execute("SELECT count(*), max(detail) FROM exclusions").fetchone()
    assert (n, detail) == (1, "second")


def test_exclusions_for_ticker_returns_the_full_audit_trail_including_reversed(con):
    db.replace_universe(con, [_rows(con, "AAA"), _rows(con, "BBB")])
    db.add_exclusion(con, "AAA", "CASH_BURNER", "burning", stage=2)
    db.add_exclusion(con, "AAA", "ASSET_BLOAT", "bloated", stage=3)
    db.add_exclusion(con, "BBB", "CHRONIC_DILUTER")
    con.execute("UPDATE exclusions SET reversed = TRUE WHERE reason = 'CASH_BURNER'")
    df = db.exclusions_for_ticker(con, "AAA")
    assert sorted(df["reason"]) == ["ASSET_BLOAT", "CASH_BURNER"]  # reversed one still shown
    assert df.set_index("reason").loc["CASH_BURNER", "reversed"]


def test_exclusions_for_ticker_on_a_clean_ticker_is_empty(con):
    db.replace_universe(con, [_rows(con)])
    assert db.exclusions_for_ticker(con, "AAA").empty


def test_exclusion_counts_groups_by_reason_and_ignores_reversed(con):
    db.replace_universe(con, [_rows(con, "AAA"), _rows(con, "BBB")])
    db.add_exclusion(con, "AAA", "CASH_BURNER")
    db.add_exclusion(con, "BBB", "CASH_BURNER")
    db.add_exclusion(con, "BBB", "CHRONIC_DILUTER")
    con.execute("UPDATE exclusions SET reversed = TRUE WHERE ticker = 'BBB'")
    counts = dict(zip(db.exclusion_counts(con)["reason"], db.exclusion_counts(con)["n"]))
    assert counts == {"CASH_BURNER": 1}


# --- reporting --------------------------------------------------------------


def test_status_summary_on_an_empty_db_returns_zeros(con):
    s = db.status_summary(con)
    assert s["universe_total"] == 0
    assert s["active"] == 0
    assert s["excluded"] == 0
    assert s["by_stage"] == {}
    assert s["universe_last_built"] is None
    assert s["scores_last_run"] is None
    assert s["monitor_last_run"] is None
    assert s["open_positions"] == 0
    assert s["unacked_alerts"] == 0


def test_status_summary_counts_and_freshness_after_a_run(con):
    db.replace_universe(con, [_rows(con, "AAA"), _rows(con, "BBB")])
    db.set_stage(con, ["AAA"], 2)
    db.add_exclusion(con, "BBB", "CASH_BURNER")
    db.upsert_score(con, "AAA", dt.date(2026, 3, 1), quant_score=9)
    s = db.status_summary(con)
    assert (s["universe_total"], s["active"], s["excluded"]) == (2, 1, 1)
    assert s["by_stage"] == {1: 1, 2: 1}
    assert s["scores_last_run"] == dt.date(2026, 3, 1)


def test_funnel_reports_active_count_per_stage(con):
    db.replace_universe(con, [_rows(con, "AAA"), _rows(con, "BBB")])
    db.set_stage(con, ["AAA"], 2)
    db.add_exclusion(con, "AAA", "CASH_BURNER")
    df = db.funnel(con).set_index("stage")
    assert int(df.loc[1, "active"]) == 1
    assert int(df.loc[2, "n"]) == 1
    assert int(df.loc[2, "active"]) == 0  # Stage 2 and excluded — status is orthogonal
