"""dense_search against a real pgvector table (RAG-12; hard rule 7)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

import psycopg
import pytest

from northwind_rag.ingest import ingest
from northwind_rag.models import Chunk
from northwind_rag.retrieval import dense_search
from northwind_rag.sources import build_chunks
from northwind_rag.store import IndexVersion, apply_migrations, sync_chunks
from northwind_rag.testing import FakeEmbedder

pytestmark = [pytest.mark.integration, pytest.mark.req("RAG-12")]

QUERY = "the query"


def at_angle(degrees: float, dim: int = 384) -> list[float]:
    """A unit vector whose cosine distance from the query grows with the angle."""
    vector = [0.0] * dim
    vector[0], vector[1] = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return vector


class PlacedEmbedder:
    """Puts each text at a chosen angle from the query, so the expected order is known."""

    id = "placed:384"
    dim = 384

    def __init__(self, angles: dict[str, float]) -> None:
        self._angles = {QUERY: 0.0, **angles}

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [at_angle(self._angles[text]) for text in texts]


def place(conn: psycopg.Connection, chunks: dict[Chunk, float]) -> PlacedEmbedder:
    apply_migrations(conn)
    embedder = PlacedEmbedder({chunk.text: angle for chunk, angle in chunks.items()})
    sync_chunks(conn, list(chunks), embedder, IndexVersion(1, "sha", 1))
    return embedder


@pytest.fixture(scope="module")
def public_chunks(fixtures_dir: Path) -> list[Chunk]:
    return [
        c
        for c in build_chunks(fixtures_dir)
        if c.source_type == "document" and c.metadata.visibility == "public"
    ][:6]


@pytest.fixture(scope="module")
def internal_chunk(fixtures_dir: Path) -> Chunk:
    return next(c for c in build_chunks(fixtures_dir) if c.metadata.visibility == "internal")


def test_results_come_back_in_exact_distance_order(
    conn: psycopg.Connection, public_chunks: list[Chunk]
) -> None:
    a, b, c, d = public_chunks[:4]
    embedder = place(conn, {c: 40.0, a: 10.0, d: 70.0, b: 25.0})
    found = dense_search(conn, embedder, QUERY, audience="shopper", k=4)
    assert [chunk.chunk_id for chunk in found] == [x.chunk_id for x in (a, b, c, d)]


def test_equal_distances_break_on_chunk_id(
    conn: psycopg.Connection, public_chunks: list[Chunk]
) -> None:
    chunks = sorted(public_chunks[:3], key=lambda c: c.chunk_id, reverse=True)
    embedder = place(conn, {c: 30.0 for c in chunks})
    found = dense_search(conn, embedder, QUERY, audience="shopper", k=3)
    assert [c.chunk_id for c in found] == sorted(c.chunk_id for c in chunks)


def test_k_limits_the_results(conn: psycopg.Connection, public_chunks: list[Chunk]) -> None:
    embedder = place(conn, {c: 10.0 * n for n, c in enumerate(public_chunks[:4], start=1)})
    assert len(dense_search(conn, embedder, QUERY, audience="shopper", k=2)) == 2


def test_the_nearest_internal_chunk_is_skipped_for_shoppers_only(
    conn: psycopg.Connection, public_chunks: list[Chunk], internal_chunk: Chunk
) -> None:
    near, far = public_chunks[:2]
    embedder = place(conn, {internal_chunk: 0.0, near: 20.0, far: 50.0})
    shopper = dense_search(conn, embedder, QUERY, audience="shopper", k=3)
    staff = dense_search(conn, embedder, QUERY, audience="staff", k=3)
    assert [c.chunk_id for c in shopper] == [near.chunk_id, far.chunk_id]
    assert staff[0].chunk_id == internal_chunk.chunk_id


def test_no_internal_chunk_reaches_a_shopper_even_when_quoted_exactly(
    conn: psycopg.Connection, fixtures_dir: Path
) -> None:
    """Over the real corpus, each internal chunk's own text is the query, a distance of zero."""
    embedder = FakeEmbedder()
    ingest(conn, fixtures_dir, embedder)
    internal = [c for c in build_chunks(fixtures_dir) if c.metadata.visibility == "internal"]
    assert internal
    public_ids = {
        row[0]
        for row in conn.execute("SELECT chunk_id FROM rag_chunks WHERE visibility = 'public'")
    }
    for chunk in internal:
        found = dense_search(conn, embedder, chunk.text, audience="shopper", k=50)
        assert len(found) == 50
        assert {c.chunk_id for c in found} <= public_ids
        staff = dense_search(conn, embedder, chunk.text, audience="staff", k=1)
        assert staff[0].chunk_id == chunk.chunk_id


@pytest.mark.parametrize("audience", ["", "public", "internal", "SHOPPER"])
def test_an_unknown_audience_is_refused(conn: psycopg.Connection, audience: str) -> None:
    with pytest.raises(ValueError, match="audience"):
        dense_search(conn, FakeEmbedder(), QUERY, audience=audience, k=1)  # type: ignore[arg-type]


def test_k_below_one_is_refused(conn: psycopg.Connection) -> None:
    with pytest.raises(ValueError, match="k must be"):
        dense_search(conn, FakeEmbedder(), QUERY, audience="shopper", k=0)
