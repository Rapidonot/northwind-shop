"""RAG-04 (row #11): a query plan's filters and parts in retrieval, over a real ingest.

Integration: a throwaway schema in the database named by DATABASE_URL (the row runs on `northwind_16`).
Colour and size narrow through the index's metadata (NULL passes: documents), the product allow-list
narrows product, variant and review chunks only, and one part with no filter is `retrieve_context`
exactly. Audience and region still come from the caller (hard rule 7).
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from northwind_rag.ingest import ingest
from northwind_rag.retrieval import (
    SearchFilter,
    retrieve_context,
    retrieve_planned,
)
from northwind_rag.testing import FakeEmbedder

pytestmark = [pytest.mark.req("RAG-04"), pytest.mark.integration]

QUERY = "warm jacket for the rain"


@pytest.fixture
def embedder(conn: psycopg.Connection, fixtures_dir: Path) -> FakeEmbedder:
    fake = FakeEmbedder()
    ingest(conn, fixtures_dir, fake)
    return fake


def rows(
    conn: psycopg.Connection, chunk_ids: list[str]
) -> list[tuple[str, str, str | None, list[str] | None, list[str] | None]]:
    return conn.execute(
        "SELECT source_type, source_id, parent_id, colour, size FROM rag_chunks"
        " WHERE chunk_id = ANY(%s)",
        (chunk_ids,),
    ).fetchall()


def test_no_filter_is_the_plain_search(conn: psycopg.Connection, embedder: FakeEmbedder) -> None:
    plain = retrieve_context(conn, embedder, QUERY, region="US", k=20)
    planned = retrieve_planned(conn, embedder, [(QUERY, SearchFilter())], region="US", k=20)
    assert [c.chunk_id for c in planned] == [c.chunk_id for c in plain]


def test_colour_and_size_narrow_and_null_passes(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    narrow = SearchFilter(colours=("Navy",), sizes=("M",))
    found = retrieve_planned(conn, embedder, [(QUERY, narrow)], region="US", k=50)
    assert found
    for source_type, _, _, colour, size in rows(conn, [c.chunk_id for c in found]):
        assert colour is None or "Navy" in colour, source_type
        assert size is None or "M" in size, source_type


def test_the_product_allow_list_keeps_documents(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    allowed = frozenset({"P0011"})
    found = retrieve_planned(
        conn,
        embedder,
        [("returns policy and rain jacket", SearchFilter(products=allowed))],
        region="US",
        k=50,
    )
    kinds = set()
    for source_type, source_id, parent_id, _, _ in rows(conn, [c.chunk_id for c in found]):
        kinds.add(source_type)
        if source_type == "document":
            continue
        assert (source_id if source_type == "product" else parent_id) in allowed
    assert "document" in kinds


def test_an_empty_allow_list_leaves_only_documents(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    found = retrieve_planned(
        conn, embedder, [(QUERY, SearchFilter(products=frozenset()))], region="US", k=50
    )
    assert {r[0] for r in rows(conn, [c.chunk_id for c in found])} <= {"document"}


def test_parts_are_fused_and_capped(conn: psycopg.Connection, embedder: FakeEmbedder) -> None:
    parts = [("navy fleece", SearchFilter(colours=("Navy",))), ("returns policy", SearchFilter())]
    found = retrieve_planned(conn, embedder, parts, region="UK", k=5)
    assert 0 < len(found) <= 5
    with pytest.raises(ValueError):
        retrieve_planned(conn, embedder, parts * 2, region="UK")


def test_internal_chunks_stay_out_whatever_the_plan(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    internal = {
        r[0] for r in conn.execute("SELECT chunk_id FROM rag_chunks WHERE visibility <> 'public'")
    }
    everything = SearchFilter(products=frozenset(f"P{n:04d}" for n in range(1, 257)))
    found = retrieve_planned(conn, embedder, [("internal pricing", everything)], region="US", k=300)
    assert not internal & {c.chunk_id for c in found}
