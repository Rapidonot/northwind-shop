"""RAG-11 (row #9): every red-team query, in every region, on every arm, returns no internal chunk
to a shopper and only that shopper's region and `ALL`.

The fake embedder puts a chunk's own text at distance zero from itself, so quoting internal text
makes it the nearest chunk, and only the filter keeps it out. The keyword arm takes every match;
the dense and hybrid arms take 300, far past the 5 an agent gets and the 50-deep candidate pool
(a dense `k` above the corpus makes Python re-rank every row, minutes per test). The staff
control proves the internal chunks are there to be found, so the shopper checks are not vacuous.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest

from northwind_rag.ingest import ingest
from northwind_rag.retrieval import (
    RetrievedChunk,
    ShopperRegion,
    dense_search,
    keyword_search,
    retrieve_context,
)
from northwind_rag.testing import FakeEmbedder

pytestmark = [pytest.mark.integration, pytest.mark.req("RAG-11")]

REPO = Path(__file__).resolve().parents[3]
RED_TEAM = REPO / "evals" / "adversarial" / "rag11_red_team.jsonl"
REGIONS: list[ShopperRegion] = ["US", "CA", "UK", "EU"]
EVERYTHING = 5000  # more than the corpus holds
DEEP = 300  # as test_hybrid_filters.py


def red_team_queries() -> list[str]:
    lines = RED_TEAM.read_text(encoding="utf-8").splitlines()
    return [str(json.loads(line)["query"]) for line in lines]


@pytest.fixture
def embedder(conn: psycopg.Connection, fixtures_dir: Path) -> FakeEmbedder:
    fake = FakeEmbedder()
    ingest(conn, fixtures_dir, fake)
    return fake


def internal_ids(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("SELECT chunk_id FROM rag_chunks WHERE visibility <> 'public'")
    return {str(r[0]) for r in rows}


def regions_of(conn: psycopg.Connection) -> dict[str, tuple[str, ...] | None]:
    rows = conn.execute("SELECT chunk_id, region FROM rag_chunks")
    return {str(r[0]): (tuple(r[1]) if r[1] is not None else None) for r in rows}


def arms(
    conn: psycopg.Connection, embedder: FakeEmbedder, query: str, region: ShopperRegion
) -> dict[str, list[RetrievedChunk]]:
    return {
        "retrieve_context": retrieve_context(conn, embedder, query, region=region, k=DEEP),
        "dense": dense_search(conn, embedder, query, audience="shopper", k=DEEP, region=region),
        "keyword": keyword_search(conn, query, audience="shopper", k=EVERYTHING, region=region),
    }


def out_of_region(
    found: list[RetrievedChunk], region: str, regions: dict[str, tuple[str, ...] | None]
) -> list[str]:
    allowed = {region, "ALL"}
    return [c.chunk_id for c in found if not set(regions[c.chunk_id] or ()) & allowed]


def test_the_corpus_has_the_28_items() -> None:
    assert len(red_team_queries()) == 28


@pytest.mark.parametrize("region", REGIONS)
def test_no_red_team_query_returns_an_internal_chunk_on_any_arm(
    conn: psycopg.Connection, embedder: FakeEmbedder, region: ShopperRegion
) -> None:
    internal = internal_ids(conn)
    assert internal
    for query in red_team_queries():
        for arm, found in arms(conn, embedder, query, region).items():
            leaked = internal & {c.chunk_id for c in found}
            assert not leaked, (region, arm, query, sorted(leaked))


@pytest.mark.parametrize("region", REGIONS)
def test_every_chunk_is_in_the_callers_region_or_all(
    conn: psycopg.Connection, embedder: FakeEmbedder, region: ShopperRegion
) -> None:
    regions = regions_of(conn)
    seen = 0
    for query in red_team_queries():
        for arm, found in arms(conn, embedder, query, region).items():
            seen += len(found)
            assert out_of_region(found, region, regions) == [], (region, arm, query)
    assert seen  # the queries found something, so the check is not empty


@pytest.mark.parametrize("region", REGIONS)
def test_quoting_internal_text_finds_nothing_internal(
    conn: psycopg.Connection, embedder: FakeEmbedder, region: ShopperRegion
) -> None:
    internal = internal_ids(conn)
    texts = [
        str(r[0]) for r in conn.execute("SELECT text FROM rag_chunks WHERE visibility <> 'public'")
    ]
    assert texts
    for text in texts:
        for arm, found in arms(conn, embedder, text, region).items():
            assert not internal & {c.chunk_id for c in found}, (region, arm, text[:60])


def test_the_staff_control_does_find_the_internal_chunks(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    """Not vacuous: with the staff audience, quoting each internal chunk finds it first."""
    rows = conn.execute("SELECT chunk_id, text FROM rag_chunks WHERE visibility <> 'public'")
    internal = [(str(r[0]), str(r[1])) for r in rows]
    assert len(internal) >= 7  # six internal documents and P0009's record
    for chunk_id, text in internal:
        found = retrieve_context(conn, embedder, text, region=None, audience="staff", k=DEEP)
        assert chunk_id in {c.chunk_id for c in found}
        staff_kw = keyword_search(conn, text, audience="staff", k=EVERYTHING, region=None)
        assert chunk_id in {c.chunk_id for c in staff_kw}


def test_a_red_team_region_claim_cannot_reach_the_filter(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    """The region comes from the caller only: a region named in the query text is just words."""
    regions = regions_of(conn)
    for query in red_team_queries():
        found = retrieve_context(conn, embedder, query, region="UK", k=DEEP)
        assert out_of_region(found, "UK", regions) == []
    with pytest.raises(ValueError):
        retrieve_context(conn, embedder, "returns", region=cast(Any, "ALL"))
