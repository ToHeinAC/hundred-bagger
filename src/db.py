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


def market_cap(con, ticker: str) -> int | None:
    """Stage 1's cap for one ticker, or None if unknown. Like `triggers.distress`,
    `moat.save_ticker` needs the one input a filing cannot supply."""
    row = con.execute("SELECT market_cap FROM universe WHERE ticker = ?", [ticker]).fetchone()
    return row[0] if row else None


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


def merge_warnings(con, ticker: str, codes: list[str], score_date: dt.date | None = None) -> None:
    """Union new warning codes into data_warnings without clobbering existing ones.

    Stage 2 and Stage 3 both write warnings into the same row, and Stage 3 runs
    second — a plain overwrite would erase the yfinance coverage gaps that Stage 2
    recorded, which are exactly what the dashboard surfaces.
    """
    score_date = score_date or dt.date.today()
    existing = con.execute(
        "SELECT data_warnings FROM scores WHERE ticker = ? AND score_date = ?",
        [ticker, score_date],
    ).fetchone()
    current = (existing[0] or "").split(",") if existing else []
    merged = sorted({c for c in [*current, *codes] if c})
    con.execute(
        "UPDATE scores SET data_warnings = ? WHERE ticker = ? AND score_date = ?",
        [",".join(merged) or None, ticker, score_date],
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


def exclusions_for_ticker(con, ticker: str) -> pd.DataFrame:
    """Every exclusion ever recorded for one ticker, reversed ones included.

    The audit trail, not the current verdict: a reversed exclusion is still part
    of the story of why a ticker is where it is.
    """
    return con.execute(
        """
        SELECT reason, detail, stage, excluded_date, reversed FROM exclusions
        WHERE ticker = ? ORDER BY excluded_date DESC, reason
        """,
        [ticker],
    ).df()


def exclusion_counts(con) -> pd.DataFrame:
    return con.execute(
        """
        SELECT reason, count(*) AS n FROM exclusions
        WHERE NOT reversed GROUP BY reason ORDER BY n DESC
        """
    ).df()


# --- insider events (Phase 3) -----------------------------------------------


def replace_insider_events(con, ticker: str, rows: list[dict]) -> int:
    """Re-running /hunt-signals restates a ticker's Form 4 history rather than
    appending to it. The table has a surrogate key and no natural one, so the
    delete is what keeps the stage idempotent."""
    con.execute("DELETE FROM insider_events WHERE ticker = ?", [ticker])
    for r in rows:
        con.execute(
            """
            INSERT INTO insider_events
                (ticker, filed_date, transaction_date, insider_name, insider_title,
                 transaction_type, shares, price, value, is_cluster_buy, signal_strength)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ticker, r.get("filed_date"), r.get("transaction_date"),
                r.get("insider_name"), r.get("insider_title"), r.get("transaction_type"),
                r.get("shares"), r.get("price"), r.get("value"),
                r.get("is_cluster_buy", False), r.get("signal_strength"),
            ],
        )
    return len(rows)


def insider_events(con, ticker: str | None = None) -> pd.DataFrame:
    sql = "SELECT * FROM insider_events"
    params: list = []
    if ticker:
        sql += " WHERE ticker = ?"
        params.append(ticker)
    return con.execute(sql + " ORDER BY transaction_date DESC", params).df()


# --- alerts (Phase 3) --------------------------------------------------------


def add_alert(con, ticker: str, alert_type: str, severity: str, message: str) -> bool:
    """Raise an alert unless the identical one already exists today.

    `alerts` has a surrogate key, so nothing in the schema stops a second
    /hunt-signals run from raising the same alert twice. Dedupe here: same
    ticker + type + message on the same day is the same alert, and re-raising it
    would silently reset an acknowledgement the user already made. Returns
    whether a row was actually written.
    """
    exists = con.execute(
        """
        SELECT 1 FROM alerts
        WHERE ticker = ? AND alert_type = ? AND message = ? AND created_date = ?
        """,
        [ticker, alert_type, message, dt.date.today()],
    ).fetchone()
    if exists:
        return False
    con.execute(
        """
        INSERT INTO alerts (ticker, alert_type, severity, message, created_date)
        VALUES (?, ?, ?, ?, ?)
        """,
        [ticker, alert_type, severity, message, dt.date.today()],
    )
    return True


def alerts(con, acknowledged: bool | None = None) -> pd.DataFrame:
    sql = "SELECT * FROM alerts"
    params: list = []
    if acknowledged is not None:
        sql += " WHERE acknowledged = ?"
        params.append(acknowledged)
    return con.execute(sql + " ORDER BY created_date DESC, id DESC", params).df()


def acknowledge_alerts(con, ids: list[int]) -> int:
    """The one write the dashboard is allowed to make (PRD §6 permits the UI to
    write only where the user is the author of the fact — here, "I have seen this")."""
    for i in ids:
        con.execute("UPDATE alerts SET acknowledged = TRUE WHERE id = ?", [int(i)])
    return len(ids)


# --- monitoring (Phase 3) ----------------------------------------------------


def add_monitoring_log(
    con, ticker: str, flags: list[str], action: str, notes: str = "",
    check_date: dt.date | None = None,
) -> None:
    """One log row per ticker per check_date; re-checking overwrites it.

    `flags` is stored as a JSON array so a code can never be confused with a
    substring of another (`ROIC_DETERIORATION` vs a hypothetical `ROIC_DET`).
    """
    check_date = check_date or dt.date.today()
    con.execute(
        "DELETE FROM monitoring_log WHERE ticker = ? AND check_date = ?", [ticker, check_date]
    )
    con.execute(
        """
        INSERT INTO monitoring_log (ticker, check_date, flags, recommended_action, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        [ticker, check_date, json.dumps(flags), action, notes or None],
    )


def monitoring_log(con, ticker: str | None = None) -> pd.DataFrame:
    sql = "SELECT * FROM monitoring_log"
    params: list = []
    if ticker:
        sql += " WHERE ticker = ?"
        params.append(ticker)
    return con.execute(sql + " ORDER BY check_date DESC, ticker", params).df()


def upsert_snapshot(con, ticker: str, snapshot_date: dt.date | None = None, **cols) -> None:
    snapshot_date = snapshot_date or dt.date.today()
    con.execute(
        """
        INSERT INTO portfolio_snapshots
            (ticker, snapshot_date, price, value, unrealized_return_pct, status_badge)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (ticker, snapshot_date) DO UPDATE SET
            price = excluded.price,
            value = excluded.value,
            unrealized_return_pct = excluded.unrealized_return_pct,
            status_badge = excluded.status_badge
        """,
        [
            ticker, snapshot_date, cols.get("price"), cols.get("value"),
            cols.get("unrealized_return_pct"), cols.get("status_badge"),
        ],
    )


def open_positions(con) -> pd.DataFrame:
    """The default target list for /hunt-monitor. Filled by the Portfolio page."""
    return con.execute(
        "SELECT * FROM portfolio WHERE status = 'open' ORDER BY ticker"
    ).df()


def latest_monitor_action(con) -> dict[str, str]:
    """Ticker -> the most recent `monitoring_log.recommended_action`.

    How a position learns whether its thesis still holds. The verdict is the
    monitor's, derived from the XBRL trigger table with evidence behind it — the
    portfolio never re-decides it from price, and a ticker never checked simply
    has no entry (missing data is a coverage gap, not a clean bill of health).
    """
    rows = con.execute(
        """
        SELECT ticker, recommended_action FROM monitoring_log
        QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY check_date DESC) = 1
        """
    ).fetchall()
    return {r[0]: r[1] for r in rows if r[1]}


# --- portfolio (Phase 4) -----------------------------------------------------


def add_position(
    con, ticker: str, entry_price: float, shares: float,
    entry_date: dt.date | None = None, thesis: str | None = None,
    horizon_months: int | None = None, entry_roic: float | None = None,
) -> None:
    """Open a position. One row per buy — a second buy of the same ticker is a
    second row, not an edit, so the entry price of each tranche is preserved."""
    con.execute(
        """
        INSERT INTO portfolio
            (ticker, entry_date, entry_price, shares, thesis, horizon_months,
             entry_roic, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
        """,
        [ticker, entry_date or dt.date.today(), entry_price, shares, thesis,
         horizon_months, entry_roic],
    )


def positions(con) -> pd.DataFrame:
    """Every position, open or closed. `open_positions` is the open subset."""
    return con.execute("SELECT * FROM portfolio ORDER BY ticker, entry_date").df()


def delete_positions(con, tickers: list[str] | None = None) -> int:
    """Drop positions so a corrected CSV can be re-imported.

    The portfolio is the one table a user maintains by hand, so it needs an undo
    that the screening tables — rebuilt by re-running their skill — do not.
    """
    if tickers is None:
        n = con.execute("SELECT count(*) FROM portfolio").fetchone()[0]
        con.execute("DELETE FROM portfolio")
        return n
    for t in tickers:
        con.execute("DELETE FROM portfolio WHERE ticker = ?", [t])
    return len(tickers)


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
