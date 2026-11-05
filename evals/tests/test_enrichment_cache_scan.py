"""The committed cache gets the scan on GitHub CI too (RAG-02, security review finding 2).

CI's scan step passes explicit paths, so `EVAL_ROOTS` does not reach it, and the row stays out of
the workflow. `uv run pytest` runs in CI, so this is where a dirty cache fails the build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from northwind_llm_replay.scan import load_canaries, scan_paths

pytestmark = pytest.mark.req("RAG-02")

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "data" / "enrichment"


def test_the_committed_cache_passes_the_regex_pan_and_canary_scan() -> None:
    if not CACHE.exists():
        pytest.skip("no cache yet: it is committed after the spending window")
    findings = scan_paths([CACHE], canaries=load_canaries(REPO, required=True))
    assert [(f.kind, f.line) for f in findings] == []


def test_the_scan_reports_a_card_number_in_the_cache_directory(tmp_path: Path) -> None:
    dirty = tmp_path / "public.jsonl"
    dirty.write_text("4111" + "1111" * 3 + "\n", encoding="utf-8")
    assert [f.kind for f in scan_paths([tmp_path])] == ["luhn-valid-pan"]
