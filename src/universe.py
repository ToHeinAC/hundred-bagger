"""Stage 1 — build the candidate universe from Yahoo's equity screener.

All hard filters run server-side except the OTC exclusion: Yahoo's `exchange`
filter takes country codes, not venue codes, so pink sheets (PNK/OQB/OQX) are
dropped client-side against ALLOWED_EXCHANGES.

Sector is absent from the screener payload, so we query one sector at a time —
that attributes a sector to every hit for free.
"""

from __future__ import annotations

import argparse
import json

import yfinance as yf
from yfinance import EquityQuery as EQ

from src import config, db

PAGE_SIZE = 250  # Yahoo's per-request maximum


def _filters() -> list[tuple[str, EQ]]:
    """The hard filters, in funnel order. Each is applied on top of the prior."""
    return [
        ("region", EQ("eq", ["region", config.REGION])),
        ("sector", EQ("or", [EQ("eq", ["sector", s]) for s in config.INCLUDED_SECTORS])),
        ("market_cap", EQ("btwn", ["intradaymarketcap", config.MIN_MARKET_CAP, config.MAX_MARKET_CAP])),
        ("volume", EQ("gt", ["avgdailyvol3m", config.MIN_AVG_VOLUME])),
        ("revenue", EQ("gt", ["totalrevenues.lasttwelvemonths", config.MIN_REVENUE_TTM])),
    ]


def _count(query: EQ) -> int:
    """Total matches for a query, without paging through them."""
    return yf.screen(query, offset=0, size=1)["total"]


def funnel_counts() -> list[tuple[str, int]]:
    """Cumulative match count after each successive filter — the drop-off story."""
    counts, applied = [], []
    for label, f in _filters():
        applied.append(f)
        counts.append((label, _count(EQ("and", applied) if len(applied) > 1 else applied[0])))
    return counts


def _screen_sector(sector: str) -> list[dict]:
    """Every hit for one sector, paginated."""
    query = EQ("and", [
        EQ("eq", ["region", config.REGION]),
        EQ("eq", ["sector", sector]),
        EQ("btwn", ["intradaymarketcap", config.MIN_MARKET_CAP, config.MAX_MARKET_CAP]),
        EQ("gt", ["avgdailyvol3m", config.MIN_AVG_VOLUME]),
        EQ("gt", ["totalrevenues.lasttwelvemonths", config.MIN_REVENUE_TTM]),
    ])
    quotes, offset = [], 0
    while True:
        page = yf.screen(query, offset=offset, size=PAGE_SIZE,
                         sortField="intradaymarketcap", sortAsc=False)
        batch = page.get("quotes", [])
        quotes.extend(batch)
        offset += len(batch)
        if not batch or offset >= page.get("total", 0):
            break
    return [dict(q, sector=sector) for q in quotes]


def build() -> dict:
    """Run the screen. Returns the funnel plus the surviving rows."""
    rows, dropped_otc = [], 0
    for sector in config.INCLUDED_SECTORS:
        for q in _screen_sector(sector):
            if q.get("exchange") not in config.ALLOWED_EXCHANGES:
                dropped_otc += 1
                continue
            rows.append({
                "ticker": q["symbol"],
                "name": q.get("longName") or q.get("shortName"),
                "sector": q["sector"],
                "exchange": q.get("exchange"),
                "market_cap": q.get("marketCap"),
                "avg_volume": q.get("averageDailyVolume3Month"),
                "revenue_ttm": None,  # screener filters on it but does not return it
            })
    # Yahoo can return the same symbol in two sector queries; keep the first.
    unique: dict[str, dict] = {}
    for r in rows:
        unique.setdefault(r["ticker"], r)
    return {"rows": list(unique.values()), "dropped_otc": dropped_otc}


def _main() -> None:
    ap = argparse.ArgumentParser(prog="src.universe")
    ap.add_argument("--rebuild", action="store_true", help="run the screen and persist")
    ap.add_argument("--json", action="store_true", help="emit machine-readable summary")
    args = ap.parse_args()

    if not args.rebuild:
        ap.error("nothing to do; pass --rebuild")

    print("Counting drop-off per filter...")
    counts = funnel_counts()
    prev = None
    for label, n in counts:
        delta = "" if prev is None else f"  (−{prev - n:,})"
        print(f"  after {label:<12} {n:>7,}{delta}")
        prev = n

    result = build()
    rows = result["rows"]
    with db.connect() as con:
        db.replace_universe(con, rows)

    print(f"  after OTC filter {len(rows):>7,}  (−{result['dropped_otc']:,})")
    print(f"\nStage 1 universe: {len(rows):,} tickers persisted.")
    if args.json:
        print(json.dumps({"funnel": dict(counts), "final": len(rows),
                          "dropped_otc": result["dropped_otc"]}))


if __name__ == "__main__":
    _main()
