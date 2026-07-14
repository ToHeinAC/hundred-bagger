"""The sell-trigger table — pure functions over a year-keyed XBRL fact table.

`src/fundamentals.py` is to `src/roic.py` what this is to `src/monitor.py`: the
arithmetic, with no I/O, so the rules can be read and tested without EDGAR.

Two rules shape every trigger here, and both are deliberate:

1. **One bad year is not a sell.** Every trend rule needs `SELL_TREND_YEARS`
   consecutive bad years, or a move too large to be noise. Selling a compounder on
   one soft year is how you lose the 100-bagger — the whole point of the funnel is
   a ten-year hold, and a screen that panics annually cannot deliver one.
2. **A trigger never fires on missing data** — the same invariant as Stages 2–4.
   An absent XBRL tag is a coverage gap, not a thesis break (PRD §2.4).

Red flags (restatement, going concern, …) are *not* here: they are read out of
8-K text by Claude Code, because no regex finds them honestly. See `src/monitor.py`.
"""

from __future__ import annotations

from src import config, fundamentals

# recommended_action, best first. The count of mechanical flags indexes this;
# any red flag jumps straight to SELL.
ACTIONS = ("HOLD", "REVIEW", "TRIM", "SELL")


def _last(series: dict[int, float], n: int = 2) -> list[int]:
    """The last n years present in a series, oldest first."""
    return sorted(series)[-n:]


def roic_deterioration(table: dict, _cap: float | None) -> tuple[str, str] | None:
    """ROIC under the floor for SELL_TREND_YEARS running.

    The headline trigger: the number the entire funnel selected on has stopped
    being true. Everything else on this list is corroboration.
    """
    values = [
        r for year in sorted(table.get("equity", {}))
        if (r := fundamentals.roic(table, year)) is not None
    ]
    recent = values[-config.SELL_TREND_YEARS :]
    if len(recent) < config.SELL_TREND_YEARS:
        return None
    if all(v < config.SELL_ROIC_FLOOR for v in recent):
        shown = ", ".join(f"{v:.1%}" for v in recent)
        return ("ROIC_DETERIORATION", f"ROIC {shown} — below the {config.SELL_ROIC_FLOOR:.0%} floor")
    return None


def revenue_decline(table: dict, _cap: float | None) -> tuple[str, str] | None:
    """Revenue lower than the year before, SELL_TREND_YEARS times running."""
    revenue = table.get("revenue", {})
    years = _last(revenue, config.SELL_TREND_YEARS + 1)
    if len(years) < config.SELL_TREND_YEARS + 1:
        return None
    if all(revenue[b] < revenue[a] for a, b in zip(years, years[1:])):
        return (
            "REVENUE_DECLINE",
            f"revenue fell {config.SELL_TREND_YEARS} years running "
            f"({revenue[years[0]]:,.0f} -> {revenue[years[-1]]:,.0f})",
        )
    return None


def margin_compression(table: dict, _cap: float | None) -> tuple[str, str] | None:
    """Operating margin down more than SELL_MARGIN_DROP against two years ago."""
    years = _last(table.get("revenue", {}), config.SELL_TREND_YEARS + 1)
    if len(years) < config.SELL_TREND_YEARS + 1:
        return None
    margin = lambda y: fundamentals.ratio(  # noqa: E731
        fundamentals.at(table, "ebit", y), fundamentals.at(table, "revenue", y)
    )
    then, now = margin(years[0]), margin(years[-1])
    if then is None or now is None or then - now <= config.SELL_MARGIN_DROP:
        return None
    return (
        "MARGIN_COMPRESSION",
        f"operating margin {then:.1%} -> {now:.1%} over {years[-1] - years[0]}y",
    )


def dilution(table: dict, _cap: float | None) -> tuple[str, str] | None:
    """Share count up more than SELL_DILUTION_PCT year-over-year."""
    years = _last(table.get("shares", {}), 2)
    if len(years) < 2:
        return None
    prior, latest = table["shares"][years[0]], table["shares"][years[1]]
    if prior <= 0:
        return None
    growth = latest / prior - 1
    if growth > config.SELL_DILUTION_PCT:
        return ("DILUTION", f"share count +{growth:.1%} year-over-year")
    return None


def distress(table: dict, market_cap: float | None) -> tuple[str, str] | None:
    """Altman Z back in the bankruptcy zone — the floor Stage 3 already screened on.

    Needs `universe.market_cap`, the one input XBRL cannot supply, so a ticker
    with no cap yields no trigger rather than a wrong one.
    """
    years = sorted(table.get("assets", {}))
    if not years:
        return None
    z = fundamentals.altman_z(table, years[-1], market_cap)
    if z is not None and z < config.ALTMAN_Z_DISTRESS:
        return ("DISTRESS_ZONE", f"Altman Z {z:.2f} — below {config.ALTMAN_Z_DISTRESS}")
    return None


TABLE = (roic_deterioration, revenue_decline, margin_compression, dilution, distress)


def fired(table: dict, market_cap: float | None = None) -> list[tuple[str, str]]:
    """Every mechanical sell trigger that fires, each with the number that fired it."""
    return [f for rule in TABLE if (f := rule(table, market_cap)) is not None]


def recommend(mechanical: list[str], red_flags: list[str]) -> str:
    """Flags → HOLD | REVIEW | TRIM | SELL.

    A red flag is categorical, not cumulative: one restatement is a sell however
    healthy the arithmetic looks. Mechanical triggers accumulate instead, because
    any one of them in isolation has an innocent explanation and three of them
    do not.
    """
    if red_flags:
        return "SELL"
    return ACTIONS[min(len(mechanical), len(ACTIONS) - 1)]
