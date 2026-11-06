"""Only product, variant and document chunks get a context (RAG-02, hard rule 6, decision 0010).

A security rule, not a recall choice. Reviews are written by shoppers, and a planted review's own
sentence lifted it into top 5s it had no place in: R00069 went from rank 33 to 5 on a plain spec
question. The chunk body is the attack, and retrieval returns the body.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from northwind_rag.enrichment import (
    ENRICH_MODEL,
    EnrichmentCache,
    apply_cache,
    judge,
    parents_of,
    plan_work,
)
from northwind_rag.models import Chunk, ChunkError
from northwind_rag.sources import CONTEXT_SOURCE_TYPES, build_chunks, check_chunk

pytestmark = pytest.mark.req("RAG-02")

FIXTURES = Path(__file__).resolve().parents[3] / "data" / "fixtures"
SENTENCE = "This passage sits inside the Northwind catalog record it belongs to."


@pytest.fixture(scope="module")
def chunks() -> list[Chunk]:
    return build_chunks(FIXTURES)


def first(chunks: list[Chunk], source_type: str) -> Chunk:
    return next(c for c in chunks if c.source_type == source_type)


def cache_with(chunks: list[Chunk], directory: Path, *wanted: Chunk) -> EnrichmentCache:
    parents = parents_of(chunks)
    cache = EnrichmentCache(directory)
    cache.add(judge(c, parents[c.chunk_id], ENRICH_MODEL, SENTENCE, "end_turn") for c in wanted)
    return cache


def test_the_allow_list_is_product_variant_and_document() -> None:
    assert frozenset({"product", "variant", "document"}) == CONTEXT_SOURCE_TYPES


def test_a_review_stays_plain_even_with_a_clean_entry_in_the_cache(
    chunks: list[Chunk], tmp_path: Path
) -> None:
    review = first(chunks, "review")
    cache = cache_with(chunks, tmp_path, review)
    entry = cache.get(review, parents_of(chunks)[review.chunk_id])
    assert entry is not None
    assert entry.context == SENTENCE  # a usable sentence, so staying plain proves the rule
    [applied] = [c for c in apply_cache([review], cache) if c.chunk_id == review.chunk_id]
    assert applied.context is None
    assert applied.embed_text == review.text


@pytest.mark.parametrize("source_type", ["product", "variant", "document"])
def test_the_allowed_types_keep_their_context(
    chunks: list[Chunk], tmp_path: Path, source_type: str
) -> None:
    chunk = first(chunks, source_type)
    [applied] = apply_cache([chunk], cache_with([chunk], tmp_path, chunk))
    assert applied.context == SENTENCE


def test_a_type_nobody_has_listed_gets_none(chunks: list[Chunk], tmp_path: Path) -> None:
    """An allow-list: a future shopper-written source type is not trusted by default."""
    review = first(chunks, "review")
    other: Any = "question"
    chunk = replace(review, source_type=other)
    [applied] = apply_cache([chunk], EnrichmentCache(tmp_path))
    assert applied.context is None


def test_a_type_nobody_has_listed_needs_no_call(chunks: list[Chunk], tmp_path: Path) -> None:
    other: Any = "question"
    chunk = replace(first(chunks, "review"), source_type=other)
    assert plan_work([chunk], EnrichmentCache(tmp_path)) == []


def test_a_type_nobody_has_listed_is_refused_at_the_index(chunks: list[Chunk]) -> None:
    other: Any = "question"
    with pytest.raises(ChunkError, match="may not have a context"):
        check_chunk(replace(first(chunks, "review"), source_type=other, context=SENTENCE))


def test_no_review_needs_a_call(chunks: list[Chunk], tmp_path: Path) -> None:
    planned = [c for _, group in plan_work(chunks, EnrichmentCache(tmp_path)) for c in group]
    assert planned
    assert {c.source_type for c in planned} == set(CONTEXT_SOURCE_TYPES)


def test_a_review_holding_a_context_is_refused_at_the_index(chunks: list[Chunk]) -> None:
    with pytest.raises(ChunkError, match="may not have a context"):
        check_chunk(replace(first(chunks, "review"), context=SENTENCE))


def test_the_real_cache_gives_no_review_a_context(chunks: list[Chunk]) -> None:
    applied = apply_cache(chunks, EnrichmentCache(FIXTURES.parent / "enrichment"))
    assert not [c.chunk_id for c in applied if c.source_type == "review" and c.context]
    assert {c.source_type for c in applied if c.context} == set(CONTEXT_SOURCE_TYPES)
