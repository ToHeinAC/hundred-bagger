"""Shared fixtures. Every test runs against a tmp_path DuckDB; no network."""

from __future__ import annotations

import socket

import pandas as pd
import pytest

from src import db


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """PRD §11 — the suite is green offline. Any real socket is a test failure."""

    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted; mock yfinance instead")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def con(tmp_path):
    """A freshly initialised database, isolated from data/100baggers.duckdb."""
    path = tmp_path / "test.duckdb"
    db.init_db(path)
    with db.connect(path) as c:
        yield c


class FakeTicker:
    """Stands in for yf.Ticker: same three statement frames plus .info."""

    def __init__(self, info: dict, income, cashflow, balance):
        self.info = info
        self.income_stmt = income
        self.cashflow = cashflow
        self.balance_sheet = balance


def frame(rows: dict[str, list[float]] | None) -> pd.DataFrame:
    """A yfinance statement frame: index = line item, columns = periods (newest first)."""
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).T


@pytest.fixture
def perfect_ticker() -> FakeTicker:
    """Scores 14/14 and trips no exclusion. Revenue 100 -> 200 over 2 years."""
    return FakeTicker(
        info={"debtToEquity": 20.0, "heldPercentInsiders": 0.15},
        income=frame({
            "Total Revenue": [200.0, 140.0, 100.0],
            "Gross Profit": [120.0, 84.0, 60.0],
            "Operating Income": [40.0, 28.0, 20.0],
        }),
        cashflow=frame({
            "Operating Cash Flow": [50.0, 35.0, 25.0],
            "Capital Expenditure": [-10.0, -7.0, -5.0],
        }),
        balance=frame({"Ordinary Shares Number": [100.0, 100.0, 100.0]}),
    )


@pytest.fixture
def empty_ticker() -> FakeTicker:
    """yfinance returned nothing usable — the microcap case."""
    return FakeTicker(info={}, income=frame(None), cashflow=frame(None), balance=frame(None))
