"""RAG-01: the index in Postgres. Each test works in its own throwaway schema."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from northwind_rag.embedding import FastEmbedEmbedder
from northwind_rag.ingest import ingest
from northwind_rag.models import Chunk, ChunkError, ChunkMetadata
from northwind_rag.sources import build_chunks
from northwind_rag.store import IndexVersion, apply_migrations, embedding_column_dim, sync_chunks
from northwind_rag.testing import FakeEmbedder

from .conftest import Edit  # noqa: TID252
from .fixture_signing import rehash  # noqa: TID252

pytestmark = [pytest.mark.req("RAG-01"), pytest.mark.integration]

VERSION = IndexVersion(data_version=1, dataset_sha256="0" * 64, chunker_version=1)
VECTOR = "[" + ",".join(["0.1"] * 384) + "]"


def count(conn: psycopg.Connection, where: str = "true") -> int:
    row = conn.execute(f"SELECT count(*) FROM rag_chunks WHERE {where}").fetchone()  # noqa: S608
    assert row is not None
    return int(row[0])


def valid_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "chunk_id": "document:X:0", "source_type": "document", "source_id": "X", "seq": 0,
        "text": "hello", "updated": "2026-09-01", "visibility": "public",
        "content_hash": "h", "text_hash": "t", "embedder_id": "e", "embedding": VECTOR,
        "index_version": "v1",
    }  # fmt: skip
    row.update(overrides)
    return row


def insert_manifest(conn: psycopg.Connection) -> None:
    conn.execute(
        "INSERT INTO rag_index_manifest (index_version, data_version, dataset_sha256, "
        "corpus_sha256, chunker_version, embedder_id, chunk_count, is_current) "
        "VALUES ('v1', 1, 's', 'c', 1, 'e', 0, false) ON CONFLICT DO NOTHING"
    )


def insert_row(conn: psycopg.Connection, **overrides: Any) -> None:
    insert_manifest(conn)
    row = valid_row(**overrides)
    cols = ", ".join(row)
    marks = ", ".join(f"%({k})s" for k in row).replace("%(embedding)s", "%(embedding)s::vector")
    if "price_band" in row and row["price_band"] is not None:
        marks = marks.replace("%(price_band)s", "%(price_band)s::jsonb")
    conn.execute(f"INSERT INTO rag_chunks ({cols}) VALUES ({marks})", row)  # noqa: S608


@pytest.fixture
def migrated(conn: psycopg.Connection) -> psycopg.Connection:
    apply_migrations(conn)
    return conn


@pytest.fixture(scope="module")
def sample(fixtures_dir: Path) -> list[Chunk]:
    """Ten chunks: enough to diff, quick to write."""
    chunks = build_chunks(fixtures_dir)
    picked = [c for c in chunks if c.source_type == "product"][:4]
    picked += [c for c in chunks if c.source_type == "document"][:3]
    picked += [c for c in chunks if c.source_type == "review"][:3]
    return picked


# ---- real embeddings (exactly one test) -------------------------------------------------


def test_real_fastembed_vector_length_equals_the_column_dimension(
    migrated: psycopg.Connection, sample: list[Chunk]
) -> None:
    embedder = FastEmbedEmbedder()
    vectors = embedder.embed([c.text for c in sample[:3]])
    assert len(vectors) == 3
    dim = embedding_column_dim(migrated)
    assert all(len(v) == dim for v in vectors)
    assert embedder.dim == dim


# ---- embedder id and re-embedding -------------------------------------------------------


def test_each_embedding_stores_its_embedder_id(
    migrated: psycopg.Connection, sample: list[Chunk]
) -> None:
    sync_chunks(migrated, sample, FakeEmbedder("fake:a:384"), VERSION)
    ids = {r[0] for r in migrated.execute("SELECT DISTINCT embedder_id FROM rag_chunks")}
    assert ids == {"fake:a:384"}


def test_changed_embedder_id_reembeds_every_chunk(
    migrated: psycopg.Connection, sample: list[Chunk]
) -> None:
    sync_chunks(migrated, sample, FakeEmbedder("fake:a:384"), VERSION)
    b = FakeEmbedder("fake:b:384")
    report = sync_chunks(migrated, sample, b, VERSION)
    assert report.embedded == len(sample)
    assert sorted(b.embedded) == sorted(c.text for c in sample)
    ids = {r[0] for r in migrated.execute("SELECT DISTINCT embedder_id FROM rag_chunks")}
    assert ids == {"fake:b:384"}


def test_changed_text_reembeds_only_that_chunk(
    migrated: psycopg.Connection, sample: list[Chunk]
) -> None:
    sync_chunks(migrated, sample, FakeEmbedder(), VERSION)
    edited = [replace(sample[0], text=sample[0].text + " Extra words.")] + sample[1:]
    fake = FakeEmbedder()
    report = sync_chunks(migrated, edited, fake, VERSION)
    assert (report.embedded, report.updated, report.inserted, report.deleted) == (1, 1, 0, 0)
    assert fake.embedded == [edited[0].text]


def test_metadata_only_change_keeps_the_vector_and_makes_no_embed_call(
    migrated: psycopg.Connection, sample: list[Chunk]
) -> None:
    sync_chunks(migrated, sample, FakeEmbedder(), VERSION)
    target = next(c for c in sample if c.source_type == "product")
    before = migrated.execute(
        "SELECT embedding::text FROM rag_chunks WHERE chunk_id = %s", (target.chunk_id,)
    ).fetchone()
    new_meta = replace(target.metadata, brand="Changedbrand")
    changed = [replace(c, metadata=new_meta) if c is target else c for c in sample]
    fake = FakeEmbedder()
    report = sync_chunks(migrated, changed, fake, VERSION)
    assert fake.embedded == []
    assert (report.embedded, report.updated) == (0, 1)
    after = migrated.execute(
        "SELECT embedding::text, brand FROM rag_chunks WHERE chunk_id = %s", (target.chunk_id,)
    ).fetchone()
    assert after is not None and before is not None
    assert after[0] == before[0]
    assert after[1] == "Changedbrand"


# ---- NULL, never empty ------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": ""}, {"brand": ""}, {"material": ""}, {"size": []}, {"colour": []},
        {"region": []}, {"price_band": "{}"}, {"text": ""},
    ],
)  # fmt: skip
def test_db_refuses_empty_spellings_of_nothing(
    migrated: psycopg.Connection, overrides: dict[str, Any]
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_row(migrated, **overrides)


def test_db_accepts_null_for_every_not_applicable_column(migrated: psycopg.Connection) -> None:
    insert_row(migrated)
    row = migrated.execute(
        "SELECT category, brand, size, colour, material, price_band, region FROM rag_chunks"
    ).fetchone()
    assert row == (None,) * 7


def test_stored_rows_never_hold_empty_values(
    migrated: psycopg.Connection, fixtures_dir: Path
) -> None:
    ingest(migrated, fixtures_dir, FakeEmbedder())
    bad = count(
        migrated,
        "category = '' OR brand = '' OR material = '' OR doc_type = '' OR parent_id = '' "
        "OR cardinality(size) = 0 OR cardinality(colour) = 0 OR cardinality(region) = 0 "
        "OR price_band = '{}'::jsonb OR text = '' "
        "OR '' = ANY(size) OR '' = ANY(colour) OR '' = ANY(region)",
    )
    assert bad == 0
    assert count(migrated, "updated IS NULL OR visibility IS NULL") == 0
    assert (
        count(
            migrated,
            "source_type = 'document' AND (category IS NOT NULL OR brand IS NOT NULL OR "
            "size IS NOT NULL OR colour IS NOT NULL OR material IS NOT NULL OR "
            "price_band IS NOT NULL OR region IS NULL)",
        )
        == 0
    )
    assert (
        count(
            migrated,
            "source_type <> 'document' AND (category IS NULL OR brand IS NULL OR size IS NULL "
            "OR colour IS NULL OR material IS NULL)",
        )
        == 0
    )
    assert (
        count(
            migrated,
            "source_type <> 'document' AND (region IS NULL OR price_band IS NULL) "
            "AND NOT (visibility = 'internal' AND (source_id = 'P0009' OR parent_id = 'P0009'))",
        )
        == 0
    )
    assert count(migrated, "source_id = 'P0009' AND region IS NULL AND price_band IS NULL") == 1


# ---- visibility fails closed ------------------------------------------------------------


def test_db_refuses_null_visibility(migrated: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.NotNullViolation):
        insert_row(migrated, visibility=None)


def test_visibility_column_has_no_default_and_a_row_without_it_is_refused(
    migrated: psycopg.Connection,
) -> None:
    row = migrated.execute(
        "SELECT column_default, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'rag_chunks' "
        "AND column_name = 'visibility'"
    ).fetchone()
    assert row == (None, "NO")
    insert_manifest(migrated)
    with pytest.raises(psycopg.errors.NotNullViolation):
        migrated.execute(
            "INSERT INTO rag_chunks (chunk_id, source_type, source_id, seq, text, updated, "
            "content_hash, text_hash, embedder_id, embedding, index_version) VALUES "
            "('d:X:0', 'document', 'X', 0, 't', '2026-09-01', 'h', 't', 'e', %s::vector, 'v1')",
            (VECTOR,),
        )


def test_db_refuses_unknown_visibility(migrated: psycopg.Connection) -> None:
    for value in ("", "everyone", "Public"):
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_row(migrated, visibility=value)


@pytest.mark.parametrize("source_type", ["products", "variants", "reviews", "documents"])
@pytest.mark.parametrize("how", ["missing", "null", "unknown"])
def test_missing_visibility_aborts_the_run_with_nothing_written(
    edit: Edit, migrated: psycopg.Connection, source_type: str, how: str
) -> None:
    def _break(records: list[dict[str, Any]]) -> None:
        if how == "missing":
            records[0].pop("visibility", None)
        else:
            records[0]["visibility"] = None if how == "null" else "everyone"

    path = rehash(edit(f"{source_type}.jsonl", _break))
    with pytest.raises(ChunkError):
        ingest(migrated, path, FakeEmbedder())
    assert count(migrated) == 0
    assert migrated.execute("SELECT count(*) FROM rag_index_manifest").fetchone() == (0,)


def test_failed_run_leaves_an_existing_index_untouched(
    edit: Edit, migrated: psycopg.Connection, fixtures_dir: Path
) -> None:
    ingest(migrated, fixtures_dir, FakeEmbedder())
    before = migrated.execute("SELECT count(*), max(updated_at) FROM rag_chunks").fetchone()

    def _break(reviews: list[dict[str, Any]]) -> None:
        reviews[0].pop("visibility")

    with pytest.raises(ChunkError):
        ingest(migrated, rehash(edit("reviews.jsonl", _break)), FakeEmbedder())
    assert migrated.execute("SELECT count(*), max(updated_at) FROM rag_chunks").fetchone() == before


def test_planted_price_aborts_the_run_and_writes_nothing(
    edit: Edit, migrated: psycopg.Connection
) -> None:
    def plant(products: list[dict[str, Any]]) -> None:
        products[3]["description"] += " Yours for $59.00."

    with pytest.raises(ChunkError, match="price or stock"):
        ingest(migrated, rehash(edit("products.jsonl", plant)), FakeEmbedder())
    assert count(migrated) == 0
    assert migrated.execute("SELECT count(*) FROM rag_index_manifest").fetchone() == (0,)


# ---- idempotency ------------------------------------------------------------------------


def test_second_run_changes_nothing(migrated: psycopg.Connection, fixtures_dir: Path) -> None:
    first = ingest(migrated, fixtures_dir, FakeEmbedder())
    assert first.inserted == first.chunks == first.embedded > 2000
    stamps = migrated.execute("SELECT chunk_id, updated_at FROM rag_chunks ORDER BY 1").fetchall()
    fake = FakeEmbedder()
    second = ingest(migrated, fixtures_dir, fake)
    assert (second.inserted, second.updated, second.deleted, second.embedded) == (0, 0, 0, 0)
    assert second.unchanged == first.chunks
    assert fake.embedded == []
    assert second.index_version == first.index_version
    assert (
        migrated.execute("SELECT chunk_id, updated_at FROM rag_chunks ORDER BY 1").fetchall()
        == stamps
    )
    assert migrated.execute("SELECT count(*) FROM rag_index_manifest").fetchone() == (1,)


def test_edited_record_changes_only_its_own_chunks(
    edit: Edit, migrated: psycopg.Connection, fixtures_dir: Path
) -> None:
    ingest(migrated, fixtures_dir, FakeEmbedder())
    stamps: dict[str, Any] = dict(
        migrated.execute("SELECT chunk_id, updated_at FROM rag_chunks").fetchall()
    )
    first_line = (fixtures_dir / "reviews.jsonl").read_text(encoding="utf-8").splitlines()[0]
    changed_id = f"review:{json.loads(first_line)['review_id']}:0"

    def tweak(reviews: list[dict[str, Any]]) -> None:
        reviews[0]["body"] += " Edited after the first run."

    fake = FakeEmbedder()
    report = ingest(migrated, rehash(edit("reviews.jsonl", tweak)), fake)
    assert (report.inserted, report.updated, report.deleted, report.embedded) == (0, 1, 0, 1)
    assert len(fake.embedded) == 1
    now: dict[str, Any] = dict(
        migrated.execute("SELECT chunk_id, updated_at FROM rag_chunks").fetchall()
    )
    assert {k for k in now if now[k] != stamps[k]} == {changed_id}


def test_removed_record_deletes_its_chunks(
    edit: Edit, migrated: psycopg.Connection, fixtures_dir: Path
) -> None:
    first = ingest(migrated, fixtures_dir, FakeEmbedder())
    line = (fixtures_dir / "reviews.jsonl").read_text(encoding="utf-8").splitlines()[5]
    victim = json.loads(line)["review_id"]
    n = count(migrated, f"source_id = '{victim}'")
    assert n >= 1

    def drop(reviews: list[dict[str, Any]]) -> None:
        del reviews[5]

    report = ingest(migrated, rehash(edit("reviews.jsonl", drop)), FakeEmbedder())
    assert (report.deleted, report.inserted, report.updated, report.embedded) == (n, 0, 0, 0)
    assert count(migrated, f"source_id = '{victim}'") == 0
    assert count(migrated) == first.chunks - n


# ---- manifest ---------------------------------------------------------------------------


def test_manifest_copies_data_version_and_dataset_sha(
    migrated: psycopg.Connection, fixtures_dir: Path
) -> None:
    source = json.loads((fixtures_dir / "manifest.json").read_text(encoding="utf-8"))
    report = ingest(migrated, fixtures_dir, FakeEmbedder())
    rows = migrated.execute(
        "SELECT data_version, dataset_sha256, chunk_count, is_current, index_version, embedder_id "
        "FROM rag_index_manifest"
    ).fetchall()
    assert rows == [
        (
            source["data_version"], source["dataset_sha256"], report.chunks, True,
            report.index_version, "fake:sha256:384",
        )
    ]  # fmt: skip
    assert report.data_version == source["data_version"]
    stored = {r[0] for r in migrated.execute("SELECT DISTINCT index_version FROM rag_chunks")}
    assert stored == {report.index_version}


def test_version_change_adds_a_row_and_exactly_one_is_current(
    fixtures_copy: Path, migrated: psycopg.Connection
) -> None:
    ingest(migrated, fixtures_copy, FakeEmbedder())
    path = fixtures_copy / "manifest.json"
    old_sha = json.loads(path.read_text(encoding="utf-8"))["dataset_sha256"]
    # A new dataset: a byte changed in a listed file (not one that is chunked), re-signed (#10b, Q4:
    # a bare `dataset_sha256` edit is now refused, so the version change is made the real way).
    regions = fixtures_copy / "regions.json"
    regions.write_bytes(regions.read_bytes() + b"\n")
    rehash(fixtures_copy)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["data_version"] += 1
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert manifest["dataset_sha256"] != old_sha
    ingest(migrated, fixtures_copy, FakeEmbedder())
    rows = migrated.execute(
        "SELECT data_version, dataset_sha256, is_current FROM rag_index_manifest ORDER BY 1"
    ).fetchall()
    assert [r[0] for r in rows] == [manifest["data_version"] - 1, manifest["data_version"]]
    assert [r[2] for r in rows] == [False, True]
    assert rows[1][1] == manifest["dataset_sha256"]
    ingest(migrated, fixtures_copy, FakeEmbedder())
    assert migrated.execute("SELECT count(*) FROM rag_index_manifest").fetchone() == (2,)


@pytest.mark.integration
def test_sync_chunks_scans_what_it_is_given(conn: psycopg.Connection) -> None:
    """Text added after build_chunks, such as RAG-02 enrichment, gets the same check."""
    apply_migrations(conn)
    metadata = ChunkMetadata(
        category=None,
        brand=None,
        size=None,
        colour=None,
        material=None,
        price_band=None,
        region=("ALL",),
        updated=date(2026, 10, 1),
        visibility="public",
    )
    chunk = Chunk("document", "FAQ-X", 0, "Only $49.99 today.", metadata, doc_type="faq")

    with pytest.raises(ChunkError, match="price or stock"):
        sync_chunks(conn, [chunk], FakeEmbedder(), IndexVersion(1, "sha", 1))
    assert conn.execute("SELECT count(*) FROM rag_chunks").fetchone() == (0,)
