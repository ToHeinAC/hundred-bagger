"""universe.py — Stage 1 screen. yf.screen is mocked; no network."""

from __future__ import annotations

import json

import pytest

from src import config, universe


def quote(symbol: str, exchange: str = "NMS", **kw) -> dict:
    """A screener hit, shaped like the real payload — note sector is absent."""
    return {
        "symbol": symbol,
        "longName": f"{symbol} Corp",
        "shortName": symbol,
        "marketCap": 500_000_000,
        "averageDailyVolume3Month": 120_000,
        "exchange": exchange,
        **kw,
    }


def _sector_of(query) -> str | None:
    """Which sector this EquityQuery pins, read out of its serialised operands."""
    payload = json.dumps(query.to_dict())
    return next((s for s in config.INCLUDED_SECTORS if f'"{s}"' in payload), None)


@pytest.fixture
def fake_screen(monkeypatch):
    """Install a yf.screen returning per-sector quotes. Returns the call log."""
    by_sector: dict[str, list[dict]] = {}
    calls: list[dict] = []

    def install(quotes_by_sector: dict[str, list[dict]], totals: list[int] | None = None):
        by_sector.update(quotes_by_sector)

        def screen(query, offset=0, size=250, **kw):
            calls.append({"offset": offset, "size": size})
            if totals is not None and size == 1:  # funnel_counts probe
                return {"total": totals[len(calls) - 1], "quotes": []}
            hits = by_sector.get(_sector_of(query), [])
            page = hits[offset: offset + size]
            return {"total": len(hits), "quotes": page}

        monkeypatch.setattr(universe.yf, "screen", screen)
        return calls

    return install


def test_build_drops_otc_exchanges_and_keeps_real_listings(fake_screen):
    fake_screen({"Technology": [
        quote("AAA", "NMS"), quote("BBB", "NYQ"),
        quote("PNKY", "PNK"), quote("OQBY", "OQB"), quote("OQXY", "OQX"),
    ]})
    result = universe.build()
    assert [r["ticker"] for r in result["rows"]] == ["AAA", "BBB"]
    assert result["dropped_otc"] == 3


def test_build_dedupes_a_symbol_returned_by_two_sector_queries(fake_screen):
    fake_screen({
        "Technology": [quote("AAA")],
        "Healthcare": [quote("AAA"), quote("CCC")],
    })
    rows = universe.build()["rows"]
    assert [r["ticker"] for r in rows] == ["AAA", "CCC"]
    assert rows[0]["sector"] == "Technology"  # first sector query wins


def test_build_maps_the_screener_payload_onto_universe_columns(fake_screen):
    fake_screen({"Technology": [quote("AAA")]})
    row = universe.build()["rows"][0]
    assert row == {
        "ticker": "AAA", "name": "AAA Corp", "sector": "Technology", "exchange": "NMS",
        "market_cap": 500_000_000, "avg_volume": 120_000, "revenue_ttm": None,
    }


def test_build_falls_back_to_short_name_when_long_name_is_missing(fake_screen):
    fake_screen({"Technology": [quote("AAA", longName=None)]})
    assert universe.build()["rows"][0]["name"] == "AAA"


def test_build_pages_through_a_sector_larger_than_one_page(fake_screen, monkeypatch):
    monkeypatch.setattr(universe, "PAGE_SIZE", 2)
    calls = fake_screen({"Technology": [quote(f"T{i}") for i in range(5)]})
    rows = universe.build()["rows"]
    assert len(rows) == 5
    assert [c["offset"] for c in calls][:3] == [0, 2, 4]


def test_build_on_an_empty_screen_returns_no_rows(fake_screen):
    fake_screen({})
    assert universe.build() == {"rows": [], "dropped_otc": 0}


def test_funnel_counts_are_cumulative_and_in_filter_order(fake_screen):
    fake_screen({}, totals=[8142, 5300, 2380, 1790, 731])
    counts = universe.funnel_counts()
    assert [label for label, _ in counts] == [
        "region", "sector", "market_cap", "volume", "revenue",
    ]
    assert [n for _, n in counts] == [8142, 5300, 2380, 1790, 731]
