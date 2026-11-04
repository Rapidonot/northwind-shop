"""Versioned, idempotent ingestion of the fixtures into the index (RAG-01)."""

from __future__ import annotations

from pathlib import Path

import psycopg

from northwind_data.ingest_guard import load_manifest
from northwind_rag.chunking import CHUNKER_VERSION
from northwind_rag.embedding import Embedder
from northwind_rag.models import MAX_CHUNK_CHARS
from northwind_rag.sources import build_chunks
from northwind_rag.store import IndexVersion, IngestReport, apply_migrations, sync_chunks


def ingest(
    conn: psycopg.Connection,
    fixtures_dir: Path,
    embedder: Embedder,
    max_chars: int = MAX_CHUNK_CHARS,
) -> IngestReport:
    """Validates every chunk first, then syncs the index. Running it twice changes nothing."""
    chunks = build_chunks(fixtures_dir, max_chars)
    manifest = load_manifest(fixtures_dir)
    version = IndexVersion(
        data_version=manifest["data_version"],
        dataset_sha256=manifest["dataset_sha256"],
        chunker_version=CHUNKER_VERSION,
    )
    apply_migrations(conn)
    return sync_chunks(conn, chunks, embedder, version)
