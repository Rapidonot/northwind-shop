"""RAG-02: the enriched index, its isolation, and what retrieval returns from it.

Integration tests use the `conn` fixture (a throwaway schema in the database DATABASE_URL names)
and a fake embedder, so nothing downloads a model and no model is called. The tests without a
mark use a fake connection and need no database.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import psycopg
import pytest

from northwind_rag import cli
from northwind_rag.cli import DEFAULT_ENRICHED_DATABASE_URL, create_enriched_database
from northwind_rag.enrichment import EnrichmentCache, EnrichmentError, apply_cache
from northwind_rag.ingest import SHARED_DATABASE, ingest, ingest_enriched, refuse_shared_database
from northwind_rag.models import Chunk, ChunkError
from northwind_rag.retrieval import dense_search, retrieve_context
from northwind_rag.sources import build_chunks
from northwind_rag.store import IndexVersion, apply_migrations, sync_chunks
from northwind_rag.testing import FakeEmbedder

from .enrich_support import NEUTRAL, neutral_entries, write_complete_cache  # noqa: TID252

pytestmark = pytest.mark.req("RAG-02")

# The version the plain index had before RAG-02 (evals/contexts/dense-only.jsonl records it).
PLAIN_INDEX_VERSION = "c9e30984a7723d23"
REAL_EMBEDDER_ID = "fastembed:BAAI/bge-small-en-v1.5:384"


@pytest.fixture(scope="module")
def chunks() -> list[Chunk]:
    return build_chunks(Path(__file__).resolve().parents[3] / "data" / "fixtures")


@pytest.fixture(scope="module")
def full_cache(chunks: list[Chunk], tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("complete-cache")
    write_complete_cache(directory, chunks)
    return directory


def count(conn: psycopg.Connection, where: str = "true") -> int:
    row = conn.execute(f"SELECT count(*) FROM rag_chunks WHERE {where}").fetchone()  # noqa: S608
    assert row is not None
    return int(row[0])


# -- 4. a plain index is what it was -------------------------------------------------------------


@pytest.mark.integration
def test_plain_ingest_of_the_real_fixtures_keeps_its_index_version(
    conn: psycopg.Connection, fixtures_dir: Path
) -> None:
    """The embedder here is a fake wearing the real embedder's ID: the version hashes the corpus
    and that ID, never the vectors, so this is the same version `make ingest` gives, without
    downloading the model."""
    report = ingest(conn, fixtures_dir, FakeEmbedder(id=REAL_EMBEDDER_ID))
    assert report.index_version == PLAIN_INDEX_VERSION
    assert count(conn, "context IS NOT NULL OR embed_hash IS NOT NULL") == 0


# -- 10. the enriched index ---------------------------------------------------------------------


@pytest.mark.integration
def test_enriched_ingest_embeds_context_and_text_and_stores_both_apart(
    conn: psycopg.Connection, fixtures_dir: Path, full_cache: Path, chunks: list[Chunk]
) -> None:
    embedder = FakeEmbedder()
    report = ingest_enriched(conn, fixtures_dir, embedder, full_cache)
    assert report.chunks == len(chunks) == report.inserted
    assert report.index_version != PLAIN_INDEX_VERSION
    ok = {e.chunk_id for e in neutral_entries(chunks) if e.status == "ok"}
    assert ok, "the neutral sentence should pass for most chunks"
    assert count(conn, "context IS NOT NULL") == len(ok)
    sample = next(c for c in chunks if c.chunk_id in ok)
    assert f"{NEUTRAL}\n\n{sample.text}" in embedder.embedded
    row = conn.execute(
        "SELECT text, context FROM rag_chunks WHERE chunk_id = %s", (sample.chunk_id,)
    ).fetchone()
    assert row == (sample.text, NEUTRAL)  # text is the plain text; the context sits beside it


@pytest.mark.integration
def test_enriched_sync_is_idempotent(
    conn: psycopg.Connection, fixtures_dir: Path, full_cache: Path
) -> None:
    embedder = FakeEmbedder()
    first = ingest_enriched(conn, fixtures_dir, embedder, full_cache)
    seen = len(embedder.embedded)
    second = ingest_enriched(conn, fixtures_dir, embedder, full_cache)
    assert second.index_version == first.index_version
    assert (second.inserted, second.updated, second.deleted, second.embedded) == (0, 0, 0, 0)
    assert second.unchanged == first.chunks
    assert len(embedder.embedded) == seen


@pytest.mark.integration
def test_a_changed_context_alone_re_embeds_that_chunk_only(
    conn: psycopg.Connection,
    fixtures_dir: Path,
    full_cache: Path,
    chunks: list[Chunk],
    tmp_path: Path,
) -> None:
    embedder = FakeEmbedder()
    first = ingest_enriched(conn, fixtures_dir, embedder, full_cache)
    target = next(c for c in chunks if c.source_type == "product")
    changed = "This is the product page for the item, with its fit and fabric."
    write_complete_cache(tmp_path, chunks, {target.chunk_id: changed})
    before = len(embedder.embedded)
    second = ingest_enriched(conn, fixtures_dir, embedder, tmp_path)
    assert second.index_version != first.index_version
    assert (second.embedded, second.updated, second.inserted, second.deleted) == (1, 1, 0, 0)
    assert embedder.embedded[before:] == [f"{changed}\n\n{target.text}"]
    row = conn.execute(
        "SELECT context FROM rag_chunks WHERE chunk_id = %s", (target.chunk_id,)
    ).fetchone()
    assert row == (changed,)


@pytest.mark.integration
def test_dropping_a_context_goes_back_to_embedding_the_plain_text(
    conn: psycopg.Connection, fixtures_dir: Path, chunks: list[Chunk]
) -> None:
    embedder = FakeEmbedder()
    enriched = [replace(c, context=NEUTRAL) for c in chunks[:5]]
    version = IndexVersion(data_version=1, dataset_sha256="0" * 64, chunker_version=1)
    apply_migrations(conn)  # without this `rag_chunks` is the real table, next on search_path
    sync_chunks(conn, enriched, embedder, version)
    before = len(embedder.embedded)
    report = sync_chunks(conn, chunks[:5], embedder, version)
    assert (report.embedded, report.updated) == (5, 5)
    assert embedder.embedded[before:] == [c.text for c in chunks[:5]]
    assert count(conn, "context IS NOT NULL") == 0


@pytest.mark.integration
def test_retrieval_from_an_enriched_index_returns_plain_text(
    conn: psycopg.Connection, fixtures_dir: Path, full_cache: Path, chunks: list[Chunk]
) -> None:
    embedder = FakeEmbedder()
    ingest_enriched(conn, fixtures_dir, embedder, full_cache)
    ok = {e.chunk_id for e in neutral_entries(chunks) if e.status == "ok"}
    target = next(
        c
        for c in chunks
        if c.source_type == "product"
        and c.chunk_id in ok
        and c.metadata.visibility == "public"
        and c.metadata.region
        and "US" in c.metadata.region
    )
    query = replace(target, context=NEUTRAL).embed_text  # its own vector: distance zero
    hits = dense_search(conn, embedder, query, audience="shopper", k=5)
    assert hits[0].chunk_id == target.chunk_id
    assert hits[0].text == target.text
    assert all(NEUTRAL not in h.text for h in hits)
    context = retrieve_context(conn, embedder, query, region="US")
    assert context[0].chunk_id == target.chunk_id
    assert context[0].text == target.text
    assert all(NEUTRAL not in h.text for h in context)


@pytest.mark.integration
def test_sync_refuses_a_generated_context_that_fails_the_checks(
    conn: psycopg.Connection, chunks: list[Chunk]
) -> None:
    bad = replace(chunks[0], context="This page lists the item at $49.99 today.")
    version = IndexVersion(data_version=1, dataset_sha256="0" * 64, chunker_version=1)
    apply_migrations(conn)
    with pytest.raises(ChunkError, match="generated context refused"):
        sync_chunks(conn, [bad], FakeEmbedder(), version)
    assert count(conn) == 0


@pytest.mark.integration
def test_ingest_refuses_a_cache_edited_by_hand_and_writes_nothing(
    conn: psycopg.Connection, fixtures_dir: Path, chunks: list[Chunk], tmp_path: Path
) -> None:
    victim = next(c for c in chunks if c.source_type == "product")
    write_complete_cache(tmp_path, chunks, {victim.chunk_id: "Every towel is 90% off this week."})
    # judge() would have rejected that; the file is edited so it holds it as an `ok` entry.
    import json

    path = tmp_path / "public.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["chunk_id"] == victim.chunk_id:
            row.update(status="ok", context="Every towel is 90% off this week.", reason=None)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with pytest.raises(ChunkError, match="generated context refused"):
        ingest_enriched(conn, fixtures_dir, FakeEmbedder(), tmp_path)
    assert count(conn) == 0


@pytest.mark.integration
def test_ingest_enriched_stops_on_a_missing_entry_and_indexes_nothing(
    conn: psycopg.Connection, fixtures_dir: Path, chunks: list[Chunk], tmp_path: Path
) -> None:
    write_complete_cache(tmp_path, chunks[:-1])
    embedder = FakeEmbedder()
    with pytest.raises(EnrichmentError, match=chunks[-1].chunk_id):
        ingest_enriched(conn, fixtures_dir, embedder, tmp_path)
    assert embedder.embedded == []
    # Only this test's schema counts: `public` may hold rows from other work.
    row = conn.execute(
        "SELECT to_regclass(current_schema() || '.rag_chunks') IS NOT NULL"
    ).fetchone()
    assert row is not None
    if row[0]:
        written = conn.execute("SELECT count(*) FROM rag_chunks").fetchone()
        assert written == (0,)


# -- 10. isolation, with no database -----------------------------------------------------------


@dataclass
class FakeConnection:
    database: str | None
    statements: list[str]

    def execute(self, query: str, *args: Any) -> FakeConnection:
        self.statements.append(query)
        return self

    def fetchone(self) -> tuple[str] | None:
        return None if self.database is None else (self.database,)


def test_refuse_shared_database_raises_for_the_shared_database() -> None:
    with pytest.raises(EnrichmentError):
        refuse_shared_database(FakeConnection(SHARED_DATABASE, []))  # type: ignore[arg-type]
    assert SHARED_DATABASE == "northwind"


@pytest.mark.parametrize("name", ["northwind_enriched", "northwind_rag02_plain", "northwind2"])
def test_refuse_shared_database_passes_any_other_name(name: str) -> None:
    refuse_shared_database(FakeConnection(name, []))  # type: ignore[arg-type]


def test_refuse_shared_database_fails_closed_when_the_name_is_unknown() -> None:
    with pytest.raises(EnrichmentError):
        refuse_shared_database(FakeConnection(None, []))  # type: ignore[arg-type]


def test_ingest_enriched_refuses_before_it_reads_or_writes_anything(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    conn = FakeConnection(SHARED_DATABASE, [])
    embedder = FakeEmbedder()
    with pytest.raises(EnrichmentError):
        ingest_enriched(conn, fixtures_dir, embedder, tmp_path)  # type: ignore[arg-type]
    assert len(conn.statements) == 1
    assert "current_database" in conn.statements[0].lower()
    assert embedder.embedded == []
    assert list(tmp_path.iterdir()) == []


def test_create_enriched_database_refuses_a_url_naming_the_shared_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("it connected before refusing")

    monkeypatch.setattr(psycopg, "connect", no_connection)
    with pytest.raises(SystemExit):
        create_enriched_database("postgresql://u:p@localhost:5432/northwind")


def test_enriched_database_url_ignores_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    shared = "postgresql://u:p@localhost:5432/northwind"
    monkeypatch.setenv("DATABASE_URL", shared)
    monkeypatch.delenv("ENRICHED_DATABASE_URL", raising=False)
    assert cli.enriched_database_url() == DEFAULT_ENRICHED_DATABASE_URL
    assert cli.enriched_database_url() != shared
    monkeypatch.setenv("ENRICHED_DATABASE_URL", "postgresql://u:p@localhost:5432/other")
    assert cli.enriched_database_url() == "postgresql://u:p@localhost:5432/other"


def test_the_default_enriched_database_is_not_the_shared_one() -> None:
    from psycopg.conninfo import conninfo_to_dict

    assert conninfo_to_dict(DEFAULT_ENRICHED_DATABASE_URL)["dbname"] != SHARED_DATABASE


def test_apply_cache_raises_rather_than_index_partly(chunks: list[Chunk], tmp_path: Path) -> None:
    write_complete_cache(tmp_path, chunks[:-3])
    with pytest.raises(EnrichmentError, match="3 chunks"):
        apply_cache(chunks, EnrichmentCache(tmp_path))
