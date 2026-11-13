"""RAG-02: the enriched index, its isolation, and what retrieval returns from it.

Integration tests use a fake embedder, so nothing downloads a model and no model is called. A plain
ingest uses `conn`, a throwaway schema in the database DATABASE_URL names. An enriched ingest uses
`enriched_conn`, the same in the test database, because it refuses every other. A test that writes
must migrate first: without its own tables, `rag_chunks` is `public` on the search path. The tests
without a mark use a fake connection and need no database.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from northwind_mcp_rag.render import render_sources
from northwind_rag import cli
from northwind_rag.cli import (
    DEFAULT_DATABASE_URL,
    DEFAULT_ENRICHED_DATABASE_URL,
    create_enriched_database,
)
from northwind_rag.enrichment import EnrichmentCache, EnrichmentError, apply_cache
from northwind_rag.ingest import (
    ENRICHED_DATABASES,
    ingest,
    ingest_enriched,
    require_enriched_database,
)
from northwind_rag.models import Chunk, ChunkError
from northwind_rag.retrieval import dense_search, retrieve_context
from northwind_rag.sources import CONTEXT_SOURCE_TYPES, build_chunks
from northwind_rag.store import IndexVersion, apply_migrations, sync_chunks, vector_literal
from northwind_rag.testing import FakeEmbedder

from .enrich_support import NEUTRAL, neutral_entries, write_complete_cache  # noqa: TID252

pytestmark = pytest.mark.req("RAG-02")

# The version the plain index had before RAG-02 (evals/contexts/dense-only.jsonl records it).
PLAIN_INDEX_VERSION = "389cae01996a2dd6"  # DATA_VERSION 2 (R01221, row #13b); was c9e30984a7723d23
REAL_EMBEDDER_ID = "fastembed:BAAI/bge-small-en-v1.5:384"
SHARED_DATABASE = "northwind"
TEST_DATABASE = "northwind_enriched_test"


@pytest.fixture
def enriched_conn() -> Iterator[psycopg.Connection]:
    """`ingest_enriched` refuses every database but its own, so these tests get the test one, on
    any machine and on CI, in a throwaway schema like `conn`."""
    url = make_conninfo(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL), dbname=TEST_DATABASE)
    create_enriched_database(url)
    schema = f"rag02_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    connection = psycopg.connect(url, autocommit=True, options=f"-csearch_path={schema},public")
    try:
        yield connection
    finally:
        connection.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


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
    enriched_conn: psycopg.Connection, fixtures_dir: Path, full_cache: Path, chunks: list[Chunk]
) -> None:
    embedder = FakeEmbedder()
    report = ingest_enriched(enriched_conn, fixtures_dir, embedder, full_cache)
    assert report.chunks == len(chunks) == report.inserted
    assert report.index_version != PLAIN_INDEX_VERSION
    allowed = {c.chunk_id for c in chunks if c.source_type in CONTEXT_SOURCE_TYPES}
    ok = {e.chunk_id for e in neutral_entries(chunks) if e.status == "ok"} & allowed
    assert ok, "the neutral sentence should pass for most chunks"
    assert count(enriched_conn, "context IS NOT NULL") == len(ok)
    assert count(enriched_conn, "context IS NOT NULL AND source_type = 'review'") == 0
    sample = next(c for c in chunks if c.chunk_id in ok)
    assert f"{NEUTRAL}\n\n{sample.text}" in embedder.embedded
    row = enriched_conn.execute(
        "SELECT text, context FROM rag_chunks WHERE chunk_id = %s", (sample.chunk_id,)
    ).fetchone()
    assert row == (sample.text, NEUTRAL)  # text is the plain text; the context sits beside it


@pytest.mark.integration
def test_enriched_sync_is_idempotent(
    enriched_conn: psycopg.Connection, fixtures_dir: Path, full_cache: Path
) -> None:
    embedder = FakeEmbedder()
    first = ingest_enriched(enriched_conn, fixtures_dir, embedder, full_cache)
    seen = len(embedder.embedded)
    second = ingest_enriched(enriched_conn, fixtures_dir, embedder, full_cache)
    assert second.index_version == first.index_version
    assert (second.inserted, second.updated, second.deleted, second.embedded) == (0, 0, 0, 0)
    assert second.unchanged == first.chunks
    assert len(embedder.embedded) == seen


@pytest.mark.integration
def test_a_changed_context_alone_re_embeds_that_chunk_only(
    enriched_conn: psycopg.Connection,
    fixtures_dir: Path,
    full_cache: Path,
    chunks: list[Chunk],
    tmp_path: Path,
) -> None:
    embedder = FakeEmbedder()
    first = ingest_enriched(enriched_conn, fixtures_dir, embedder, full_cache)
    target = next(c for c in chunks if c.source_type == "product")
    changed = "This is the product page for the item, with its fit and fabric."
    write_complete_cache(tmp_path, chunks, {target.chunk_id: changed})
    before = len(embedder.embedded)
    second = ingest_enriched(enriched_conn, fixtures_dir, embedder, tmp_path)
    assert second.index_version != first.index_version
    assert (second.embedded, second.updated, second.inserted, second.deleted) == (1, 1, 0, 0)
    assert embedder.embedded[before:] == [f"{changed}\n\n{target.text}"]
    row = enriched_conn.execute(
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
def test_a_review_that_loses_its_context_gets_its_plain_vector_back(
    conn: psycopg.Connection, chunks: list[Chunk]
) -> None:
    """The row as the first run left it, seeded by SQL because `sync_chunks` now refuses it. The
    enriched vector must not survive: that is what the allow-list exists to prevent."""
    review = next(c for c in chunks if c.source_type == "review")
    embedder = FakeEmbedder()
    version = IndexVersion(data_version=1, dataset_sha256="0" * 64, chunker_version=1)
    apply_migrations(conn)  # without this `rag_chunks` is the real table, next on search_path
    sync_chunks(conn, [review], embedder, version)
    [enriched_vector] = embedder.embed([f"{NEUTRAL}\n\n{review.text}"])
    conn.execute(
        "UPDATE rag_chunks SET context = %s, embed_hash = %s, content_hash = %s, "
        "embedding = %s::vector WHERE chunk_id = %s",
        (
            NEUTRAL,
            replace(review, context=NEUTRAL).embed_hash,
            replace(review, context=NEUTRAL).content_hash,
            vector_literal(enriched_vector),
            review.chunk_id,
        ),
    )
    report = sync_chunks(conn, [review], embedder, version)
    assert (report.updated, report.embedded) == (1, 1)
    row = conn.execute(
        "SELECT context, embed_hash, embedding::text FROM rag_chunks WHERE chunk_id = %s",
        (review.chunk_id,),
    ).fetchone()
    assert row is not None
    [plain_vector] = embedder.embed([review.text])
    stored = [float(x) for x in row[2].strip("[]").split(",")]
    assert row[:2] == (None, None)
    assert stored == pytest.approx(list(plain_vector), abs=1e-6)
    assert stored != pytest.approx(list(enriched_vector), abs=1e-6)


@pytest.mark.integration
def test_retrieval_from_an_enriched_index_returns_plain_text(
    enriched_conn: psycopg.Connection, fixtures_dir: Path, full_cache: Path, chunks: list[Chunk]
) -> None:
    embedder = FakeEmbedder()
    ingest_enriched(enriched_conn, fixtures_dir, embedder, full_cache)
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
    hits = dense_search(enriched_conn, embedder, query, audience="shopper", k=5)
    assert hits[0].chunk_id == target.chunk_id
    assert hits[0].text == target.text
    assert all(NEUTRAL not in h.text for h in hits)
    context = retrieve_context(enriched_conn, embedder, query, region="US")
    assert context[0].chunk_id == target.chunk_id
    assert context[0].text == target.text
    assert all(NEUTRAL not in h.text for h in context)
    prompt_text = render_sources(context)  # what the assistant puts in front of the model
    assert context and all(h.text.split("\n")[0] in prompt_text for h in context)
    assert NEUTRAL not in prompt_text


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
    enriched_conn: psycopg.Connection, fixtures_dir: Path, chunks: list[Chunk], tmp_path: Path
) -> None:
    victim = next(c for c in chunks if c.source_type == "product")
    write_complete_cache(tmp_path, chunks, {victim.chunk_id: "This towel is 90% off this week."})
    # judge() would have rejected that; the file is edited so it holds it as an `ok` entry.
    import json

    path = tmp_path / "public.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["chunk_id"] == victim.chunk_id:
            row.update(status="ok", context="This towel is 90% off this week.", reason=None)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with pytest.raises(EnrichmentError, match="cached context refused: promotion"):
        ingest_enriched(enriched_conn, fixtures_dir, FakeEmbedder(), tmp_path)
    # Refused before migrations run, so this schema has no table at all, let alone rows.
    row = enriched_conn.execute("SELECT to_regclass(current_schema() || '.rag_chunks')").fetchone()
    assert row == (None,)


@pytest.mark.integration
def test_ingest_enriched_stops_on_a_missing_entry_and_indexes_nothing(
    enriched_conn: psycopg.Connection, fixtures_dir: Path, chunks: list[Chunk], tmp_path: Path
) -> None:
    write_complete_cache(tmp_path, chunks[:-1])
    embedder = FakeEmbedder()
    with pytest.raises(EnrichmentError, match=chunks[-1].chunk_id):
        ingest_enriched(enriched_conn, fixtures_dir, embedder, tmp_path)
    assert embedder.embedded == []
    # Only this test's schema counts: `public` may hold rows from other work.
    row = enriched_conn.execute(
        "SELECT to_regclass(current_schema() || '.rag_chunks') IS NOT NULL"
    ).fetchone()
    assert row is not None
    if row[0]:
        written = enriched_conn.execute("SELECT count(*) FROM rag_chunks").fetchone()
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


def test_require_enriched_database_raises_for_the_shared_database() -> None:
    with pytest.raises(EnrichmentError):
        require_enriched_database(FakeConnection(SHARED_DATABASE, []))  # type: ignore[arg-type]


@pytest.mark.parametrize("name", sorted(ENRICHED_DATABASES))
def test_require_enriched_database_passes_its_own_databases(name: str) -> None:
    require_enriched_database(FakeConnection(name, []))  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["northwind_rag02_plain", "postgres", "template1", "northwind2"])
def test_require_enriched_database_refuses_every_other_name(name: str) -> None:
    with pytest.raises(EnrichmentError):
        require_enriched_database(FakeConnection(name, []))  # type: ignore[arg-type]


def test_require_enriched_database_fails_closed_when_the_name_is_unknown() -> None:
    with pytest.raises(EnrichmentError):
        require_enriched_database(FakeConnection(None, []))  # type: ignore[arg-type]


def test_ingest_enriched_refuses_before_it_reads_or_writes_anything(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    enriched_conn = FakeConnection(SHARED_DATABASE, [])
    embedder = FakeEmbedder()
    with pytest.raises(EnrichmentError):
        ingest_enriched(enriched_conn, fixtures_dir, embedder, tmp_path)  # type: ignore[arg-type]
    assert len(enriched_conn.statements) == 1
    assert "current_database" in enriched_conn.statements[0].lower()
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


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@localhost:5432/template1",
        "postgresql://u:p@localhost:5432/northwind_rag02_plain",
        "postgresql://u:p@localhost:5432",  # no name: libpq would fall back to the user's
    ],
)
def test_create_enriched_database_refuses_anything_but_its_own(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("it connected before refusing")

    monkeypatch.setattr(psycopg, "connect", no_connection)
    with pytest.raises(SystemExit):
        create_enriched_database(url)


def test_a_blank_enriched_url_means_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENRICHED_DATABASE_URL", "")
    assert cli.enriched_database_url() == DEFAULT_ENRICHED_DATABASE_URL


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
