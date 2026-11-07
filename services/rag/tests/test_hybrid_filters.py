"""RAG-03: audience and region filters on every arm, candidate bounds, determinism."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
import pytest

from northwind_rag.ingest import ingest
from northwind_rag.retrieval import (
    CANDIDATE_DEPTH,
    dense_search,
    keyword_search,
    retrieve_context,
)
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("RAG-03")

QUERIES = ["waterproof jacket with a hood", "return policy refund days", "wool care washing"]
REGIONS = ["US", "CA", "UK", "EU"]


@pytest.fixture
def embedder(conn: psycopg.Connection, fixtures_dir: Path) -> FakeEmbedder:
    fake = FakeEmbedder()
    ingest(conn, fixtures_dir, fake)
    return fake


def ids(chunks: Any) -> list[str]:
    return [c.chunk_id for c in chunks]


@pytest.mark.integration
@pytest.mark.parametrize("region", REGIONS)
def test_no_arm_returns_an_internal_chunk_to_a_shopper(
    conn: psycopg.Connection, embedder: FakeEmbedder, region: Any
) -> None:
    internal = {
        r[0] for r in conn.execute("SELECT chunk_id FROM rag_chunks WHERE visibility <> 'public'")
    }
    assert internal
    for query in QUERIES:
        for found in (
            dense_search(conn, embedder, query, audience="shopper", k=300, region=region),
            keyword_search(conn, query, audience="shopper", k=300, region=region),
            retrieve_context(conn, embedder, query, region=region, k=300),
        ):
            assert not internal & set(ids(found))


@pytest.mark.integration
def test_p0009_is_never_returned_to_a_shopper(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    p0009 = {
        r[0] for r in conn.execute("SELECT chunk_id FROM rag_chunks WHERE source_id = 'P0009'")
    }
    assert p0009
    texts = [r[0] for r in conn.execute("SELECT text FROM rag_chunks WHERE source_id = 'P0009'")]
    for region in REGIONS:
        for query in [*QUERIES, *texts]:
            found = retrieve_context(conn, embedder, query, region=region, k=300)  # type: ignore[arg-type]
            assert not p0009 & set(ids(found))
            found_kw = keyword_search(conn, query, audience="shopper", k=300, region=region)  # type: ignore[arg-type]
            assert not p0009 & set(ids(found_kw))


@pytest.mark.integration
def test_a_shopper_without_a_valid_region_is_refused(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    with pytest.raises(ValueError):
        retrieve_context(conn, embedder, "jacket", region=None)
    with pytest.raises(ValueError):
        retrieve_context(conn, embedder, "jacket", region="MARS")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        keyword_search(conn, "jacket", audience="shopper", k=5, region="MARS")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        dense_search(conn, embedder, "jacket", audience="shopper", k=5, region="MARS")  # type: ignore[arg-type]


@pytest.mark.integration
def test_an_unknown_audience_or_empty_k_is_refused(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    with pytest.raises(ValueError):
        retrieve_context(conn, embedder, "jacket", region="US", audience="admin")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        retrieve_context(conn, embedder, "jacket", region="US", k=0)
    with pytest.raises(ValueError):
        keyword_search(conn, "jacket", audience="shopper", k=0, region="US")


@pytest.mark.integration
@pytest.mark.parametrize("region", REGIONS)
def test_policy_chunks_stay_in_the_callers_region_on_every_arm(
    conn: psycopg.Connection, embedder: FakeEmbedder, region: Any
) -> None:
    for found in (
        dense_search(conn, embedder, "return policy", audience="shopper", k=300, region=region),
        keyword_search(conn, "return policy", audience="shopper", k=300, region=region),
        retrieve_context(conn, embedder, "return policy", region=region, k=300),
    ):
        rows = conn.execute(
            "SELECT region FROM rag_chunks WHERE chunk_id = ANY(%s) AND doc_type LIKE 'policy%%'",
            (ids(found),),
        ).fetchall()
        assert rows
        assert all(set(r[0]) & {region, "ALL"} for r in rows)


@pytest.mark.integration
def test_hybrid_never_returns_a_chunk_outside_both_arms(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    for query in QUERIES:
        for k in (3, 5, 20):
            depth = max(CANDIDATE_DEPTH, k)
            dense = set(
                ids(dense_search(conn, embedder, query, audience="shopper", k=depth, region="US"))
            )
            keyword = set(
                ids(keyword_search(conn, query, audience="shopper", k=depth, region="US"))
            )
            found = ids(retrieve_context(conn, embedder, query, region="US", k=k))
            assert len(found) == k
            assert len(set(found)) == k
            assert set(found) <= dense | keyword


@pytest.mark.integration
def test_k_larger_than_the_candidate_depth_is_honoured(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    k = CANDIDATE_DEPTH + 30
    found = retrieve_context(conn, embedder, "jacket", region="US", k=k)
    assert len(found) == k
    assert len(set(ids(found))) == k
    smaller = ids(retrieve_context(conn, embedder, "jacket", region="US", k=5))
    assert len(smaller) == 5


@pytest.mark.integration
def test_the_same_query_twice_gives_the_same_order(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    for query in QUERIES:
        first = ids(retrieve_context(conn, embedder, query, region="UK", k=20))
        second = ids(retrieve_context(conn, embedder, query, region="UK", k=20))
        assert first == second
        assert ids(keyword_search(conn, query, audience="shopper", k=20, region="UK")) == ids(
            keyword_search(conn, query, audience="shopper", k=20, region="UK")
        )


@pytest.mark.integration
def test_a_smaller_k_is_a_prefix_of_a_larger_one(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    small = ids(retrieve_context(conn, embedder, "wool care washing", region="US", k=5))
    large = ids(retrieve_context(conn, embedder, "wool care washing", region="US", k=15))
    assert large[:5] == small
