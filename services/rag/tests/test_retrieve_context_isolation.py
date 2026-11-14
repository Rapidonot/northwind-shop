"""Visibility and region are separate filters (SA-02; hard rule 7, RAG-12 finding 3).

The corpus puts every internal document in region ALL, so a bug that read one filter as the other
would pass on it. These tests move internal documents into a shopper's own region.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
import pytest

from northwind_rag.ingest import ingest
from northwind_rag.retrieval import SHOPPER_REGIONS, retrieve_context
from northwind_rag.sources import build_chunks
from northwind_rag.testing import FakeEmbedder

from .fixture_signing import rehash  # noqa: TID252

pytestmark = [pytest.mark.integration, pytest.mark.req("SA-02")]

EVERYTHING = 5000


def internal_in_own_region(fixtures_copy: Path, edit: Callable[..., Path]) -> tuple[Path, set[str]]:
    moved: set[str] = set()

    def change(records: list[dict[str, Any]]) -> None:
        for number, record in enumerate(records):
            if record["visibility"] == "internal":
                record["region"] = sorted(SHOPPER_REGIONS)[number % 4]
                moved.add(record["doc_id"])

    # The copy is changed on purpose, so it is re-signed to pass ingest's dataset check (#10b,
    # Q4; the user's stop-5 decision, 2026-11-13). The assertions are unchanged.
    return rehash(edit("documents.jsonl", change)), moved


@pytest.fixture
def moved_corpus(
    conn: psycopg.Connection, fixtures_copy: Path, edit: Callable[..., Path]
) -> tuple[FakeEmbedder, set[str]]:
    root, moved = internal_in_own_region(fixtures_copy, edit)
    assert moved
    embedder = FakeEmbedder()
    ingest(conn, root, embedder)
    return embedder, moved


@pytest.mark.req("RAG-11")
@pytest.mark.parametrize("region", sorted(SHOPPER_REGIONS))
def test_an_internal_document_in_the_shoppers_own_region_is_still_hidden(
    conn: psycopg.Connection,
    moved_corpus: tuple[FakeEmbedder, set[str]],
    fixtures_copy: Path,
    region: str,
) -> None:
    embedder, moved = moved_corpus
    found = retrieve_context(conn, embedder, "anything", region=region, k=EVERYTHING)  # type: ignore[arg-type]
    assert found
    ids = {c.source_id for c in found}
    assert not ids & moved
    internal_chunks = [c for c in build_chunks(fixtures_copy) if c.source_id in moved]
    assert internal_chunks
    for chunk in internal_chunks:
        quoted = retrieve_context(conn, embedder, chunk.text, region=region, k=50)  # type: ignore[arg-type]
        assert chunk.chunk_id not in {c.chunk_id for c in quoted}


@pytest.mark.req("RAG-11")
@pytest.mark.parametrize("region", sorted(SHOPPER_REGIONS))
def test_staff_in_the_same_region_do_get_them(
    conn: psycopg.Connection, moved_corpus: tuple[FakeEmbedder, set[str]], region: str
) -> None:
    """The visibility filter is what hid them above, not a missing row."""
    embedder, moved = moved_corpus
    found = retrieve_context(
        conn,
        embedder,
        "anything",
        region=region,  # type: ignore[arg-type]
        audience="staff",
        k=EVERYTHING,
    )
    assert {c.source_id for c in found} & moved


@pytest.mark.req("RAG-11")
def test_a_call_that_names_no_audience_is_a_shopper_call(
    conn: psycopg.Connection, moved_corpus: tuple[FakeEmbedder, set[str]]
) -> None:
    embedder, moved = moved_corpus
    default = retrieve_context(conn, embedder, "q", region="US", k=EVERYTHING)
    explicit = retrieve_context(conn, embedder, "q", region="US", audience="shopper", k=EVERYTHING)
    assert [c.chunk_id for c in default] == [c.chunk_id for c in explicit]
    assert not {c.source_id for c in default} & moved
