"""PRD §11 — `grep -r anthropic src/ pyproject.toml` must return nothing.

Claude Code is the reasoning engine; the app never calls an LLM. If an SDK ever
sneaks back in, this is the test that fails.
"""

from __future__ import annotations

from src.config import REPO_ROOT

TARGETS = [*(REPO_ROOT / "src").rglob("*.py"), REPO_ROOT / "src" / "schema.sql",
           REPO_ROOT / "pyproject.toml"]


def test_no_llm_sdk_anywhere_in_src_or_dependencies():
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in TARGETS
        if "anthropic" in p.read_text().lower()
    ]
    assert offenders == [], f"LLM SDK reference found in: {offenders}"
