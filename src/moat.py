"""Stage 4 — moat scoring: the two Python halves of fetch → judge → save.

Claude Code is the judge. The rubric lives in `.claude/skills/hunt-moat/SKILL.md`
and deliberately nowhere else — this module never sees a prompt. It puts the
annual-report Business narrative on disk (`fetch`) and validates the JSON that
comes back (`save`).

Python owns the arithmetic: Claude supplies the six dimension scores, `moat_total`
is summed here and `moat_score` derived here. A moat miss is not an exclusion —
below-gate tickers keep their stage and status (PRD §2.4, flag don't auto-delete).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from edgar import Company

from src import config, db
from src.filings import identity as _identity
from src.filings import throttle as _throttle

# Item 1 runs to 80k+ chars for some filers. Claude has to actually read this,
# so cap it and say in the file when we did.
ITEM1_CHAR_CAP = 40_000


# --- fetch ------------------------------------------------------------------


def _header(ticker: str, filing, truncated: int | None) -> str:
    # A 10-K carries the Business narrative in Item 1; a 20-F (every foreign
    # private issuer, US-listed ADRs included) carries it in Item 4.
    item = "4 (Information on the Company)" if filing.form == "20-F" else "1 (Business)"
    lines = [
        f"# ticker:       {ticker}",
        f"# company:      {filing.company}",
        f"# form:         {filing.form}",
        f"# filing_date:  {filing.filing_date}",
        f"# accession:    {filing.accession_no}",
        f"# item:         {item}",
    ]
    if truncated:
        lines.append(f"# TRUNCATED:    first {ITEM1_CHAR_CAP:,} of {truncated:,} chars")
    return "\n".join(lines) + "\n\n"


def fetch_ticker(ticker: str, out_dir: Path) -> Path:
    """Write one ticker's annual-report Business section to {out_dir}/{TICKER}.txt.

    US filers file a 10-K (Item 1, Business); foreign private issuers — every
    US-listed ADR among them — file a 20-F (Item 4, Information on the Company).
    Same qualitative narrative, and edgar exposes both as `.business`, so take
    whichever the company files.

    `amendments=False` is load-bearing: edgar defaults it to True, so a company
    that has filed a 10-K/A gets the amendment back from `latest()` — and an
    amendment restates only the items it changes, so `.business` is empty and the
    ticker is lost. Ask for unamended annual reports and the real Item 1 returns.
    """
    _throttle()
    filings = Company(ticker).get_filings(form=["10-K", "20-F"], amendments=False)
    filing = filings.latest()
    if filing is None:
        raise ValueError("no unamended 10-K or 20-F on file")

    _throttle()
    text = (filing.obj().business or "").strip()
    if not text:
        raise ValueError(f"{filing.form} Business section is empty")

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


def _tam(payload: dict) -> int | None:
    """tam_usd: whole USD, or None when no defensible figure exists.

    The key is required but the value is nullable, and that asymmetry is the
    point — "I could not establish a TAM" has to be sayable, or the answer gets
    guessed. Unbounded above, so `_score` does not fit.
    """
    if "tam_usd" not in payload:
        raise ValueError("tam_usd: required (use null if no defensible figure exists)")
    value = payload["tam_usd"]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"tam_usd: expected whole USD as an int, or null, got {value!r}")
    if value <= 0:
        raise ValueError(f"tam_usd: {value} must be positive")
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
        "tam_usd": _tam(payload),
        "tam_basis": _text(payload, "tam_basis"),
    }


def _usd(value: float) -> str:
    return f"${value / 1e9:.1f}B" if value >= 1e9 else f"${value / 1e6:.0f}M"


def _tam_alert(con, ticker: str, tam_usd: int | None) -> float | None:
    """Raise the 100x plausibility alert when the arithmetic does not work.

    Never touches the score, the stage or the status — a company can have a fine
    moat and still be unable to 100-bag, and that is the finding worth surfacing
    (docs/first-principles.md §5). An unknown TAM raises nothing: a gap is not a
    failure. Returns the headroom for the caller to report.
    """
    cap = db.market_cap(con, ticker)
    headroom = config.tam_headroom(tam_usd, cap)
    if headroom is not None and headroom <= config.TAM_HEADROOM_MIN:
        db.add_alert(
            con, ticker, "tam", "MEDIUM",
            f"100x implausible — TAM {_usd(tam_usd)} is {headroom:.1f}x the "
            f"{_usd(cap)} cap (need >{config.TAM_HEADROOM_MIN:.0f}x)",
        )
    return headroom


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

    headroom = _tam_alert(con, ticker, cols["tam_usd"])
    return {"ticker": ticker, "passed": passed, "tam_headroom": headroom, **cols}


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
    # Reported next to the gate, never folded into it — see docs/first-principles.md §5.
    if r["tam_headroom"] is None:
        print(f"{' ' * len(r['ticker'])}  TAM headroom: not established ({r['tam_basis']})")
    else:
        verdict = "100x implausible" if r["tam_headroom"] <= config.TAM_HEADROOM_MIN else "100x fits"
        print(
            f"{' ' * len(r['ticker'])}  TAM {_usd(r['tam_usd'])}  headroom "
            f"{r['tam_headroom']:.1f}x  -> {verdict}"
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
