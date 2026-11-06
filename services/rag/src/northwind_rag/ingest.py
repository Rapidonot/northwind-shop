"""Versioned, idempotent ingestion of the fixtures into the index (RAG-01)."""

from __future__ import annotations

from pathlib import Path

import psycopg

from northwind_data.ingest_guard import load_manifest
from northwind_rag.chunking import CHUNKER_VERSION
from northwind_rag.embedding import Embedder
from northwind_rag.enrichment import CACHE_DIR, EnrichmentCache, EnrichmentError, apply_cache
from northwind_rag.models import MAX_CHUNK_CHARS, Chunk
from northwind_rag.sources import build_chunks
from northwind_rag.store import IndexVersion, IngestReport, apply_migrations, sync_chunks

# Other windows score live retrieval against the shared database, so generated text goes only here
# until a row switches it on purpose (decision 0010). The test database keeps the tests off the
# real one. An allowlist: `template1` or a scratch plain index must not take an enriched run either.
ENRICHED_DATABASES = frozenset({"northwind_enriched", "northwind_enriched_test"})


def ingest(
    conn: psycopg.Connection,
    fixtures_dir: Path,
    embedder: Embedder,
    max_chars: int = MAX_CHUNK_CHARS,
) -> IngestReport:
    """Validates every chunk first, then syncs the index. Running it twice changes nothing."""
    return _sync(conn, build_chunks(fixtures_dir, max_chars), fixtures_dir, embedder)


def require_enriched_database(conn: psycopg.Connection) -> None:
    row = conn.execute("SELECT current_database()").fetchone()
    if row is None or row[0] not in ENRICHED_DATABASES:
        raise EnrichmentError(f"the enriched index goes only in {sorted(ENRICHED_DATABASES)}")


def ingest_enriched(
    conn: psycopg.Connection,
    fixtures_dir: Path,
    embedder: Embedder,
    cache_dir: Path = CACHE_DIR,
    max_chars: int = MAX_CHUNK_CHARS,
) -> IngestReport:
    """The same, with each chunk's cached context. Reads the cache only, so it makes no calls."""
    require_enriched_database(conn)
    chunks = apply_cache(build_chunks(fixtures_dir, max_chars), EnrichmentCache(cache_dir))
    return _sync(conn, chunks, fixtures_dir, embedder)


def _sync(
    conn: psycopg.Connection, chunks: list[Chunk], fixtures_dir: Path, embedder: Embedder
) -> IngestReport:
    manifest = load_manifest(fixtures_dir)
    version = IndexVersion(
        data_version=manifest["data_version"],
        dataset_sha256=manifest["dataset_sha256"],
        chunker_version=CHUNKER_VERSION,
    )
    apply_migrations(conn)
    return sync_chunks(conn, chunks, embedder, version)
