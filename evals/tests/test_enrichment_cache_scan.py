"""The committed cache gets the scan on GitHub CI too (RAG-02, security review finding 2).

CI's scan step passes explicit paths, so `EVAL_ROOTS` does not reach it, and the row stays out of
the workflow. `uv run pytest` runs in CI, so this is where a dirty cache fails the build.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_llm_replay.scan import load_canaries, scan_paths
from northwind_rag.enrichment import EnrichmentCache, apply_cache, parents_of, plan_work
from northwind_rag.sources import build_chunks, context_problem

pytestmark = pytest.mark.req("RAG-02")

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "data" / "enrichment"
FIXTURES = REPO / "data" / "fixtures"


def test_the_committed_cache_passes_the_regex_pan_and_canary_scan() -> None:
    assert CACHE.exists()
    findings = scan_paths([CACHE], canaries=load_canaries(REPO, required=True))
    assert [(f.kind, f.line) for f in findings] == []


def test_every_chunk_has_an_entry_and_every_stored_sentence_passes_the_checks() -> None:
    chunks = build_chunks(FIXTURES)
    cache = EnrichmentCache(CACHE)
    assert plan_work(chunks, cache) == []
    apply_cache(chunks, cache)  # re-checks every context, and refuses a missing or edited entry


def test_no_planted_review_passed_anything_into_its_sentence() -> None:
    """Hard rules 2 and 6, on what the model really wrote for all 15 traps."""
    key = json.loads((FIXTURES / "_answer_key.json").read_text(encoding="utf-8"))
    chunks = build_chunks(FIXTURES)
    parents = parents_of(chunks)
    cache = EnrichmentCache(CACHE)
    traps = {entry["review_id"] for entry in key["injection_reviews"]}
    found = [c for c in chunks if c.source_id in traps]
    assert {c.source_id for c in found} == traps
    for chunk in found:
        entry = cache.get(chunk, parents[chunk.chunk_id])
        assert entry is not None
        if entry.context is not None:
            assert context_problem(entry.context, chunk) is None, chunk.chunk_id


def test_the_scan_reports_a_card_number_in_the_cache_directory(tmp_path: Path) -> None:
    dirty = tmp_path / "public.jsonl"
    dirty.write_text("4111" + "1111" * 3 + "\n", encoding="utf-8")
    assert [f.kind for f in scan_paths([tmp_path])] == ["luhn-valid-pan"]
