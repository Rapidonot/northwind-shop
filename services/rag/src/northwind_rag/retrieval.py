"""Dense retrieval over the RAG index (RAG-12 baseline; RAG-03 adds keyword search and fusion).

`audience` is required and maps to the visibilities a caller may see, so a query cannot reach
internal chunks by leaving it out (hard rule 7). The SQL does an exact scan, there being no ANN
index, and ties break on chunk_id, so a given query vector always gives the same order.

`dense_search` applies no region filter: the eval baseline stands on it until #8 re-snapshots.
`retrieve_context` is the path for agents. It needs a shopper's region, because one prompt must not
hold four regions' conflicting rules. `region` is an array column, so the filter is `&&`, and the
visibility filter stays because internal documents have region ALL (hard rule 7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

import psycopg

from northwind_rag.embedding import Embedder
from northwind_rag.sources import ALL_REGIONS
from northwind_rag.store import vector_literal

Audience = Literal["shopper", "staff"]
ShopperRegion = Literal["US", "CA", "UK", "EU"]

RETRIEVER_VERSION = 1
CONTEXT_K = 5
SHOPPER_REGIONS: frozenset[str] = frozenset(get_args(ShopperRegion))
_VISIBLE: dict[str, tuple[str, ...]] = {"shopper": ("public",), "staff": ("public", "internal")}

_SEARCH = """
SELECT chunk_id, source_type, source_id, doc_type, text
FROM rag_chunks
WHERE visibility = ANY(%(visible)s)
  AND (%(regions)s::text[] IS NULL OR region && %(regions)s::text[])
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


def _search(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    audience: Audience,
    regions: list[str] | None,
    k: int,
) -> list[RetrievedChunk]:
    if audience not in _VISIBLE:
        raise ValueError(f"unknown audience: {audience!r}")
    if k < 1:
        raise ValueError("k must be at least 1")
    [vector] = embedder.embed([query])
    params = {
        "visible": list(_VISIBLE[audience]),
        "regions": regions,
        "vector": vector_literal(vector),
        "k": k,
    }
    with conn.cursor() as cursor:
        rows = cursor.execute(_SEARCH, params).fetchall()
    return [RetrievedChunk(*row) for row in rows]


def dense_search(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    audience: Audience,
    k: int,
) -> list[RetrievedChunk]:
    """The `k` chunks nearest to `query` by cosine distance that `audience` may see, in any
    region. For evals; agents use `retrieve_context`."""
    return _search(conn, embedder, query, audience=audience, regions=None, k=k)


def retrieve_context(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    region: ShopperRegion | None,
    audience: Audience = "shopper",
    k: int = CONTEXT_K,
) -> list[RetrievedChunk]:
    """What an agent may put in front of the model for this caller. `audience` and `region` come
    from the caller's identity on the server, never from the model (hard rule 7).

    A shopper must have a real region, or this refuses. Only staff may pass None, for no region
    filter, since P0009's chunks have a NULL region and `&&` never matches NULL."""
    if audience not in _VISIBLE:
        raise ValueError(f"unknown audience: {audience!r}")
    if region is None and audience == "staff":
        return _search(conn, embedder, query, audience=audience, regions=None, k=k)
    if region not in SHOPPER_REGIONS:
        raise ValueError(f"unknown region: {region!r}")
    return _search(conn, embedder, query, audience=audience, regions=[region, ALL_REGIONS], k=k)
