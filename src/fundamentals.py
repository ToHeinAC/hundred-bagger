"""Arithmetic over a year-keyed XBRL fact table: ROIC, Piotroski F, Altman Z.

Pure functions, no I/O. `src/xbrl.py` gets the facts, this turns them into the
three numbers Stage 3 scores on, and `src/roic.py` orchestrates and persists.

Every function returns None rather than raising when an input is absent: a missing
XBRL tag is the *normal* case for a microcap filer, not an error (PRD §14).
"""

from __future__ import annotations

import statistics

from src import config

# name -> {fiscal year: value}, as built by roic.CHAINS
Table = dict[str, dict[int, float]]


# --- helpers ----------------------------------------------------------------


def at(table: Table, name: str, year: int, default: float | None = None) -> float | None:
    return table.get(name, {}).get(year, default)


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def _signal(condition: bool) -> int:
    """A Piotroski signal we cannot evaluate scores 0 — it is never awarded on faith."""
    return 1 if condition else 0


def _gt(a: float | None, b: float | None) -> int:
    return _signal(a is not None and b is not None and a > b)


def _lt(a: float | None, b: float | None) -> int:
    return _signal(a is not None and b is not None and a < b)


def cagr(series: dict[int, float], years: int = config.ROIC_MEDIAN_YEARS) -> float | None:
    """CAGR across the last `years` intervals of a year-keyed series."""
    available = sorted(series)[-(years + 1) :]
    if len(available) < 2:
        return None
    oldest, newest = series[available[0]], series[available[-1]]
    if oldest <= 0 or newest <= 0:
        return None
    return (newest / oldest) ** (1 / (available[-1] - available[0])) - 1


def ebitda(table: Table) -> dict[int, float]:
    """EBIT + D&A, for the years where both are reported."""
    depreciation = table.get("depreciation", {})
    return {
        year: value + depreciation[year]
        for year, value in table.get("ebit", {}).items()
        if year in depreciation
    }


# --- ROIC -------------------------------------------------------------------


def roic(table: Table, year: int) -> float | None:
    """NOPAT / invested capital, for one fiscal year.

    Invested capital = equity + total debt - cash. Absent debt and cash tags are
    read as **zero**: for those two, absence overwhelmingly means the company has
    none, and reading them as unknown would strip debt-free companies of a score.
    """
    ebit, equity = at(table, "ebit", year), at(table, "equity", year)
    if ebit is None or equity is None:
        return None
    debt = (at(table, "long_term_debt", year, 0.0) or 0.0) + (
        at(table, "short_term_debt", year, 0.0) or 0.0
    )
    invested = equity + debt - (at(table, "cash", year, 0.0) or 0.0)
    if invested <= 0:
        return None  # a negative capital base makes the ratio meaningless, not stellar
    return ebit * (1 - _tax_rate(table, year)) / invested


def _tax_rate(table: Table, year: int) -> float:
    """Effective rate, falling back to statutory when it is absent or absurd."""
    rate = ratio(at(table, "tax", year), at(table, "pretax", year))
    if rate is None or not 0.0 <= rate <= 0.5:
        return config.DEFAULT_TAX_RATE
    return rate


def roic_median(table: Table) -> float | None:
    """Median ROIC over the last ROIC_MEDIAN_YEARS fiscal years we can compute."""
    values = [r for year in sorted(table.get("equity", {})) if (r := roic(table, year)) is not None]
    if not values:
        return None
    return statistics.median(values[-config.ROIC_MEDIAN_YEARS :])


# --- Piotroski F (0-9) ------------------------------------------------------


def piotroski_f(table: Table, year: int) -> int | None:
    """The standard nine signals. None when there is no prior year to compare to."""
    prior = year - 1
    assets, assets_prior = at(table, "assets", year), at(table, "assets", prior)
    if not assets or not assets_prior:
        return None

    net_income, net_income_prior = at(table, "net_income", year), at(table, "net_income", prior)
    cfo = at(table, "cfo", year)
    leverage = ratio(at(table, "long_term_debt", year, 0.0), assets)
    leverage_prior = ratio(at(table, "long_term_debt", prior, 0.0), assets_prior)

    return sum([
        _gt(ratio(net_income, assets), 0.0),                                      # ROA positive
        _gt(cfo, 0.0),                                                            # CFO positive
        _gt(ratio(net_income, assets), ratio(net_income_prior, assets_prior)),    # ROA rising
        _gt(cfo, net_income),                                                     # accrual quality
        _lt(leverage, leverage_prior),                                            # deleveraging
        _gt(_current_ratio(table, year), _current_ratio(table, prior)),           # liquidity rising
        _signal(_no_issuance(table, year, prior)),                                # no dilution
        _gt(_margin(table, year), _margin(table, prior)),                         # margin rising
        _gt(ratio(at(table, "revenue", year), assets),
            ratio(at(table, "revenue", prior), assets_prior)),                    # turnover rising
    ])


def _current_ratio(table: Table, year: int) -> float | None:
    return ratio(at(table, "assets_current", year), at(table, "liabilities_current", year))


def _margin(table: Table, year: int) -> float | None:
    return ratio(at(table, "gross_profit", year), at(table, "revenue", year))


def _no_issuance(table: Table, year: int, prior: int) -> bool:
    shares, shares_prior = at(table, "shares", year), at(table, "shares", prior)
    return shares is not None and shares_prior is not None and shares <= shares_prior


# --- Altman Z ---------------------------------------------------------------


def altman_z(table: Table, year: int, market_cap: float | None) -> float | None:
    """The public-company Z-score. Market cap is the one input XBRL cannot supply."""
    assets, liabilities = at(table, "assets", year), at(table, "liabilities", year)
    if not assets or not liabilities or not market_cap:
        return None
    working_capital = (at(table, "assets_current", year, 0.0) or 0.0) - (
        at(table, "liabilities_current", year, 0.0) or 0.0
    )
    return (
        1.2 * working_capital / assets
        + 1.4 * (at(table, "retained_earnings", year, 0.0) or 0.0) / assets
        + 3.3 * (at(table, "ebit", year, 0.0) or 0.0) / assets
        + 0.6 * market_cap / liabilities
        + 1.0 * (at(table, "revenue", year, 0.0) or 0.0) / assets
    )
