"""Stage 4 — moat scoring: the two Python halves of fetch → judge → save.

Claude Code is the judge. The rubric lives in `.claude/skills/hunt-moat/SKILL.md`
and deliberately nowhere else — this module never sees a prompt. It puts 10-K
Item 1 text on disk (`fetch`) and validates the JSON that comes back (`save`).

Python owns the arithmetic: Claude supplies the six dimension scores, `moat_total`
is summed here and `moat_score` derived here. A moat miss is not an exclusion —
below-gate tickers keep their stage and status (PRD §2.4, flag don't auto-delete).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from edgar import Company, set_identity

from src import config, db

# Item 1 runs to 80k+ chars for some filers. Claude has to actually read this,
# so cap it and say in the file when we did.
ITEM1_CHAR_CAP = 40_000


# --- fetch ------------------------------------------------------------------


def _identity() -> None:
    """The SEC requires a contact email on every request. Fail loudly, not later."""
    if not config.SEC_USER_AGENT:
        raise SystemExit(
            "SEC_USER_AGENT is unset. EDGAR rejects anonymous requests.\n"
            "Set it in .env, e.g.  SEC_USER_AGENT=Your Name your@email.com"
        )
    set_identity(config.SEC_USER_AGENT)


def _throttle() -> None:
    """<= 10 req/s (PRD §9). Exceeding it gets the user's IP blocked, so this is
    enforced in code rather than left to discipline. Conservative: edgartools may
    issue more than one request per call, and we sleep before each entry point."""
    time.sleep(config.SEC_SLEEP)


def _header(ticker: str, filing, truncated: int | None) -> str:
    lines = [
        f"# ticker:       {ticker}",
        f"# company:      {filing.company}",
        f"# form:         {filing.form}",
        f"# filing_date:  {filing.filing_date}",
        f"# accession:    {filing.accession_no}",
        "# item:         1 (Business)",
    ]
    if truncated:
        lines.append(f"# TRUNCATED:    first {ITEM1_CHAR_CAP:,} of {truncated:,} chars")
    return "\n".join(lines) + "\n\n"


def fetch_ticker(ticker: str, out_dir: Path) -> Path:
    """Write one ticker's 10-K Item 1 to {out_dir}/{TICKER}.txt."""
    _throttle()
    filings = Company(ticker).get_filings(form="10-K")
    filing = filings.latest()
    if filing is None:
        raise ValueError("no 10-K on file")

    _throttle()
    text = (filing.obj().business or "").strip()
    if not text:
        raise ValueError("10-K Item 1 (Business) is empty")

    full_len = len(text)
    truncated = full_len if full_len > ITEM1_CHAR_CAP else None
    path = out_dir / f"{ticker}.txt"
    path.write_text(_header(ticker, filing, truncated) + text[:ITEM1_CHAR_CAP])
    return path


# --- save -------------------------------------------------------------------


def _score(payload: dict, key: str, maximum: int) -> int:
    value = payload.get(key)
    # bool is an int subclass in Python; a `true` where a score belongs is a bug.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key}: expected int 0-{maximum}, got {value!r}")
    if not 0 <= value <= maximum:
        raise ValueError(f"{key}: {value} out of range 0-{maximum}")
    return value


def _text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if isinstance(value, list):  # key_risks is naturally a list; store it flat
        return "; ".join(str(v) for v in value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key}: expected a non-empty string, got {value!r}")
    return value.strip()


def validate(payload: dict) -> dict:
    """Claude's JSON → `scores` columns. Raises ValueError on anything malformed."""
    dimensions = {
        f"moat_{d}": _score(payload, d, config.MOAT_DIMENSION_MAX)
        for d in config.MOAT_DIMENSIONS
    }
    founder_led = payload.get("founder_led")
    if not isinstance(founder_led, bool):
        raise ValueError(f"founder_led: expected true or false, got {founder_led!r}")

    runway = payload.get("reinvest_runway")
    if runway not in config.REINVEST_RUNWAYS:
        raise ValueError(
            f"reinvest_runway: expected one of {list(config.REINVEST_RUNWAYS)}, got {runway!r}"
        )

    return {
        **dimensions,
        "moat_total": sum(dimensions.values()),
        "moat_durability": _score(payload, "durability", config.MOAT_DURABILITY_MAX),
        "founder_led": founder_led,
        "reinvest_runway": runway,
        "moat_notes": _text(payload, "notes"),
        "key_risks": _text(payload, "key_risks"),
    }


def save_ticker(con, ticker: str, payload: dict) -> dict:
    """Validate → persist → apply the Stage 4 gate. Idempotent for a score_date."""
    cols = validate(payload)
    cols["moat_score"] = config.moat_score(cols["moat_total"], cols["moat_durability"])
    db.upsert_score(con, ticker, **cols)

    passed = (
        cols["moat_total"] >= config.MOAT_TOTAL_GATE
        and cols["moat_durability"] >= config.MOAT_DURABILITY_GATE
    )
    if passed:  # Stage 4 survivors are Watchlist B — the funnel's output
        db.set_stage(con, [ticker], 4)
        db.set_status(con, [ticker], "watchlist")
    return {"ticker": ticker, "passed": passed, **cols}


# --- CLI --------------------------------------------------------------------


def _run_fetch(args) -> None:
    _identity()
    config.MOAT_INPUT_DIR.mkdir(parents=True, exist_ok=True)

    with db.connect() as con:
        tickers = db.get_universe(con, stage=args.stage, status="active")["ticker"].tolist()
    if args.limit:
        tickers = tickers[: args.limit]

    written, skipped, failed = 0, 0, []
    for i, t in enumerate(tickers, 1):
        path = config.MOAT_INPUT_DIR / f"{t}.txt"
        if path.exists() and not args.force:
            skipped += 1
            continue
        try:
            fetch_ticker(t, config.MOAT_INPUT_DIR)
            written += 1
            print(f"[{i}/{len(tickers)}] {t:<6} -> {path}")
        except Exception as e:  # EDGAR breaks in many ways; never abort the batch
            failed.append(t)
            print(f"[{i}/{len(tickers)}] {t:<6} FETCH FAILED: {type(e).__name__}: {e}")

    print(f"\nWrote {written}  |  skipped (already fetched) {skipped}  |  failures {len(failed)}")
    print(f"Item 1 text is in {config.MOAT_INPUT_DIR}/ — read each file, then save a score.")


def _run_save(args) -> None:
    raw = Path(args.json_file).read_text() if args.json_file else args.json
    payload = json.loads(raw)
    with db.connect() as con:
        r = save_ticker(con, args.ticker, payload)

    gate = "ADVANCED to Stage 4 (Watchlist B)" if r["passed"] else "below gate — not advanced"
    print(
        f"{r['ticker']}  moat_total {r['moat_total']}/18  durability "
        f"{r['moat_durability']}/5  -> moat_score {r['moat_score']}/10  |  {gate}"
    )


def _main() -> None:
    ap = argparse.ArgumentParser(prog="src.moat")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="10-K Item 1 -> data/moat_input/*.txt")
    f.add_argument("--stage", type=int, default=3, help="universe stage to fetch (default 3)")
    f.add_argument("--limit", type=int, help="cap the batch size")
    f.add_argument("--force", action="store_true", help="re-fetch tickers already on disk")
    f.set_defaults(func=_run_fetch)

    s = sub.add_parser("save", help="persist Claude's moat JSON")
    s.add_argument("--ticker", required=True)
    src = s.add_mutually_exclusive_group(required=True)
    src.add_argument("--json", help="the moat JSON as a string")
    src.add_argument("--json-file", help="path to a file holding the moat JSON")
    s.set_defaults(func=_run_save)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    _main()
