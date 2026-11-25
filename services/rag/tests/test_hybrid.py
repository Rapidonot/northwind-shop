"""Hybrid retrieval: RRF fusion, the keyword arm's filters, and what agents get (RAG-03)."""

from __future__ import annotations

import re
from pathlib import Path

import psycopg
import pytest

from northwind_rag import retrieval
from northwind_rag.ingest import ingest
from northwind_rag.retrieval import (
    CANDIDATE_DEPTH,
    RRF_K,
    _hybrid,
    dense_search,
    keyword_search,
    retrieve_context,
    retriever_id,
    rrf_fuse,
)
from northwind_rag.sources import build_chunks
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("RAG-03")


# ---- fusion (unit) ------------------------------------------------------------------------


def test_an_id_in_both_lists_beats_one_in_a_single_list() -> None:
    assert rrf_fuse([["a", "b"], ["b", "c"]]) == ["b", "a", "c"]


def test_scores_are_the_sum_of_reciprocal_ranks() -> None:
    # a: 1/61 + 1/63; b: 1/62 + 1/61; so b leads, then a, then c (1/62).
    assert rrf_fuse([["a", "b"], ["b", "c", "a"]]) == ["b", "a", "c"]


def test_equal_scores_break_on_the_id() -> None:
    assert rrf_fuse([["z"], ["a"]]) == ["a", "z"]


def test_a_repeat_within_one_list_counts_once_at_its_first_rank() -> None:
    assert rrf_fuse([["a", "a", "a"], ["b"]]) == ["a", "b"]


def test_fusion_is_exact_so_near_ties_cannot_flip() -> None:
    """1/61 + 1/62 equals 1/62 + 1/61 exactly; floats need not."""
    first = rrf_fuse([["x", "y"], ["y", "x"]])
    assert first == rrf_fuse([["y", "x"], ["x", "y"]]) == ["x", "y"]


def test_k_60_gives_an_exact_tie_that_breaks_on_the_id() -> None:
    """At k = 60, rank 1 once (1/61) equals rank 62 twice (2/122) exactly, so the ID decides;
    any other k, or a float sum, would put one ahead (mutant review, row #8)."""
    first = ["a", *(f"x{i:02d}" for i in range(60)), "b"]
    second = [*(f"y{i:02d}" for i in range(61)), "b"]
    assert first.index("b") == second.index("b") == 61
    assert rrf_fuse([first, second])[:2] == ["a", "b"]
    assert rrf_fuse([first, second], rrf_k=61)[0] == "b"


def test_empty_lists_fuse_to_nothing() -> None:
    assert rrf_fuse([[], []]) == []


def test_rrf_k_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="rrf_k"):
        rrf_fuse([["a"]], rrf_k=0)


def test_the_parameters_are_the_ones_fixed_in_the_plan() -> None:
    assert (RRF_K, CANDIDATE_DEPTH, retrieval.CONTEXT_K, retrieval.FTS_CONFIG) == (
        60,
        50,
        5,
        "english",
    )


def test_the_retriever_id_names_the_method_so_old_contexts_go_stale() -> None:
    assert (
        retriever_id(FakeEmbedder()) == "hybrid:v3:rrf60:d50:fts-text-english:fake:sha256:384"
    )  # v3: row #11 (RAG-04)


@pytest.mark.parametrize("name", ["_SEARCH", "_KEYWORD", "_HYBRID"])
def test_no_search_selects_context(name: str) -> None:
    """Context is ranking-only (decision 0010): no statement reads it back."""
    sql = getattr(retrieval, name)
    assert not re.search(r"\bcontext\b|\bembed_text\b", sql)


def test_the_fts_migration_indexes_text_only() -> None:
    migration = (
        Path(retrieval.__file__).resolve().parents[4] / "infra/db/migrations/0003_rag_fts.sql"
    )
    sql = migration.read_text(encoding="utf-8")
    assert "to_tsvector('english', text)" in sql
    assert not re.search(r"\bcontext\b\s*[,)|]|\|\|", sql)


# ---- against a real index (integration) -----------------------------------------------------


@pytest.fixture
def embedder(conn: psycopg.Connection, fixtures_dir: Path) -> FakeEmbedder:
    fake = FakeEmbedder()
    ingest(conn, fixtures_dir, fake)
    return fake


@pytest.mark.integration
def test_agents_get_the_hybrid_order(conn: psycopg.Connection, embedder: FakeEmbedder) -> None:
    query = "waterproof jacket with a hood"
    found = retrieve_context(conn, embedder, query, region="US")
    fused = _hybrid(conn, embedder, query, audience="shopper", regions=["US", "ALL"], k=5)
    assert [c.chunk_id for c in found] == [c.chunk_id for c in fused]
    assert len(found) == 5


@pytest.mark.integration
def test_hybrid_draws_on_both_arms(conn: psycopg.Connection, embedder: FakeEmbedder) -> None:
    query = "machine washable wool throw"
    found = {c.chunk_id for c in retrieve_context(conn, embedder, query, region="US", k=20)}
    dense = {
        c.chunk_id
        for c in dense_search(conn, embedder, query, audience="shopper", k=50, region="US")
    }
    keyword = {
        c.chunk_id for c in keyword_search(conn, query, audience="shopper", k=50, region="US")
    }
    assert found <= dense | keyword
    assert found & keyword and found & dense


@pytest.mark.integration
def test_keyword_search_never_returns_an_internal_chunk_to_a_shopper(
    conn: psycopg.Connection, embedder: FakeEmbedder, fixtures_dir: Path
) -> None:
    internal = [c for c in build_chunks(fixtures_dir) if c.metadata.visibility == "internal"]
    assert internal
    for chunk in internal:
        for region in ("US", "CA", "UK", "EU"):
            shopper = keyword_search(conn, chunk.text, audience="shopper", k=50, region=region)
            assert chunk.chunk_id not in {c.chunk_id for c in shopper}
            hybrid = retrieve_context(conn, embedder, chunk.text, region=region, k=50)
            assert chunk.chunk_id not in {c.chunk_id for c in hybrid}


@pytest.mark.integration
def test_staff_do_find_internal_chunks_by_keyword(
    conn: psycopg.Connection, embedder: FakeEmbedder, fixtures_dir: Path
) -> None:
    """So the filter, not a missing match, is what hid them above."""
    chunk = next(c for c in build_chunks(fixtures_dir) if c.metadata.visibility == "internal")
    staff = keyword_search(conn, chunk.text, audience="staff", k=50)
    assert chunk.chunk_id in {c.chunk_id for c in staff}


@pytest.mark.integration
@pytest.mark.parametrize("region", ["US", "CA", "UK", "EU"])
def test_keyword_and_hybrid_keep_the_region_filter(
    conn: psycopg.Connection, embedder: FakeEmbedder, region: str
) -> None:
    query = "return policy refund days"
    for found in (
        keyword_search(conn, query, audience="shopper", k=200, region=region),  # type: ignore[arg-type]
        retrieve_context(conn, embedder, query, region=region, k=200),  # type: ignore[arg-type]
    ):
        assert found
        rows = conn.execute(
            "SELECT region FROM rag_chunks WHERE chunk_id = ANY(%s)", ([c.chunk_id for c in found],)
        ).fetchall()
        assert all(set(row[0]) & {region, "ALL"} for row in rows)


@pytest.mark.integration
def test_a_word_only_in_context_is_not_found_by_keyword(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    """FTS reads `text` only (decision 0012)."""
    row = conn.execute(
        "SELECT chunk_id FROM rag_chunks WHERE source_type = 'product' AND visibility = 'public' "
        "ORDER BY chunk_id LIMIT 1"
    ).fetchone()
    assert row is not None
    conn.execute(
        "UPDATE rag_chunks SET context = 'This chunk mentions zyxquorble.', embed_hash = 'x' "
        "WHERE chunk_id = %s",
        (row[0],),
    )
    assert keyword_search(conn, "zyxquorble", audience="staff", k=50) == []


@pytest.mark.integration
def test_a_query_of_stop_words_only_finds_nothing_by_keyword(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    assert keyword_search(conn, "the and of", audience="shopper", k=10, region="US") == []
    assert len(retrieve_context(conn, embedder, "the and of", region="US")) == 5


@pytest.mark.integration
@pytest.mark.parametrize(
    ("hostile", "plain"),
    [
        ("wool' | 'jacket", "wool jacket"),
        ("!wool & (jacket", "wool jacket"),
        ("wool:* <-> jacket", "wool jacket"),
        ("wool'); DROP TABLE rag_chunks; --", "wool drop table rag_chunks"),
    ],
)
def test_query_syntax_in_a_question_is_text_not_operators(
    conn: psycopg.Connection, embedder: FakeEmbedder, hostile: str, plain: str
) -> None:
    """Operators in a shopper's words are punctuation: the keyword arm returns what the same
    words without it return, and nothing in the table changes (security review, row #8)."""
    before = conn.execute("SELECT count(*) FROM rag_chunks").fetchone()

    def ids(query: str) -> list[str]:
        found = keyword_search(conn, query, audience="shopper", k=50, region="US")
        return [c.chunk_id for c in found]

    assert ids(hostile) == ids(plain)
    assert ids(plain)
    retrieve_context(conn, embedder, hostile, region="US")
    assert conn.execute("SELECT count(*) FROM rag_chunks").fetchone() == before


@pytest.mark.integration
def test_a_shopper_keyword_search_needs_a_region(conn: psycopg.Connection) -> None:
    """Security review, row #8: a later reader cannot get every region's policies by omission."""
    with pytest.raises(ValueError, match="region"):
        keyword_search(conn, "returns", audience="shopper", k=5)
