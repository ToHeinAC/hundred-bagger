"""edgartools adapter — Form 4 insider transactions and 8-K text.

`src/xbrl.py` is the raw-JSON half of EDGAR (companyfacts, Stage 3). This is the
*documents* half: the filings a human would actually read. Stage 4 reads 10-K
Item 1 through `src/moat.py`; Phase 3 reads Form 4 and 8-K through here.

Everything edgartools-shaped is quarantined in this module on purpose. It is an
unofficial client over a government API, both of which change, and the callers
(`signals.py`, `monitor.py`) should never have to know that `market_trades`
returns `None` rather than an empty frame.
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from edgar import Company, set_identity

from src import config

# Form 4 transaction codes. Only "P" is an open-market purchase made with the
# insider's own money; "A" is a grant and "M" an option exercise, and counting
# either as a buy signal is the standard way to fool yourself with Form 4 data.
OPEN_MARKET_BUY = "P"
ACQUIRED = "A"


def identity() -> None:
    """The SEC requires a contact email on every request. Fail loudly, not later."""
    if not config.SEC_USER_AGENT:
        raise SystemExit(
            "SEC_USER_AGENT is unset. EDGAR rejects anonymous requests.\n"
            "Set it in .env, e.g.  SEC_USER_AGENT=Your Name your@email.com"
        )
    set_identity(config.SEC_USER_AGENT)


def throttle() -> None:
    """<= 10 req/s (PRD §9). Enforced in code because exceeding it gets the user's
    IP blocked. Conservative: edgartools may issue more than one request per call."""
    time.sleep(config.SEC_SLEEP)


def _since(days: int) -> str:
    """edgartools' open-ended date-range syntax: everything filed on or after."""
    return f"{dt.date.today() - dt.timedelta(days=days):%Y-%m-%d}:"


# --- Form 4 -----------------------------------------------------------------


def _owner(form4) -> tuple[str, str]:
    owners = form4.reporting_owners.owners
    if not owners:
        return ("unknown", "")
    return (owners[0].name, owners[0].position or "")


def _transactions(form4, filed_date) -> list[dict]:
    """One dict per non-derivative trade on a single Form 4.

    `market_trades` is `None` — not an empty frame — when the filing has no
    non-derivative transactions (an options-only filing), so it is guarded rather
    than truthiness-tested. `Code` may itself be None when the filer omitted the
    transaction coding, which is why the comparison is `== "P"` and not a string op.
    """
    trades = form4.market_trades
    if trades is None or trades.empty:
        return []

    name, title = _owner(form4)
    rows = []
    for t in trades.itertuples():
        shares, price = t.Shares, t.Price
        rows.append({
            "filed_date": filed_date,
            "transaction_date": dt.date.fromisoformat(str(t.Date)[:10]),
            "insider_name": name,
            "insider_title": title,
            # An open-market buy is code P *acquiring* shares. A "P" that disposes
            # is not a purchase, whatever the code says.
            "transaction_type": (
                OPEN_MARKET_BUY
                if t.Code == OPEN_MARKET_BUY and t.AcquiredDisposed == ACQUIRED
                else str(t.Code or "?")
            ),
            "shares": None if shares is None else int(shares),
            "price": None if price is None else float(price),
            "value": None if shares is None or price is None else float(shares) * float(price),
        })
    return rows


def is_foreign_issuer(ticker: str) -> bool:
    """True for a foreign private issuer (files 20-F/40-F, not 10-K).

    Such issuers are exempt from Section 16, so they never file Form 4. An empty
    insider result for one of them means "no insider data exists", not "no
    insider bought" — the caller must not conflate the two. `is_foreign` comes
    from the submissions metadata the Company object already carries."""
    throttle()
    return bool(Company(ticker).is_foreign)


def insider_transactions(ticker: str, lookback_days: int) -> list[dict]:
    """Every non-derivative Form 4 trade for a ticker in the lookback window.

    Amendments (4/A) are excluded: edgartools includes them by default, and a
    restated filing would otherwise double-count the transaction it restates.
    """
    throttle()
    found = Company(ticker).get_filings(form="4", amendments=False)
    if found is None:
        return []
    found = found.filter(filing_date=_since(lookback_days))

    rows = []
    for filing in found:
        throttle()  # each obj() pulls the filing's XML
        try:
            rows.extend(_transactions(filing.obj(), filing.filing_date))
        except Exception:  # one malformed Form 4 must not lose the other twenty
            continue
    return rows


# --- 8-K --------------------------------------------------------------------


def _header(ticker: str, filing, items: str) -> str:
    return "\n".join([
        f"# ticker:       {ticker}",
        f"# form:         {filing.form}",
        f"# filing_date:  {filing.filing_date}",
        f"# accession:    {filing.accession_no}",
        f"# items:        {items or 'not stated'}",
    ]) + "\n\n"


def write_recent_8k(ticker: str, out_dir: Path) -> Path | None:
    """Concatenate the lookback window's 8-K text to {out_dir}/{TICKER}.txt.

    Returns None when the company filed no 8-K — which is itself the common case
    and a perfectly good outcome, not an error. The reported item numbers go in
    each header: `filing.items` comes from SEC metadata and costs no download,
    and Item 4.02 (non-reliance / restatement) is legible before a word is read.
    """
    throttle()
    found = Company(ticker).get_filings(form="8-K")
    if found is None:
        return None
    found = found.filter(filing_date=_since(config.EIGHTK_LOOKBACK_DAYS))
    if not len(found):
        return None

    chunks = []
    for filing in found:
        throttle()
        items = getattr(filing, "items", "") or ""
        try:
            body = filing.text() or ""
        except Exception:
            body = "(text unavailable)"
        chunks.append(_header(ticker, filing, items) + body.strip()[: config.EIGHTK_CHAR_CAP])

    path = out_dir / f"{ticker}.txt"
    path.write_text("\n\n---\n\n".join(chunks))
    return path
