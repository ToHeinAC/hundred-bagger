"""The only SQL surface in the project.

No other module writes SQL. Every query is parameterised; ticker strings are
never interpolated. Column names used in dynamic upserts are validated against
the live schema, so a typo raises rather than injecting.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import duckdb
import pandas as pd

from src.config import DUCKDB_PATH

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the database. Dashboard pages pass read_only=True."""
    path = Path(db_path or DUCKDB_PATH)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.exists():
        raise FileNotFoundError(f"No database at {path}. Run: uv run python -m src.db --init")
    return duckdb.connect(str(path), read_only=read_only)


def init_db(db_path: Path | None = None) -> None:
    """Create all 9 tables. Idempotent."""
    with connect(db_path) as con:
        con.execute(SCHEMA_PATH.read_text())


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?", [table]
    ).fetchall()
    return {r[0] for r in rows}


# --- universe ---------------------------------------------------------------


def replace_universe(con, rows: list[dict]) -> int:
    """Upsert Stage 1 rows. Existing tickers keep their stage/status/added_date."""
    today = dt.date.today()
    for r in rows:
        con.execute(
            """
            INSERT INTO universe
                (ticker, name, sector, exchange, market_cap, avg_volume, revenue_ttm,
                 stage, status, added_date, updated_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?)
            ON CONFLICT (ticker) DO UPDATE SET
                name = excluded.name,
                sector = excluded.sector,
                exchange = excluded.exchange,
                market_cap = excluded.market_cap,
                avg_volume = excluded.avg_volume,
                revenue_ttm = excluded.revenue_ttm,
                updated_date = excluded.updated_date
            """,
            [
                r["ticker"], r.get("name"), r.get("sector"), r.get("exchange"),
                r.get("market_cap"), r.get("avg_volume"), r.get("revenue_ttm"),
                today, today,
            ],
        )
    return len(rows)


def get_universe(con, stage: int | None = None, status: str | None = None) -> pd.DataFrame:
    sql = "SELECT * FROM universe WHERE 1=1"
    params: list = []
    if stage is not None:
        sql += " AND stage >= ?"
        params.append(stage)
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    return con.execute(sql + " ORDER BY ticker", params).df()


def set_stage(con, tickers: list[str], stage: int) -> None:
    """Advance the high-water mark. Never lowers an existing stage."""
    for t in tickers:
        con.execute(
            "UPDATE universe SET stage = greatest(stage, ?), updated_date = ? WHERE ticker = ?",
            [stage, dt.date.today(), t],
        )


def set_status(con, tickers: list[str], status: str) -> None:
    for t in tickers:
        con.execute(
            "UPDATE universe SET status = ?, updated_date = ? WHERE ticker = ?",
            [status, dt.date.today(), t],
        )


# --- scores -----------------------------------------------------------------


def upsert_score(con, ticker: str, score_date: dt.date | None = None, **cols) -> None:
    """Write metric columns into (ticker, score_date), creating the row if absent.

    Stage 2/3/4 each write their own columns into the same row. Unknown column
    names raise. total_score is recomputed from whatever subscores are present.
    """
    score_date = score_date or dt.date.today()
    if not cols:
        return
    valid = _columns(con, "scores") - {"ticker", "score_date"}
    unknown = set(cols) - valid
    if unknown:
        raise ValueError(f"Unknown scores columns: {sorted(unknown)}")

    names = list(cols)
    assignments = ", ".join(f"{n} = excluded.{n}" for n in names)
    placeholders = ", ".join("?" for _ in names)
    con.execute(
        f"""
        INSERT INTO scores (ticker, score_date, {", ".join(names)})
        VALUES (?, ?, {placeholders})
        ON CONFLICT (ticker, score_date) DO UPDATE SET {assignments}
        """,
        [ticker, score_date, *[cols[n] for n in names]],
    )
    con.execute(
        """
        UPDATE scores SET total_score =
            coalesce(quant_score, 0) + coalesce(roic_score, 0) + coalesce(moat_score, 0)
        WHERE ticker = ? AND score_date = ?
        """,
        [ticker, score_date],
    )


def latest_scores(con) -> pd.DataFrame:
    """Most recent score row per ticker, joined to universe."""
    return con.execute(
        """
        SELECT u.ticker, u.name, u.sector, u.market_cap, u.stage, u.status, s.*
        FROM universe u
        JOIN scores s ON s.ticker = u.ticker
        QUALIFY row_number() OVER (PARTITION BY u.ticker ORDER BY s.score_date DESC) = 1
        """
    ).df()


# --- exclusions -------------------------------------------------------------


def add_exclusion(con, ticker: str, reason: str, detail: str = "", stage: int | None = None) -> None:
    con.execute(
        """
        INSERT INTO exclusions (ticker, reason, detail, stage, excluded_date)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (ticker, reason, excluded_date) DO UPDATE SET
            detail = excluded.detail, reversed = FALSE
        """,
        [ticker, reason, detail, stage, dt.date.today()],
    )
    set_status(con, [ticker], "excluded")


def exclusion_counts(con) -> pd.DataFrame:
    return con.execute(
        """
        SELECT reason, count(*) AS n FROM exclusions
        WHERE NOT reversed GROUP BY reason ORDER BY n DESC
        """
    ).df()


# --- reporting --------------------------------------------------------------


def funnel(con) -> pd.DataFrame:
    """Ticker count at each stage, as a high-water mark."""
    return con.execute(
        """
        SELECT stage, count(*) AS n, count(*) FILTER (WHERE status = 'active') AS active
        FROM universe GROUP BY stage ORDER BY stage
        """
    ).df()


def status_summary(con) -> dict:
    """Pipeline counts plus data freshness. No network calls."""
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "universe_total": one("SELECT count(*) FROM universe"),
        "active": one("SELECT count(*) FROM universe WHERE status = 'active'"),
        "excluded": one("SELECT count(*) FROM universe WHERE status = 'excluded'"),
        "watchlist": one("SELECT count(*) FROM universe WHERE status = 'watchlist'"),
        "by_stage": {int(r[0]): int(r[1]) for r in con.execute(
            "SELECT stage, count(*) FROM universe GROUP BY stage ORDER BY stage"
        ).fetchall()},
        "universe_last_built": one("SELECT max(added_date) FROM universe"),
        "scores_last_run": one("SELECT max(score_date) FROM scores"),
        "monitor_last_run": one("SELECT max(check_date) FROM monitoring_log"),
        "open_positions": one("SELECT count(*) FROM portfolio WHERE status = 'open'"),
        "unacked_alerts": one("SELECT count(*) FROM alerts WHERE NOT acknowledged"),
    }


def _main() -> None:
    ap = argparse.ArgumentParser(prog="src.db")
    ap.add_argument("--init", action="store_true", help="create all tables")
    ap.add_argument("--status", action="store_true", help="print pipeline summary as JSON")
    args = ap.parse_args()

    if args.init:
        init_db()
        print(f"Initialised {DUCKDB_PATH}")
    if args.status:
        with connect(read_only=True) as con:
            print(json.dumps(status_summary(con), indent=2, default=str))


if __name__ == "__main__":
    _main()
