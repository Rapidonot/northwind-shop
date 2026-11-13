"""Q4 (#10b): ingest verifies the dataset before it does anything else (RAG-01, CC-10). A tampered
copy aborts with no change to the index or its manifest, including over a good index. Throwaway
schemas only (`conn`, and `enriched_conn` for the enriched path); no re-hashing anywhere."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from northwind_data.ingest_guard import DatasetMismatchError
from northwind_rag.cli import DEFAULT_DATABASE_URL, create_enriched_database
from northwind_rag.ingest import ingest, ingest_enriched
from northwind_rag.store import apply_migrations
from northwind_rag.testing import FakeEmbedder

pytestmark = [pytest.mark.integration, pytest.mark.req("RAG-01"), pytest.mark.req("CC-10")]

TEST_ENRICHED_DATABASE = "northwind_enriched_test"


@pytest.fixture
def migrated(conn: psycopg.Connection) -> psycopg.Connection:
    apply_migrations(conn)
    return conn


@pytest.fixture
def enriched_migrated() -> Iterator[psycopg.Connection]:
    url = make_conninfo(
        os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL), dbname=TEST_ENRICHED_DATABASE
    )
    create_enriched_database(url)
    schema = f"rag01v_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    connection = psycopg.connect(url, autocommit=True, options=f"-csearch_path={schema},public")
    try:
        apply_migrations(connection)
        yield connection
    finally:
        connection.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def flip_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))


def snapshot(conn: psycopg.Connection) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    chunks = conn.execute(
        "SELECT chunk_id, content_hash, text_hash, index_version, visibility FROM rag_chunks "
        "ORDER BY chunk_id"
    ).fetchall()
    manifest = conn.execute("SELECT * FROM rag_index_manifest ORDER BY index_version").fetchall()
    return chunks, manifest


@pytest.mark.parametrize("name", ["prices.jsonl", "products.jsonl", "documents.jsonl"])
def test_a_tampered_copy_aborts_ingest_before_anything_is_written(
    migrated: psycopg.Connection, fixtures_copy: Path, name: str
) -> None:
    flip_byte(fixtures_copy / name)
    embedder = FakeEmbedder()
    with pytest.raises(DatasetMismatchError, match=name.replace(".", r"\.")):
        ingest(migrated, fixtures_copy, embedder)
    assert snapshot(migrated) == ([], [])
    assert embedder.embedded == []  # nothing was embedded: the check came first


def test_a_wrong_dataset_hash_aborts_ingest(
    migrated: psycopg.Connection, fixtures_copy: Path
) -> None:
    path = fixtures_copy / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["dataset_sha256"] = "0" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DatasetMismatchError, match="dataset_sha256"):
        ingest(migrated, fixtures_copy, FakeEmbedder())
    assert snapshot(migrated) == ([], [])


def test_a_tampered_copy_leaves_a_good_index_and_its_manifest_unchanged(
    migrated: psycopg.Connection, fixtures_dir: Path, fixtures_copy: Path
) -> None:
    ingest(migrated, fixtures_dir, FakeEmbedder())
    before = snapshot(migrated)
    assert before[0] and len(before[1]) == 1
    flip_byte(fixtures_copy / "products.jsonl")
    with pytest.raises(DatasetMismatchError):
        ingest(migrated, fixtures_copy, FakeEmbedder())
    assert snapshot(migrated) == before
    # and the good dataset still ingests as a no-op afterwards
    ingest(migrated, fixtures_dir, FakeEmbedder())
    assert snapshot(migrated) == before


def test_a_missing_listed_file_aborts_ingest(
    migrated: psycopg.Connection, fixtures_copy: Path
) -> None:
    (fixtures_copy / "stock.jsonl").unlink()
    with pytest.raises(DatasetMismatchError, match=r"stock\.jsonl"):
        ingest(migrated, fixtures_copy, FakeEmbedder())
    assert snapshot(migrated) == ([], [])


def test_the_real_fixtures_still_ingest(migrated: psycopg.Connection, fixtures_dir: Path) -> None:
    report = ingest(migrated, fixtures_dir, FakeEmbedder())
    assert report.chunks > 0
    chunks, manifest = snapshot(migrated)
    assert len(chunks) == report.chunks and len(manifest) == 1


def test_a_tampered_copy_aborts_the_enriched_ingest_too(
    enriched_migrated: psycopg.Connection, fixtures_copy: Path, tmp_path: Path
) -> None:
    flip_byte(fixtures_copy / "reviews.jsonl")
    embedder = FakeEmbedder()
    with pytest.raises(DatasetMismatchError, match=r"reviews\.jsonl"):
        ingest_enriched(enriched_migrated, fixtures_copy, embedder, tmp_path / "no-cache")
    assert snapshot(enriched_migrated) == ([], [])
    assert embedder.embedded == []


def test_ingest_stores_the_hash_it_verified_not_a_reread_of_the_manifest(
    migrated: psycopg.Connection, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-review N3 (L2): the manifest row holds the value `verify_dataset` returned. A sentinel
    from a patched verifier must be stored; a re-read of manifest.json would store the real hash."""
    import northwind_rag.ingest as ingest_module

    sentinel = "5e" * 32
    monkeypatch.setattr(ingest_module, "verify_dataset", lambda _dir: sentinel)
    ingest(migrated, fixtures_dir, FakeEmbedder())
    row = migrated.execute(
        "SELECT dataset_sha256 FROM rag_index_manifest WHERE is_current"
    ).fetchone()
    assert row == (sentinel,)


def test_ingest_enriched_stores_the_hash_it_verified(
    enriched_migrated: psycopg.Connection, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same for the enriched path (pass-3 P2, optional part): its manifest row holds the value
    `verify_dataset` returned, not a re-read of manifest.json."""
    import northwind_rag.ingest as ingest_module

    sentinel = "e5" * 32
    monkeypatch.setattr(ingest_module, "verify_dataset", lambda _dir: sentinel)
    ingest_enriched(enriched_migrated, fixtures_dir, FakeEmbedder())
    row = enriched_migrated.execute(
        "SELECT dataset_sha256 FROM rag_index_manifest WHERE is_current"
    ).fetchone()
    assert row == (sentinel,)
