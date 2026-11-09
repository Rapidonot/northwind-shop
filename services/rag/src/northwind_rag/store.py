"""Writes chunks to Postgres: migrations, the diff against the index, the manifest (RAG-01)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from northwind_rag.embedding import Embedder
from northwind_rag.models import Chunk
from northwind_rag.sources import check_chunk

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "infra" / "db" / "migrations"
# Serialises migrations and ingestion, so two runs cannot interleave their diffs.
_ADVISORY_LOCK = 0x52414701


class IngestError(RuntimeError):
    pass


@dataclass(frozen=True)
class IndexVersion:
    """What the index was built from; the first two are copied from manifest.json."""

    data_version: int
    dataset_sha256: str
    chunker_version: int


@dataclass(frozen=True)
class IngestReport:
    index_version: str
    data_version: int
    chunks: int
    inserted: int
    updated: int
    deleted: int
    unchanged: int
    embedded: int


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Applies new migrations in name order; refuses one that changed after it was applied."""
    applied: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (filename text PRIMARY KEY, "
            "sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        rows = conn.execute("SELECT filename, sha256 FROM schema_migrations").fetchall()
        known: dict[str, str] = {row[0]: row[1] for row in rows}
        for path in sorted(directory.glob("*.sql")):
            sql = path.read_bytes()
            digest = hashlib.sha256(sql).hexdigest()
            if path.name in known:
                if known[path.name] != digest:
                    raise IngestError(f"{path.name} changed after it was applied")
                continue
            conn.execute(sql)
            conn.execute(
                "INSERT INTO schema_migrations (filename, sha256) VALUES (%s, %s)",
                (path.name, digest),
            )
            applied.append(path.name)
    return applied


def embedding_column_dim(conn: psycopg.Connection) -> int:
    """The N of `rag_chunks.embedding vector(N)`, read from the catalog."""
    row = conn.execute(
        "SELECT atttypmod FROM pg_attribute "
        "WHERE attrelid = 'rag_chunks'::regclass AND attname = 'embedding'"
    ).fetchone()
    if row is None:
        raise IngestError("rag_chunks.embedding does not exist")
    return int(row[0])


def _index_version(version: IndexVersion, corpus_sha256: str, embedder_id: str) -> str:
    parts = (
        version.data_version,
        version.dataset_sha256,
        version.chunker_version,
        corpus_sha256,
        embedder_id,
    )
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16]


def _corpus_sha256(chunks: Sequence[Chunk]) -> str:
    lines = sorted(f"{c.chunk_id} {c.content_hash}" for c in chunks)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _row(chunk: Chunk, embedder_id: str, index_version: str) -> dict[str, object]:
    meta = chunk.metadata
    return {
        "chunk_id": chunk.chunk_id,
        "source_type": chunk.source_type,
        "source_id": chunk.source_id,
        "seq": chunk.seq,
        "parent_id": chunk.parent_id,
        "doc_type": chunk.doc_type,
        "text": chunk.text,
        "category": meta.category,
        "brand": meta.brand,
        "size": list(meta.size) if meta.size else None,
        "colour": list(meta.colour) if meta.colour else None,
        "material": meta.material,
        "price_band": Jsonb(meta.price_band) if meta.price_band else None,
        "region": list(meta.region) if meta.region else None,
        "updated": meta.updated,
        "visibility": meta.visibility,
        "content_hash": chunk.content_hash,
        "text_hash": chunk.text_hash,
        "embedder_id": embedder_id,
        "index_version": index_version,
        "context": chunk.context,
        "embed_hash": chunk.embed_hash if chunk.context else None,
    }


_COLUMNS = (
    "chunk_id, source_type, source_id, seq, parent_id, doc_type, text, category, brand, size, "
    "colour, material, price_band, region, updated, visibility, content_hash, text_hash, "
    "embedder_id, index_version, context, embed_hash"
)
_PLACEHOLDERS = ", ".join(f"%({name.strip()})s" for name in _COLUMNS.split(","))
_INSERT = (
    f"INSERT INTO rag_chunks ({_COLUMNS}, embedding) "  # noqa: S608 - constants, values are bound
    f"VALUES ({_PLACEHOLDERS}, %(embedding)s::vector)"
)
_UPDATE_SET = ", ".join(
    f"{name.strip()} = %({name.strip()})s"
    for name in _COLUMNS.split(",")
    if name.strip() != "chunk_id"
)
_UPDATE = (
    f"UPDATE rag_chunks SET {_UPDATE_SET}, "  # noqa: S608 - constants, values are bound
    "embedding = COALESCE(%(embedding)s::vector, embedding), updated_at = now() "
    "WHERE chunk_id = %(chunk_id)s"
)


# Plain rows only: generated context stays inside the index writers (decision 0010), so this never
# fetches it, and a plain chunk's embedded text is its text. Read by the frozen vectors (RAG-03,
# decision 0012); moved here from `embedding.py` so only the two readers run SQL on the index.
_PLAIN_ROWS = """SELECT chunk_id, text, embedder_id, embedding::real[]::float8[]
FROM rag_chunks WHERE chunk_id = ANY(%(ids)s) AND context IS NULL"""


def plain_chunk_rows(conn: psycopg.Connection, chunk_ids: Sequence[str]) -> list[tuple[Any, ...]]:
    """(chunk_id, text, embedder_id, stored vector) for each plain row among `chunk_ids`."""
    return list(conn.execute(_PLAIN_ROWS, {"ids": list(chunk_ids)}).fetchall())


_CURRENT_MANIFEST = """SELECT index_version, data_version, dataset_sha256, chunker_version,
    embedder_id, created_at
FROM rag_index_manifest WHERE is_current"""
_VISIBLE_COUNTS = """SELECT source_type, count(*) FROM rag_chunks
WHERE visibility = ANY(%(visible)s) GROUP BY source_type ORDER BY source_type"""


def index_manifest(conn: psycopg.Connection, audience: str) -> dict[str, Any] | None:
    """The current index version and chunk counts by source type, counting only what `audience`
    may see, so a shopper learns nothing about internal documents (CC-10, hard rule 7). None when
    no index is published. No chunk text or context is read."""
    from northwind_rag.retrieval import visible_to  # retrieval imports this module

    visible = list(visible_to(audience))
    row = conn.execute(_CURRENT_MANIFEST).fetchone()
    if row is None:
        return None
    index_version, data_version, dataset_sha256, chunker_version, embedder_id, created_at = row
    counts = conn.execute(_VISIBLE_COUNTS, {"visible": visible}).fetchall()
    return {
        "index_version": index_version,
        "data_version": data_version,
        "dataset_sha256": dataset_sha256,
        "chunker_version": chunker_version,
        "embedder_id": embedder_id,
        "refreshed_at": created_at.isoformat(),
        "chunks_by_source_type": {source_type: int(n) for source_type, n in counts},
    }


def _publish_manifest(
    conn: psycopg.Connection,
    index_version: str,
    version: IndexVersion,
    corpus_sha256: str,
    embedder_id: str,
    chunk_count: int,
) -> None:
    """Writes nothing when this version is already current, which keeps a re-run a no-op."""
    conn.execute(
        "UPDATE rag_index_manifest SET is_current = false WHERE is_current AND index_version <> %s",
        (index_version,),
    )
    conn.execute(
        "INSERT INTO rag_index_manifest (index_version, data_version, dataset_sha256, "
        "corpus_sha256, chunker_version, embedder_id, chunk_count, is_current) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, true) "
        "ON CONFLICT (index_version) DO UPDATE SET is_current = true "
        "WHERE NOT rag_index_manifest.is_current",
        (
            index_version,
            version.data_version,
            version.dataset_sha256,
            corpus_sha256,
            version.chunker_version,
            embedder_id,
            chunk_count,
        ),
    )


def sync_chunks(
    conn: psycopg.Connection,
    chunks: Sequence[Chunk],
    embedder: Embedder,
    version: IndexVersion,
) -> IngestReport:
    """Makes the index equal `chunks` in one transaction: all of the change, or none of it.

    A chunk is embedded only if it is new, what it embeds changed, or its stored embedder id
    differs; a metadata-only change keeps its vector. What it embeds is its text, or its context
    and text (RAG-02); a plain row has no `embed_hash`, so its text hash stands in.

    A row's `index_version` is the version that last wrote it, so unchanged rows keep an older
    one. Find the live version in `rag_index_manifest.is_current`; never filter rows by it.
    """
    for chunk in chunks:
        check_chunk(chunk)
    corpus = _corpus_sha256(chunks)
    index_version = _index_version(version, corpus, embedder.id)
    wanted = {c.chunk_id: c for c in chunks}

    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK,))
        column_dim = embedding_column_dim(conn)
        if embedder.dim != column_dim:
            raise IngestError(
                f"embedder {embedder.id} has {embedder.dim} dims, column {column_dim}"
            )
        rows = conn.execute(
            "SELECT chunk_id, content_hash, COALESCE(embed_hash, text_hash), embedder_id "
            "FROM rag_chunks"
        ).fetchall()
        existing: dict[str, tuple[str, str, str]] = {r[0]: (r[1], r[2], r[3]) for r in rows}

        to_write = [
            c
            for c in chunks
            if c.chunk_id not in existing
            or existing[c.chunk_id][0] != c.content_hash
            or existing[c.chunk_id][2] != embedder.id
        ]
        to_embed = [
            c
            for c in to_write
            if c.chunk_id not in existing
            or existing[c.chunk_id][1] != c.embed_hash
            or existing[c.chunk_id][2] != embedder.id
        ]
        vectors = embedder.embed([c.embed_text for c in to_embed]) if to_embed else []
        if len(vectors) != len(to_embed) or any(len(v) != column_dim for v in vectors):
            raise IngestError(f"embedder {embedder.id} returned vectors of the wrong shape")
        literals = {c.chunk_id: vector_literal(v) for c, v in zip(to_embed, vectors, strict=True)}

        _publish_manifest(conn, index_version, version, corpus, embedder.id, len(chunks))

        new = [c for c in to_write if c.chunk_id not in existing]
        changed = [c for c in to_write if c.chunk_id in existing]
        stale = [chunk_id for chunk_id in existing if chunk_id not in wanted]
        with conn.cursor() as cursor:
            if new:
                cursor.executemany(
                    _INSERT,
                    [
                        {
                            **_row(c, embedder.id, index_version),
                            "embedding": literals[c.chunk_id],
                        }
                        for c in new
                    ],
                )
            if changed:
                cursor.executemany(
                    _UPDATE,
                    [
                        {
                            **_row(c, embedder.id, index_version),
                            "embedding": literals.get(c.chunk_id),
                        }
                        for c in changed
                    ],
                )
            if stale:
                cursor.execute("DELETE FROM rag_chunks WHERE chunk_id = ANY(%s)", (stale,))

    return IngestReport(
        index_version=index_version,
        data_version=version.data_version,
        chunks=len(chunks),
        inserted=len(new),
        updated=len(changed),
        deleted=len(stale),
        unchanged=len(chunks) - len(to_write),
        embedded=len(to_embed),
    )
