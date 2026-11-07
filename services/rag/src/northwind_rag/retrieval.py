"""Retrieval over the RAG index: dense, keyword and their fusion (RAG-12 baseline, RAG-03 hybrid).

`audience` is required and maps to the visibilities a caller may see, so a query cannot reach
internal chunks by leaving it out (hard rule 7). The SQL does an exact scan, there being no ANN
index, and ties break on chunk_id, so a given query always gives the same order.

Agents use `retrieve_context`, which is hybrid (RAG-03): the top `CANDIDATE_DEPTH` (or `k`, if
larger) of a dense list and of a keyword list, fused by reciprocal rank (RRF, `RRF_K`). Both lists
come from one statement under one filter, so neither can see a chunk the other's filter refused.
`dense_search` and `keyword_search` stay as the comparison arms for evals. The parameters were
fixed in the plan before anything was measured (docs/reviews/2026-11-07-RAG-03-plan.md), and
changing one is a new retriever.

The keyword list searches `fts`, generated from `text` only: context is ranking-only through the
embedding (decisions 0010 and 0012). The query's lexemes are OR-ed, so keyword-only is a real
competitor: AND would match almost nothing on a natural question.

`region` is an array column, so the region filter is `&&`, and the visibility filter stays because
internal documents have region ALL (hard rule 7).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, get_args

import psycopg

from northwind_rag.embedding import Embedder
from northwind_rag.sources import ALL_REGIONS
from northwind_rag.store import vector_literal

Audience = Literal["shopper", "staff"]
ShopperRegion = Literal["US", "CA", "UK", "EU"]

RETRIEVER_VERSION = 2
CONTEXT_K = 5
RRF_K = 60
CANDIDATE_DEPTH = 50
FTS_CONFIG = "english"  # must match infra/db/migrations/0003_rag_fts.sql
SHOPPER_REGIONS: frozenset[str] = frozenset(get_args(ShopperRegion))
_VISIBLE: dict[str, tuple[str, ...]] = {"shopper": ("public",), "staff": ("public", "internal")}

_FILTER = """visibility = ANY(%(visible)s)
  AND (%(regions)s::text[] IS NULL OR region && %(regions)s::text[])"""

# `plainto_tsquery` ANDs the lexemes; its text form quotes each one, so swapping the operator and
# casting back cannot inject query syntax. A query of stop words only is empty and matches nothing.
_QUERY = "SELECT replace(plainto_tsquery('english', %(query)s)::text, ' & ', ' | ')::tsquery AS q"

_SEARCH = f"""
SELECT chunk_id, source_type, source_id, doc_type, text
FROM rag_chunks
WHERE {_FILTER}
ORDER BY embedding <=> %(vector)s::vector, chunk_id
LIMIT %(k)s
"""  # noqa: S608 - constants only; every value is bound

_KEYWORD = f"""
WITH query AS ({_QUERY})
SELECT chunk_id, source_type, source_id, doc_type, text
FROM rag_chunks, query
WHERE {_FILTER}
  AND fts @@ query.q
ORDER BY ts_rank_cd(fts, query.q) DESC, chunk_id
LIMIT %(k)s
"""  # noqa: S608 - constants only; every value is bound

_HYBRID = f"""
WITH query AS ({_QUERY}),
dense AS (
    SELECT chunk_id, source_type, source_id, doc_type, text
    FROM rag_chunks
    WHERE {_FILTER}
    ORDER BY embedding <=> %(vector)s::vector, chunk_id
    LIMIT %(depth)s
),
keyword AS (
    SELECT chunk_id, source_type, source_id, doc_type, text
    FROM rag_chunks, query
    WHERE {_FILTER}
      AND fts @@ query.q
    ORDER BY ts_rank_cd(fts, query.q) DESC, chunk_id
    LIMIT %(depth)s
)
SELECT 'dense' AS arm, chunk_id, source_type, source_id, doc_type, text FROM dense
UNION ALL
SELECT 'keyword' AS arm, chunk_id, source_type, source_id, doc_type, text FROM keyword
"""  # noqa: S608 - constants only; every value is bound


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    source_type: str
    source_id: str
    doc_type: str | None
    text: str


def retriever_id(embedder: Embedder) -> str:
    """Names the retrieval method; a different ID means saved contexts no longer apply."""
    return (
        f"hybrid:v{RETRIEVER_VERSION}:rrf{RRF_K}:d{CANDIDATE_DEPTH}:"
        f"fts-text-{FTS_CONFIG}:{embedder.id}"
    )


def rrf_fuse(ranked_lists: Sequence[Sequence[str]], *, rrf_k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion: each ID scores the sum of 1 / (rrf_k + rank) over the lists it is
    in, rank counted from 1. Exact fractions, so equal scores are equal on every platform, and ties
    break on the ID. An ID repeated in one list counts at its first rank only."""
    if rrf_k < 1:
        raise ValueError("rrf_k must be at least 1")
    scores: dict[str, Fraction] = {}
    for ranked in ranked_lists:
        seen: set[str] = set()
        for rank, item in enumerate(ranked, start=1):
            if item in seen:
                continue
            seen.add(item)
            scores[item] = scores.get(item, Fraction(0)) + Fraction(1, rrf_k + rank)
    return sorted(scores, key=lambda item: (-scores[item], item))


def _check(audience: str, k: int) -> None:
    if audience not in _VISIBLE:
        raise ValueError(f"unknown audience: {audience!r}")
    if k < 1:
        raise ValueError("k must be at least 1")


def _params(audience: Audience, regions: list[str] | None, k: int) -> dict[str, object]:
    return {"visible": list(_VISIBLE[audience]), "regions": regions, "k": k}


def _dense(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    audience: Audience,
    regions: list[str] | None,
    k: int,
) -> list[RetrievedChunk]:
    _check(audience, k)
    [vector] = embedder.embed([query])
    params = {**_params(audience, regions, k), "vector": vector_literal(vector)}
    with conn.cursor() as cursor:
        rows = cursor.execute(_SEARCH, params).fetchall()
    return [RetrievedChunk(*row) for row in rows]


def _keyword(
    conn: psycopg.Connection,
    query: str,
    *,
    audience: Audience,
    regions: list[str] | None,
    k: int,
) -> list[RetrievedChunk]:
    _check(audience, k)
    params = {**_params(audience, regions, k), "query": query}
    with conn.cursor() as cursor:
        rows = cursor.execute(_KEYWORD, params).fetchall()
    return [RetrievedChunk(*row) for row in rows]


def _hybrid(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    audience: Audience,
    regions: list[str] | None,
    k: int,
) -> list[RetrievedChunk]:
    _check(audience, k)
    [vector] = embedder.embed([query])
    params = {
        **_params(audience, regions, k),
        "vector": vector_literal(vector),
        "query": query,
        # Never fewer candidates than asked for, so a large `k` still sees every match.
        "depth": max(CANDIDATE_DEPTH, k),
    }
    with conn.cursor() as cursor:
        rows = cursor.execute(_HYBRID, params).fetchall()
    chunks = {row[1]: RetrievedChunk(*row[1:]) for row in rows}
    arms = [[row[1] for row in rows if row[0] == arm] for arm in ("dense", "keyword")]
    return [chunks[chunk_id] for chunk_id in rrf_fuse(arms)[:k]]


def _regions(region: ShopperRegion | None, audience: Audience) -> list[str] | None:
    """A shopper must have a real region, or this refuses. Only staff may pass None, for no region
    filter, since P0009's chunks have a NULL region and `&&` never matches NULL."""
    if audience not in _VISIBLE:
        raise ValueError(f"unknown audience: {audience!r}")
    if region is None and audience == "staff":
        return None
    if region not in SHOPPER_REGIONS:
        raise ValueError(f"unknown region: {region!r}")
    return [region, ALL_REGIONS]


def dense_search(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    audience: Audience,
    k: int,
    region: ShopperRegion | None = None,
) -> list[RetrievedChunk]:
    """The `k` chunks nearest to `query` by cosine distance that `audience` may see. With no
    region, any region (the RAG-12 dense-only path); with one, the same filter as agents get.
    An eval comparison arm; agents use `retrieve_context`."""
    regions = None if region is None else _regions(region, audience)
    return _dense(conn, embedder, query, audience=audience, regions=regions, k=k)


def keyword_search(
    conn: psycopg.Connection,
    query: str,
    *,
    audience: Audience,
    k: int,
    region: ShopperRegion | None = None,
) -> list[RetrievedChunk]:
    """The `k` best full-text matches for `query` that `audience` may see, ranked by `ts_rank_cd`.
    A shopper must name a region, as for `retrieve_context`; only staff may pass None. An eval
    comparison arm; agents use `retrieve_context`."""
    return _keyword(conn, query, audience=audience, regions=_regions(region, audience), k=k)


def retrieve_context(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    region: ShopperRegion | None,
    audience: Audience = "shopper",
    k: int = CONTEXT_K,
) -> list[RetrievedChunk]:
    """What an agent may put in front of the model for this caller: hybrid retrieval (RAG-03).
    `audience` and `region` come from the caller's identity on the server, never from the model
    (hard rule 7)."""
    regions = _regions(region, audience)
    return _hybrid(conn, embedder, query, audience=audience, regions=regions, k=k)
