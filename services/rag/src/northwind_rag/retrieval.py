"""Dense retrieval over the RAG index (RAG-12 baseline; RAG-03 adds keyword search and fusion).

`audience` is required and maps to the visibilities a caller may see, so a query cannot reach
internal chunks by leaving it out (hard rule 7). The SQL does an exact scan, there being no ANN
index, and ties break on chunk_id, so a given query vector always gives the same order.

Region filtering belongs to RAG-11; this retriever does not apply one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import psycopg

from northwind_rag.embedding import Embedder
from northwind_rag.store import vector_literal

Audience = Literal["shopper", "staff"]

RETRIEVER_VERSION = 1
_VISIBLE: dict[str, tuple[str, ...]] = {"shopper": ("public",), "staff": ("public", "internal")}

_SEARCH = """
SELECT chunk_id, source_type, source_id, doc_type, text
FROM rag_chunks
WHERE visibility = ANY(%(visible)s)
ORDER BY embedding <=> %(vector)s::vector, chunk_id
LIMIT %(k)s
"""


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    source_type: str
    source_id: str
    doc_type: str | None
    text: str


def retriever_id(embedder: Embedder) -> str:
    """Names the retrieval method; a different ID means saved contexts no longer apply."""
    return f"dense:v{RETRIEVER_VERSION}:{embedder.id}"


def dense_search(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    audience: Audience,
    k: int,
) -> list[RetrievedChunk]:
    """The `k` chunks nearest to `query` by cosine distance that `audience` may see."""
    if audience not in _VISIBLE:
        raise ValueError(f"unknown audience: {audience!r}")
    if k < 1:
        raise ValueError("k must be at least 1")
    [vector] = embedder.embed([query])
    params = {"visible": list(_VISIBLE[audience]), "vector": vector_literal(vector), "k": k}
    with conn.cursor() as cursor:
        rows = cursor.execute(_SEARCH, params).fetchall()
    return [RetrievedChunk(*row) for row in rows]
