"""RAG-01: heading splits that also respect the maximum chunk size."""

from __future__ import annotations

from pathlib import Path

import pytest

from northwind_rag.chunking import chunk_markdown, split_headings, split_to_fit
from northwind_rag.models import MAX_CHUNK_CHARS, ChunkError
from northwind_rag.sources import build_chunks

pytestmark = pytest.mark.req("RAG-01")

LIMIT = 300
DOC = (
    "# Title\n\nIntro text.\n\n## Alpha\n\nAlpha body.\n\n"
    "## Beta\n\nBeta body.\n\n### Deep\n\nDeep body."
)


def test_split_headings_makes_one_section_per_heading() -> None:
    sections = split_headings(DOC)
    assert [s.path for s in sections] == [(), ("Alpha",), ("Beta",), ("Beta", "Deep")]
    assert sections[1].body == "Alpha body."


def test_heading_in_code_fence_is_not_a_split() -> None:
    sections = split_headings("# T\n\n## A\n\n```\n## not a heading\n```\n")
    assert [s.path for s in sections] == [("A",)]


def test_chunk_markdown_splits_on_headings_and_prefixes_the_path() -> None:
    texts = chunk_markdown("Doc", DOC, MAX_CHUNK_CHARS)
    assert len(texts) == 4
    assert texts[1].startswith("Doc > Alpha\n\n")
    assert texts[3].startswith("Doc > Beta > Deep\n\n")


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "## Big\n\n" + " ".join(f"Sentence number {i}." for i in range(200)),
            id="oversized-section",
        ),
        pytest.param("## Para\n\n" + "word " * 2000, id="very-long-paragraph"),
        pytest.param("## Token\n\n" + "x" * 5000, id="single-unbroken-token"),
    ],
)
@pytest.mark.parametrize("limit", [LIMIT, MAX_CHUNK_CHARS])
def test_no_chunk_exceeds_the_limit_prefix_included(body: str, limit: int) -> None:
    texts = chunk_markdown("Doc title", body, limit)
    assert len(texts) > 1
    assert all(len(t) <= limit for t in texts)
    assert all(
        t.startswith(("Doc title > Big\n\n", "Doc title > Para\n\n", "Doc title > Token\n\n"))
        for t in texts
    )


def test_heading_splits_and_size_limit_work_together() -> None:
    body = "## One\n\n" + "a " * 600 + "\n\n## Two\n\nShort."
    texts = chunk_markdown("D", body, LIMIT)
    assert all(len(t) <= LIMIT for t in texts)
    assert any(t.startswith("D > One") for t in texts)
    assert sum(t.startswith("D > Two") for t in texts) == 1


def test_split_to_fit_is_lossless_for_a_unbroken_token() -> None:
    token = "y" * 1234
    assert "".join(split_to_fit(token, 100)) == token


def test_heading_path_longer_than_the_limit_is_refused() -> None:
    with pytest.raises(ChunkError):
        chunk_markdown("T" * 400, "## H\n\nbody", LIMIT)


def test_real_corpus_is_within_max_chunk_chars(fixtures_dir: Path) -> None:
    chunks = build_chunks(fixtures_dir)
    assert max(len(c.text) for c in chunks) <= MAX_CHUNK_CHARS


def test_oversized_document_in_the_pipeline_respects_the_limit(edit) -> None:  # type: ignore[no-untyped-def]
    def grow(docs: list[dict]) -> None:  # type: ignore[type-arg]
        for doc in docs:
            if doc["doc_id"] == "CG-COTTON":
                doc["body"] += "\n## Long\n\n" + "Wash gently. " * 400 + "z" * 4000

    path = edit("documents.jsonl", grow)
    chunks = [c for c in build_chunks(path) if c.source_id == "CG-COTTON"]
    assert len(chunks) > 3
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)
