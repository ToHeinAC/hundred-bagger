"""SEC EDGAR XBRL client — the primary-source half of Stage 3.

yfinance is a scraper; `companyfacts` is the filing itself. Stage 3 exists partly
to cross-check Stage 2 against it, so this module talks to EDGAR and nothing else:
no scoring, no arithmetic beyond picking the right number out of the JSON.

Two things are load-bearing here rather than in the caller:

- **The 10 req/s cap is enforced in code** (PRD §9). Every request goes through
  `_get`, which sleeps first. Exceeding the cap gets the user's IP blocked, so it
  cannot be left to discipline.
- **`SEC_USER_AGENT` is mandatory.** The SEC rejects requests without a contact
  email, so an unset value fails loudly at the first call rather than producing a
  confusing 403 per ticker.
"""

from __future__ import annotations

import datetime as dt
import functools
import time

import requests

from src import config

# Only annual reports. A 10-Q would silently mix quarters into a "yearly" series.
ANNUAL_FORMS = frozenset({"10-K", "10-K/A"})

# Small filers use non-standard tags, so every metric is a fallback chain
# (PRD §14: "XBRL tag coverage is uneven"). Order expresses preference, not
# precedence: `annual()` picks the most *current* series in the chain, not the
# first one with data. See its docstring — that distinction is load-bearing.
REVENUE = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)
EBIT = ("OperatingIncomeLoss",)
PRETAX_INCOME = (
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
)
TAX_EXPENSE = ("IncomeTaxExpenseBenefit",)
NET_INCOME = ("NetIncomeLoss",)
GROSS_PROFIT = ("GrossProfit",)
CFO = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)
DEPRECIATION = (
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "DepreciationAndAmortization",
)
ASSETS = ("Assets",)
ASSETS_CURRENT = ("AssetsCurrent",)
LIABILITIES = ("Liabilities",)
LIABILITIES_CURRENT = ("LiabilitiesCurrent",)
EQUITY = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)
RETAINED_EARNINGS = ("RetainedEarningsAccumulatedDeficit",)
CASH = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
)
LONG_TERM_DEBT = ("LongTermDebtNoncurrent", "LongTermDebt")
SHORT_TERM_DEBT = ("LongTermDebtCurrent", "ShortTermBorrowings")
SHARES = ("CommonStockSharesOutstanding", "WeightedAverageNumberOfDilutedSharesOutstanding")


class SecError(RuntimeError):
    """EDGAR was reachable, but had nothing usable for this ticker."""


def _headers() -> dict[str, str]:
    if not config.SEC_USER_AGENT:
        raise SecError(
            "SEC_USER_AGENT is unset. The SEC requires a contact email on every "
            "request. Set it in .env, e.g.\n"
            "  SEC_USER_AGENT='Jane Doe jane@example.com'"
        )
    return {"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def _get(url: str) -> dict:
    """The only way out to EDGAR, so the rate limit cannot be bypassed."""
    time.sleep(config.SEC_SLEEP)
    response = requests.get(url, headers=_headers(), timeout=config.SEC_TIMEOUT)
    response.raise_for_status()
    return response.json()


@functools.cache
def cik_map() -> dict[str, str]:
    """ticker -> zero-padded 10-digit CIK. Fetched once per process."""
    payload = _get(config.SEC_TICKER_MAP_URL)
    return {e["ticker"].upper(): f"{int(e['cik_str']):010d}" for e in payload.values()}


def company_facts(ticker: str) -> dict:
    """The whole XBRL fact set for one company. One request."""
    cik = cik_map().get(ticker.upper())
    if cik is None:
        raise SecError(f"{ticker} has no CIK in the SEC ticker map")
    return _get(f"{config.SEC_BASE}/api/xbrl/companyfacts/CIK{cik}.json")


def _is_annual(row: dict) -> bool:
    """Instant facts (balance sheet) always qualify; duration facts must span a year."""
    start = row.get("start")
    if start is None:
        return True
    span = (dt.date.fromisoformat(row["end"]) - dt.date.fromisoformat(start)).days
    return 340 <= span <= 400


def annual(facts: dict, tags: tuple[str, ...]) -> dict[int, float]:
    """Fiscal-year series for the best tag in the chain, keyed by period-end year.

    Keyed by the calendar year of the period end, not by EDGAR's `fy` field — a
    single 10-K carries three years of income statement under one `fy`.

    **Not simply the first tag with data.** A company that migrates tags mid-life
    keeps reporting the retired one for its old years: AMPH moved to the
    NCI-inclusive equity tag in 2022, and still carries `StockholdersEquity` for
    2011-2021. Taking the first chain entry with any data would return a series
    that silently stops in 2021 — and ROIC computed from stale years is far worse
    than no ROIC, because nothing about it looks wrong. So the chain prefers the
    most *current* series, breaking ties by length and then by chain order.

    An empty dict means no tag reported anything. The caller flags XBRL_INCOMPLETE;
    it never excludes on absence (PRD §2.4).
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    candidates = []
    for rank, tag in enumerate(tags):
        units = us_gaap.get(tag, {}).get("units", {})
        series = _by_year(units.get("USD") or units.get("shares") or [])
        if series:
            candidates.append((max(series), len(series), -rank, series))
    if not candidates:
        return {}

    # Keep only series that run to within a year of the freshest — a tag retired
    # three years ago is a trap, however many years of history it carries.
    freshest = max(c[0] for c in candidates)
    current = [c for c in candidates if c[0] >= freshest - 1]
    return max(current, key=lambda c: (c[1], c[2]))[3]


def _by_year(rows: list[dict]) -> dict[int, float]:
    """Latest-filed value per year, so a restatement supersedes the original."""
    best: dict[int, tuple[str, float]] = {}
    for row in rows:
        if row.get("form") not in ANNUAL_FORMS or row.get("val") is None:
            continue
        if not row.get("end") or not _is_annual(row):
            continue
        year, filed = int(row["end"][:4]), row.get("filed", "")
        previous = best.get(year)
        if previous is None or filed > previous[0]:
            best[year] = (filed, float(row["val"]))
    return {year: value for year, (_, value) in best.items()}
