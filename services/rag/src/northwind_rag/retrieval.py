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

Row #11 (RAG-04, decision 0022): `retrieve_planned` runs a query plan's parts. Each part may narrow
by colour and size, through the index's metadata arrays (a chunk where the attribute does not apply,
NULL, passes), and by a live product allow-list (`SearchFilter.products`), which the caller resolved
from live price and stock (hard rule 2): product, variant and review chunks are kept only for those
products, and documents pass. With no filter the statement returns what it always did. Several parts
are fused by reciprocal rank over their chunk lists, the same `rrf_fuse` as the two arms.

The dense order does not depend on the CPU (decision 0012, the PR #30 drift). pgvector's `<=>` is a
float32 sum whose order varies by platform, so the SQL only picks a pool of `RERANK_FACTOR` times
the candidates needed, with their exact float32 vectors. Python then ranks the pool by
`exact_cosine_distance`, which gives the same bits everywhere, and breaks ties on `chunk_id` in
code-point order, the `COLLATE "C"` the SQL uses too. If the pool's edge is too close to call,
the same statement runs again with no dense limit, and every filtered row is ranked. Nothing here
raises on a zero vector: its distance is infinite.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from math import fsum, inf, isfinite, sqrt
from typing import Any, Literal, get_args

import psycopg

from northwind_rag.embedding import Embedder, to_float32
from northwind_rag.sources import ALL_REGIONS
from northwind_rag.store import vector_literal

Audience = Literal["shopper", "staff"]
ShopperRegion = Literal["US", "CA", "UK", "EU"]

RETRIEVER_VERSION = 3  # row #11: query-plan filters and parts (RAG-04)
CONTEXT_K = 5
RRF_K = 60
CANDIDATE_DEPTH = 50
RERANK_FACTOR = 2  # the dense pool is twice what fusion takes, so the edge is rarely close
# Above the worst case for pgvector's float32 sum over 384 terms (about 2.3e-5), not only the
# 3.7e-7 measured on macOS, so a pool judged safe is safe on any CPU.
RERANK_MARGIN = 1e-4
FTS_CONFIG = "english"  # must match infra/db/migrations/0003_rag_fts.sql
SHOPPER_REGIONS: frozenset[str] = frozenset(get_args(ShopperRegion))
_VISIBLE: dict[str, tuple[str, ...]] = {"shopper": ("public",), "staff": ("public", "internal")}

_FILTER = """visibility = ANY(%(visible)s)
  AND (%(regions)s::text[] IS NULL OR region && %(regions)s::text[])
  AND (%(colours)s::text[] IS NULL OR colour IS NULL OR colour && %(colours)s::text[])
  AND (%(sizes)s::text[] IS NULL OR size IS NULL OR size && %(sizes)s::text[])
  AND (%(products)s::text[] IS NULL OR source_type = 'document'
       OR (CASE WHEN source_type = 'product' THEN source_id ELSE parent_id END)
          = ANY(%(products)s::text[]))"""

# `plainto_tsquery` ANDs the lexemes; its text form quotes each one, so swapping the operator and
# casting back cannot inject query syntax. A query of stop words only is empty and matches nothing.
_QUERY = "SELECT replace(plainto_tsquery('english', %(query)s)::text, ' & ', ' | ')::tsquery AS q"

# The dense pool: `pg_distance` orders it and guards its edge; `exact` is the stored float32 vector,
# exactly, since float4 to float8 is exact and float8 prints in shortest round-trip form.
_DENSE_COLUMNS = """chunk_id, source_type, source_id, doc_type, text,
    embedding <=> %(vector)s::vector AS pg_distance, embedding::real[]::float8[] AS exact"""
_SEARCH = f"""
SELECT {_DENSE_COLUMNS}
FROM rag_chunks
WHERE {_FILTER}
ORDER BY embedding <=> %(vector)s::vector, chunk_id COLLATE "C"
LIMIT %(pool)s
"""  # noqa: S608 - constants only; every value is bound

_KEYWORD = f"""
WITH query AS ({_QUERY})
SELECT chunk_id, source_type, source_id, doc_type, text
FROM rag_chunks, query
WHERE {_FILTER}
  AND fts @@ query.q
ORDER BY ts_rank_cd(fts, query.q) DESC, chunk_id COLLATE "C"
LIMIT %(k)s
"""  # noqa: S608 - constants only; every value is bound

_HYBRID = f"""
WITH query AS ({_QUERY}),
dense AS (
    SELECT {_DENSE_COLUMNS}
    FROM rag_chunks
    WHERE {_FILTER}
    ORDER BY embedding <=> %(vector)s::vector, chunk_id COLLATE "C"
    LIMIT %(pool)s
),
keyword AS (
    SELECT chunk_id, source_type, source_id, doc_type, text
    FROM rag_chunks, query
    WHERE {_FILTER}
      AND fts @@ query.q
    ORDER BY ts_rank_cd(fts, query.q) DESC, chunk_id COLLATE "C"
    LIMIT %(depth)s
)
SELECT 'dense' AS arm, chunk_id, source_type, source_id, doc_type, text, pg_distance, exact
FROM dense
UNION ALL
SELECT 'keyword' AS arm, chunk_id, source_type, source_id, doc_type, text,
    NULL::float8, NULL::float8[]
FROM keyword
"""  # noqa: S608 - constants only; every value is bound


@dataclass(frozen=True)
class SearchFilter:
    """What one part of a query plan narrows to (row #11). None means no filter on that field.
    `products` is a live allow-list the caller resolved from price and stock, never the index."""

    colours: tuple[str, ...] | None = None
    sizes: tuple[str, ...] | None = None
    products: frozenset[str] | None = None


NO_FILTER = SearchFilter()


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


def exact_cosine_distance(query: Sequence[float], vector: Sequence[float]) -> float:
    """1 - cosine similarity, the same bits on every CPU: both sides rounded to float32, so each
    product is exact in float64, each sum is `fsum` (correctly rounded), and sqrt and division
    are IEEE. A zero vector is infinitely far, where pgvector gives NaN."""
    q = [to_float32(x) for x in query]
    v = [to_float32(x) for x in vector]
    nq, nv = fsum(x * x for x in q), fsum(y * y for y in v)
    if nq == 0.0 or nv == 0.0:
        return inf
    return 1.0 - fsum(x * y for x, y in zip(q, v, strict=True)) / sqrt(nq * nv)


def _pool_is_safe(last_pg_distance: float, exact_at_keep: float) -> bool:
    """No chunk outside the pool can rank inside it: every row outside has a pgvector distance of
    at least `last_pg_distance`, which is within float32 error of its exact one."""
    return isfinite(last_pg_distance) and last_pg_distance >= exact_at_keep + RERANK_MARGIN


@dataclass(frozen=True)
class _Candidate:
    chunk: RetrievedChunk
    pg_distance: float | None
    distance: float


def _candidates(rows: Sequence[Sequence[Any]], vector: Sequence[float]) -> list[_Candidate]:
    """Dense rows: the five chunk columns, then `pg_distance`, then `exact`."""
    return [
        _Candidate(RetrievedChunk(*row[:5]), row[5], exact_cosine_distance(vector, row[6]))
        for row in rows
    ]


def _edge_is_safe(found: Sequence[_Candidate], exact_at_keep: float) -> bool:
    distances = [c.pg_distance for c in found]
    if any(d is None or not isfinite(d) for d in distances):
        return False  # a zero-norm row (NaN): rank every row rather than reason about it
    return _pool_is_safe(max(d for d in distances if d is not None), exact_at_keep)


def _exactly_ranked(fetch: Callable[[int | None], list[_Candidate]], keep: int) -> list[_Candidate]:
    """The `keep` nearest by exact distance, then ID. `fetch(pool)` runs the statement with that
    dense limit, `fetch(None)` with none. A pool that came back short holds every filtered row."""
    pool = RERANK_FACTOR * keep
    found = fetch(pool)
    ranked = sorted(found, key=lambda c: (c.distance, c.chunk.chunk_id))
    if len(found) >= pool and not _edge_is_safe(found, ranked[keep - 1].distance):
        ranked = sorted(fetch(None), key=lambda c: (c.distance, c.chunk.chunk_id))
    return ranked[:keep]


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


def visible_to(audience: str) -> tuple[str, ...]:
    """The visibilities `audience` may see; the one definition every reader uses (hard rule 7)."""
    if audience not in _VISIBLE:
        raise ValueError(f"unknown audience: {audience!r}")
    return _VISIBLE[audience]


def _check(audience: str, k: int) -> None:
    if audience not in _VISIBLE:
        raise ValueError(f"unknown audience: {audience!r}")
    if k < 1:
        raise ValueError("k must be at least 1")


def _params(
    audience: Audience, regions: list[str] | None, k: int, narrow: SearchFilter = NO_FILTER
) -> dict[str, object]:
    return {
        "visible": list(_VISIBLE[audience]),
        "regions": regions,
        "k": k,
        "colours": None if narrow.colours is None else list(narrow.colours),
        "sizes": None if narrow.sizes is None else list(narrow.sizes),
        "products": None if narrow.products is None else sorted(narrow.products),
    }


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

    def fetch(pool: int | None) -> list[_Candidate]:
        with conn.cursor() as cursor:
            rows = cursor.execute(_SEARCH, {**params, "pool": pool}).fetchall()
        return _candidates(rows, vector)

    return [c.chunk for c in _exactly_ranked(fetch, k)]


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
    narrow: SearchFilter = NO_FILTER,
) -> list[RetrievedChunk]:
    _check(audience, k)
    [vector] = embedder.embed([query])
    # Never fewer candidates than asked for, so a large `k` still sees every match.
    depth = max(CANDIDATE_DEPTH, k)
    params = {
        **_params(audience, regions, k, narrow),
        "vector": vector_literal(vector),
        "query": query,
        "depth": depth,
    }
    keyword: list[RetrievedChunk] = []

    def fetch(pool: int | None) -> list[_Candidate]:
        with conn.cursor() as cursor:
            rows = cursor.execute(_HYBRID, {**params, "pool": pool}).fetchall()
        keyword[:] = [RetrievedChunk(*row[1:6]) for row in rows if row[0] == "keyword"]
        return _candidates([row[1:] for row in rows if row[0] == "dense"], vector)

    dense = [c.chunk for c in _exactly_ranked(fetch, depth)]
    chunks = {c.chunk_id: c for c in [*dense, *keyword]}
    arms = [[c.chunk_id for c in dense], [c.chunk_id for c in keyword]]
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


def retrieve_planned(
    conn: psycopg.Connection,
    embedder: Embedder,
    parts: Sequence[tuple[str, SearchFilter]],
    *,
    region: ShopperRegion | None,
    audience: Audience = "shopper",
    k: int = CONTEXT_K,
) -> list[RetrievedChunk]:
    """A query plan's search (row #11, RAG-04): each part's hybrid search under its own filter, the
    parts fused by reciprocal rank, the top `k`. One part with no filter is `retrieve_context`
    exactly. `audience` and `region` come from the caller, never from the plan (hard rule 7)."""
    if not 1 <= len(parts) <= 3:
        raise ValueError("a plan has one to three parts")
    regions = _regions(region, audience)
    found = [
        _hybrid(conn, embedder, text, audience=audience, regions=regions, k=k, narrow=narrow)
        for text, narrow in parts
    ]
    if len(found) == 1:
        return found[0]
    chunks = {c.chunk_id: c for result in found for c in result}
    return [chunks[i] for i in rrf_fuse([[c.chunk_id for c in r] for r in found])[:k]]
