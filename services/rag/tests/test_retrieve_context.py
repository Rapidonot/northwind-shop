"""retrieve_context: the shopper-facing retrieval path (SA-02; hard rule 7, RAG-11 groundwork)."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from northwind_rag.ingest import ingest
from northwind_rag.retrieval import SHOPPER_REGIONS, dense_search, retrieve_context
from northwind_rag.sources import build_chunks
from northwind_rag.testing import FakeEmbedder

pytestmark = [pytest.mark.integration, pytest.mark.req("SA-02")]

EVERYTHING = 5000  # more than the corpus, so a filter is the only thing that can drop a chunk


@pytest.fixture
def embedder(conn: psycopg.Connection, fixtures_dir: Path) -> FakeEmbedder:
    fake = FakeEmbedder()
    ingest(conn, fixtures_dir, fake)
    return fake


def regions_of(conn: psycopg.Connection, chunk_ids: list[str]) -> list[list[str]]:
    rows = conn.execute(
        "SELECT region FROM rag_chunks WHERE chunk_id = ANY(%s)", (chunk_ids,)
    ).fetchall()
    return [row[0] for row in rows]


def test_the_default_audience_is_shopper(
    conn: psycopg.Connection, embedder: FakeEmbedder, fixtures_dir: Path
) -> None:
    internal = [c for c in build_chunks(fixtures_dir) if c.metadata.visibility == "internal"]
    found = retrieve_context(conn, embedder, "anything", region="US", k=EVERYTHING)
    assert not {c.chunk_id for c in found} & {c.chunk_id for c in internal}


def test_no_internal_chunk_reaches_a_shopper_even_when_quoted_exactly(
    conn: psycopg.Connection, embedder: FakeEmbedder, fixtures_dir: Path
) -> None:
    """Internal docs have region ALL, so the region filter alone would admit them."""
    internal = [c for c in build_chunks(fixtures_dir) if c.metadata.visibility == "internal"]
    assert internal
    public = {
        row[0]
        for row in conn.execute("SELECT chunk_id FROM rag_chunks WHERE visibility = 'public'")
    }
    for chunk in internal:
        for region in sorted(SHOPPER_REGIONS):
            found = retrieve_context(conn, embedder, chunk.text, region=region, k=50)  # type: ignore[arg-type]
            assert found and {c.chunk_id for c in found} <= public


@pytest.mark.parametrize("region", sorted(SHOPPER_REGIONS))
def test_a_shopper_gets_only_their_region_and_all(
    conn: psycopg.Connection, embedder: FakeEmbedder, region: str
) -> None:
    found = retrieve_context(conn, embedder, "return window", region=region, k=EVERYTHING)  # type: ignore[arg-type]
    regions = regions_of(conn, [c.chunk_id for c in found])
    assert regions and all({region, "ALL"} & set(chunk_regions) for chunk_regions in regions)


@pytest.mark.parametrize("region", sorted(SHOPPER_REGIONS))
def test_one_context_never_holds_two_regions_return_rules(
    conn: psycopg.Connection, embedder: FakeEmbedder, region: str
) -> None:
    """RAG-12 security review, finding 3: unfiltered, one prompt held all four regions' rules."""
    found = retrieve_context(conn, embedder, "return window", region=region, k=EVERYTHING)  # type: ignore[arg-type]
    policies = {c.source_id for c in found if c.source_id.startswith("POL-RETURNS-")}
    assert policies == {f"POL-RETURNS-{region}"}


def test_the_region_filter_drops_nothing_a_shopper_should_see(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    expected = conn.execute(
        "SELECT count(*) FROM rag_chunks "
        "WHERE visibility = 'public' AND region && ARRAY['UK', 'ALL']"
    ).fetchone()
    assert expected is not None
    assert len(retrieve_context(conn, embedder, "q", region="UK", k=EVERYTHING)) == expected[0]


@pytest.mark.parametrize("region", ["ALL", "", "us", "AU", None])
def test_a_shopper_with_no_valid_region_is_refused(
    conn: psycopg.Connection, region: str | None
) -> None:
    with pytest.raises(ValueError, match="region"):
        retrieve_context(conn, FakeEmbedder(), "q", region=region)  # type: ignore[arg-type]


def test_a_region_is_required(conn: psycopg.Connection) -> None:
    with pytest.raises(TypeError):
        retrieve_context(conn, FakeEmbedder(), "q")  # type: ignore[call-arg]


def test_staff_may_search_without_a_region_and_still_reach_null_region_chunks(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    """P0009's chunks have a NULL region, which never matches `&&`."""
    found = retrieve_context(conn, embedder, "q", region=None, audience="staff", k=EVERYTHING)
    assert any(c.source_id.startswith("P0009") for c in found)


@pytest.mark.parametrize("audience", ["", "public", "internal", "SHOPPER"])
def test_an_unknown_audience_is_refused(conn: psycopg.Connection, audience: str) -> None:
    with pytest.raises(ValueError, match="audience"):
        retrieve_context(conn, FakeEmbedder(), "q", region="US", audience=audience)  # type: ignore[arg-type]


def test_dense_search_results_do_not_change(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    """The eval baseline stands on dense_search: no region filter until #8."""
    found = dense_search(conn, embedder, "return window", audience="shopper", k=EVERYTHING)
    policies = {c.source_id for c in found if c.source_id.startswith("POL-RETURNS-")}
    assert len(policies) == 4
