"""The dense order is a function of the stored vectors only (RAG-03, the macOS/Linux drift).

PR #30's Linux CI reordered three near-tied dense pairs, one 1.96e-7 apart, so EV-029 went stale
(docs/reviews/2026-11-08-RAG-03-drift.md). pgvector computes `<=>` in float32 with an
order-dependent sum. So the dense candidates are re-ranked in Python: exact float32 products in
float64, `fsum`, then IEEE `sqrt` and division, which give the same bits on every CPU. Ties break
on `chunk_id` in code-point order, which is `COLLATE "C"` in the SQL.
"""

from __future__ import annotations

import math
import random
import re
import struct
from collections.abc import Callable, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
from psycopg import sql

from northwind_rag import retrieval
from northwind_rag.ingest import ingest
from northwind_rag.retrieval import dense_search, keyword_search, retrieve_context
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("RAG-03")

Rows = list[tuple[Any, ...]]
QUERIES = ("waterproof jacket with a hood", "machine washable wool throw", "return a sofa")


def f32(x: float) -> float:
    return cast(float, struct.unpack("<f", struct.pack("<f", x))[0])


# ---- the distance (unit) ----------------------------------------------------------------------


def reference(q: Sequence[float], v: Sequence[float]) -> float:
    """Each sum exact in `Fraction`, rounded once (what `fsum` returns), then the same float64
    steps in the same order: product, sqrt, division, 1 - x."""
    a, b = [f32(x) for x in q], [f32(x) for x in v]
    dot = float(sum(Fraction(x) * Fraction(y) for x, y in zip(a, b, strict=True)))
    na = float(sum(Fraction(x) * Fraction(x) for x in a))
    nb = float(sum(Fraction(y) * Fraction(y) for y in b))
    if na == 0.0 or nb == 0.0:
        return math.inf
    return 1.0 - dot / math.sqrt(na * nb)


def test_the_distance_equals_the_exact_reference_bit_for_bit() -> None:
    rng = random.Random(3)  # noqa: S311 - test data, not security
    for _ in range(300):
        q = [rng.uniform(-1, 1) for _ in range(384)]
        v = [rng.uniform(-1, 1) for _ in range(384)]
        got = retrieval.exact_cosine_distance(q, v)
        assert struct.pack("<d", got) == struct.pack("<d", reference(q, v))


# Found by search: a float32 kernel that sums in order ranks V1 first; the exact distance ranks V2.
Q = [
    -0.9008213877677917, -0.5578363537788391, 0.11332979798316956, -0.7336503863334656,
    -0.16172191500663757, 0.08137176930904388, 0.14182737469673157, 0.12051455676555634,
]  # fmt: skip
V1 = [
    0.3640053868293762, -0.7938885688781738, 0.14240878820419312, -0.6242579221725464,
    -0.8051388263702393, 0.42422154545783997, 0.12873658537864685, 0.2380191832780838,
]  # fmt: skip
V2 = [*V1[:7], 0.2380194216966629]


def float32_kernel(q: Sequence[float], v: Sequence[float]) -> float:
    dot = na = nb = 0.0
    for a, b in zip(q, v, strict=True):
        dot, na, nb = f32(dot + f32(a * b)), f32(na + f32(a * a)), f32(nb + f32(b * b))
    return f32(1.0 - f32(dot / f32(math.sqrt(f32(na * nb)))))


def test_a_pair_a_float32_sum_misorders_is_ordered_the_exact_way() -> None:
    assert float32_kernel(Q, V1) < float32_kernel(Q, V2)  # the pair is the one it claims to be
    assert reference(Q, V2) < reference(Q, V1)
    assert retrieval.exact_cosine_distance(Q, V2) < retrieval.exact_cosine_distance(Q, V1)


def test_a_zero_vector_is_infinitely_far_and_never_raises() -> None:
    assert retrieval.exact_cosine_distance([0.0] * 8, V1) == math.inf
    assert retrieval.exact_cosine_distance(Q, [0.0] * 8) == math.inf


@pytest.mark.parametrize(
    ("last_pg", "exact_at_keep", "safe"),
    [
        (0.5, 0.4, True),
        (0.4 + 1e-6, 0.4, False),  # inside the margin: something outside the pool might belong
        (math.nan, 0.4, False),  # a zero-norm row gives NaN in pgvector
        (math.inf, 0.4, False),
        (0.5, math.inf, False),
    ],
)
def test_the_pool_guard(last_pg: float, exact_at_keep: float, safe: bool) -> None:
    assert retrieval._pool_is_safe(last_pg, exact_at_keep) is safe


# ---- the SQL tie-break (unit) -----------------------------------------------------------------


@pytest.mark.parametrize(("name", "count"), [("_SEARCH", 1), ("_KEYWORD", 1), ("_HYBRID", 2)])
def test_every_tie_break_is_in_code_point_order(name: str, count: int) -> None:
    text = vars(retrieval)[name]
    assert text.count('chunk_id COLLATE "C"') == count
    assert not re.search(r'ORDER BY[^\n]*chunk_id(?! COLLATE "C")', text)


# ---- against a real index (integration) -------------------------------------------------------


@pytest.fixture
def embedder(conn: psycopg.Connection, fixtures_dir: Path) -> FakeEmbedder:
    fake = FakeEmbedder()
    ingest(conn, fixtures_dir, fake)
    return fake


class _Cursor:
    def __init__(self, cursor: psycopg.Cursor[Any], owner: Reordering) -> None:
        self._cursor, self._owner = cursor, owner

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        self._cursor.close()

    def execute(self, query: Any, params: Any = None) -> _Cursor:
        self._owner.statements += 1
        self._cursor.execute(query, params)
        return self

    def fetchall(self) -> Rows:
        return self._owner.reorder(list(self._cursor.fetchall()))


class Reordering:
    """A connection whose dense rows come back in another order, as another CPU could return
    near-tied rows. Keyword rows keep their order."""

    def __init__(self, conn: psycopg.Connection, reorder: Callable[[Rows], Rows]) -> None:
        self._conn, self._reorder, self.statements = conn, reorder, 0

    def cursor(self) -> _Cursor:
        return _Cursor(self._conn.cursor(), self)

    def execute(self, query: Any, params: Any = None) -> Any:
        return self._conn.execute(query, params)

    def reorder(self, rows: Rows) -> Rows:
        if rows and rows[0][0] in ("dense", "keyword"):
            dense = [r for r in rows if r[0] == "dense"]
            return self._reorder(dense) + [r for r in rows if r[0] != "dense"]
        return self._reorder(rows)


def reverse(rows: Rows) -> Rows:
    return rows[::-1]


def shuffle(rows: Rows) -> Rows:
    rows = list(rows)
    random.Random(11).shuffle(rows)  # noqa: S311 - test data, not security
    return rows


def ids(chunks: Sequence[Any]) -> list[str]:
    return [c.chunk_id for c in chunks]


def proxy(conn: psycopg.Connection, reorder: Callable[[Rows], Rows]) -> psycopg.Connection:
    return cast(psycopg.Connection, Reordering(conn, reorder))


@pytest.mark.integration
@pytest.mark.parametrize("reorder", [reverse, shuffle])
def test_the_rerank_ignores_the_order_pgvector_returns(
    conn: psycopg.Connection, embedder: FakeEmbedder, reorder: Callable[[Rows], Rows]
) -> None:
    for query in QUERIES:
        hybrid = ids(retrieve_context(conn, embedder, query, region="US", k=10))
        assert ids(retrieve_context(proxy(conn, reorder), embedder, query, region="US", k=10)) == (
            hybrid
        )
        dense = ids(dense_search(conn, embedder, query, audience="shopper", k=10, region="US"))
        moved = proxy(conn, reorder)
        assert ids(dense_search(moved, embedder, query, audience="shopper", k=10, region="US")) == (
            dense
        )


def _forced_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval, "RERANK_MARGIN", 10.0)  # no pool is ever safe


@pytest.mark.integration
def test_a_forced_fallback_reranks_every_row_and_agrees(
    conn: psycopg.Connection, embedder: FakeEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    normal = {q: ids(retrieve_context(conn, embedder, q, region="US", k=10)) for q in QUERIES}
    _forced_fallback(monkeypatch)
    for query in QUERIES:
        counted = Reordering(conn, list)
        found = retrieve_context(cast(psycopg.Connection, counted), embedder, query, region="US")
        assert counted.statements == 2  # the pool, then every filtered row
        assert ids(retrieve_context(conn, embedder, query, region="US", k=10)) == normal[query]
        assert len(found) == retrieval.CONTEXT_K


@pytest.mark.integration
def test_a_pool_larger_than_the_filtered_set_is_ranked_whole_in_one_statement(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    counted = Reordering(conn, list)
    query = QUERIES[0]
    found = dense_search(
        cast(psycopg.Connection, counted), embedder, query, audience="shopper", k=5000, region="US"
    )
    assert counted.statements == 1
    [vector] = embedder.embed([query])
    rows = conn.execute(
        "SELECT chunk_id, embedding::real[]::float8[] FROM rag_chunks "
        "WHERE visibility = 'public' AND region && ARRAY['US', 'ALL']"
    ).fetchall()
    exact = sorted(rows, key=lambda r: (retrieval.exact_cosine_distance(vector, r[1]), r[0]))
    assert ids(found) == [r[0] for r in exact]


def _shopper_may_see(conn: psycopg.Connection, chunk_ids: list[str], region: str) -> bool:
    rows = conn.execute(
        "SELECT visibility, region FROM rag_chunks WHERE chunk_id = ANY(%s)", (chunk_ids,)
    ).fetchall()
    return len(rows) == len(chunk_ids) and all(
        vis == "public" and regions is not None and {region, "ALL"} & set(regions)
        for vis, regions in rows
    )


@pytest.mark.integration
@pytest.mark.parametrize("region", ["US", "CA", "UK", "EU"])
def test_the_fallback_keeps_the_shopper_filter(
    conn: psycopg.Connection,
    embedder: FakeEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    region: str,
) -> None:
    _forced_fallback(monkeypatch)
    for query in (*QUERIES, "internal staff refund playbook", "returns policy"):
        found = ids(retrieve_context(conn, embedder, query, region=region, k=40))  # type: ignore[arg-type]
        assert found
        assert _shopper_may_see(conn, found, region)
        dense = dense_search(conn, embedder, query, audience="shopper", k=40, region=region)  # type: ignore[arg-type]
        assert _shopper_may_see(conn, ids(dense), region)


class ZeroEmbedder(FakeEmbedder):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] * self.dim for _ in texts]


def _copy_chunk(conn: psycopg.Connection, source: str, new_ids: Sequence[str]) -> None:
    """Copies of one row under new IDs: same text and vector, so tied in both arms."""
    columns = [
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = "
            "current_schema() AND table_name = 'rag_chunks' AND is_generated = 'NEVER' "
            "ORDER BY ordinal_position"
        ).fetchall()
    ]
    for new in new_ids:
        source_type, source_id, seq = new.split(":")
        values = {"chunk_id": new, "source_type": source_type, "source_id": source_id}
        picks = [
            sql.Literal(values[c])
            if c in values
            else sql.Literal(int(seq))
            if c == "seq"
            else sql.Identifier(c)
            for c in columns
        ]
        conn.execute(
            sql.SQL(
                "INSERT INTO rag_chunks ({}) SELECT {} FROM rag_chunks WHERE chunk_id = {}"
            ).format(
                sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.SQL(", ").join(picks),
                sql.Literal(source),
            )
        )


def _a_us_product_chunk(conn: psycopg.Connection) -> tuple[str, str]:
    row = conn.execute(
        "SELECT chunk_id, text FROM rag_chunks WHERE source_type = 'product' "
        "AND visibility = 'public' AND region && ARRAY['US', 'ALL'] ORDER BY chunk_id LIMIT 1"
    ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


# en_US.utf8 orders these A:0, a:1, a-b:0, ab:0; "C" and Python order them A:0, a-b:0, a:1, ab:0.
TIED = ("product:ab:0", "product:a-b:0", "product:A:0", "product:a:1")


@pytest.mark.integration
def test_tied_rows_come_back_in_code_point_order_from_both_arms(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    source, text = _a_us_product_chunk(conn)
    _copy_chunk(conn, source, TIED)
    tied = {source, *TIED}
    kw = [
        c
        for c in ids(keyword_search(conn, text, audience="shopper", k=50, region="US"))
        if c in tied
    ]
    assert kw == sorted(tied)
    dense = dense_search(conn, embedder, text, audience="shopper", k=10, region="US")
    assert [c for c in ids(dense) if c in tied] == sorted(tied)


@pytest.mark.integration
def test_zero_vectors_in_the_query_or_the_index_never_raise(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    source, _ = _a_us_product_chunk(conn)
    _copy_chunk(conn, source, ["product:zero:0"])
    zero = "[" + ",".join(["0"] * 384) + "]"
    conn.execute(
        "UPDATE rag_chunks SET embedding = %s::vector WHERE chunk_id = 'product:zero:0'", (zero,)
    )
    everything = ids(
        dense_search(conn, embedder, QUERIES[0], audience="shopper", k=5000, region="US")
    )
    assert everything[-1] == "product:zero:0"  # infinitely far, never a crash
    assert (
        len(retrieve_context(conn, ZeroEmbedder(), QUERIES[0], region="US")) == retrieval.CONTEXT_K
    )
