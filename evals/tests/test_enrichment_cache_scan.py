"""The committed cache gets the scan on GitHub CI too (RAG-02, security review finding 2).

CI's scan step passes explicit paths, so `EVAL_ROOTS` does not reach it, and the row stays out of
the workflow. `uv run pytest` runs in CI, so this is where a dirty cache fails the build.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_data.planted import APPENDED_REVIEWS
from northwind_llm_replay.scan import load_canaries, scan_paths
from northwind_rag.enrichment import (
    EnrichmentCache,
    apply_cache,
    internal_shingles,
    parents_of,
    plan_work,
)
from northwind_rag.sources import build_chunks, context_problem

pytestmark = pytest.mark.req("RAG-02")

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "data" / "enrichment"
FIXTURES = REPO / "data" / "fixtures"


def test_the_committed_cache_passes_the_regex_pan_and_canary_scan() -> None:
    assert CACHE.exists()
    findings = scan_paths([CACHE], canaries=load_canaries(REPO, required=True))
    assert [(f.kind, f.line) for f in findings] == []


def test_every_allowed_chunk_has_an_entry_and_its_sentence_passes_the_checks() -> None:
    chunks = build_chunks(FIXTURES)
    cache = EnrichmentCache(CACHE)
    assert plan_work(chunks, cache) == []
    apply_cache(chunks, cache)  # re-checks each allowed context, and refuses an edited entry


def test_the_unused_review_sentences_pass_the_checks_too() -> None:
    """`apply_cache` skips reviews, so nothing else re-reads them. They stay held to the content
    checks in case the list is ever widened. The stored-form and chunk checks run only at use."""
    chunks = build_chunks(FIXTURES)
    parents = parents_of(chunks)
    internal = internal_shingles(chunks)
    cache = EnrichmentCache(CACHE)
    reviews = [c for c in chunks if c.source_type == "review"]
    entries = [(c, cache.get(c, parents[c.chunk_id])) for c in reviews]
    kept = [(c, e.context) for c, e in entries if e is not None and e.context is not None]
    assert len(kept) > 1000
    assert [c.chunk_id for c, text in kept if context_problem(text, c, internal=internal)] == []


def test_no_planted_review_passed_anything_into_its_sentence() -> None:
    """Hard rules 2 and 6, on what the model really wrote for all 15 first-run traps. Reviews have
    not been enriched since (`CONTEXT_SOURCE_TYPES`), so a trap appended later (R01221, DATA_VERSION
    2, row #13b) can have no entry, and must not: the user's (b1), stop 5."""
    key = json.loads((FIXTURES / "_answer_key.json").read_text(encoding="utf-8"))
    chunks = build_chunks(FIXTURES)
    parents = parents_of(chunks)
    cache = EnrichmentCache(CACHE)
    appended = {planted.trap_id for planted in APPENDED_REVIEWS}
    entries = key["injection_reviews"]
    traps = {entry["review_id"] for entry in entries if entry["trap_id"] not in appended}
    later = {entry["review_id"] for entry in entries if entry["trap_id"] in appended}
    # R01222 to R01224 were appended at DATA_VERSION 3 (row #32), also never enriched.
    assert len(traps) == 15 and later == {"R01221", "R01222", "R01223", "R01224"}
    for chunk in (c for c in chunks if c.source_id in later):
        assert cache.get(chunk, parents[chunk.chunk_id]) is None, chunk.chunk_id
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
