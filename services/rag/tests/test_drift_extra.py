"""RAG-03 drift fix, extra edge cases: boundaries, hard rule 7 on every path, properties.

Complements test_exact_rerank.py and test_frozen_vectors.py; written from the criteria and the
public interfaces (docs/reviews/2026-11-08-RAG-03-drift.md, section 9).
"""

from __future__ import annotations

import json
import math
import random
import re
import struct
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
from psycopg import sql

from northwind_rag import retrieval
from northwind_rag.embedding import (
    CachedEmbedder,
    FrozenVectorError,
    add_frozen,
    cached,
    format_float32,
    frozen_key,
    frozen_path,
    load_frozen,
    to_float32,
)
from northwind_rag.ingest import ingest
from northwind_rag.retrieval import (
    RERANK_FACTOR,
    RERANK_MARGIN,
    _pool_is_safe,
    dense_search,
    exact_cosine_distance,
    keyword_search,
    retrieve_context,
)
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("RAG-03")

Rows = list[tuple[Any, ...]]
QUERIES = ("waterproof jacket with a hood", "machine washable wool throw", "return a sofa")
KEY = "g" * 64


def unit(values: Sequence[float]) -> list[float]:
    norm = math.sqrt(math.fsum(v * v for v in values))
    return [to_float32(v / norm) for v in values]


class UnitFake(FakeEmbedder):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [unit(v) for v in super().embed(texts)]


class Recording(UnitFake):
    def __init__(self, id: str = "test:extra:4", dim: int = 4) -> None:  # noqa: A002
        super().__init__(id, dim)
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return super().embed(texts)


def write_lines(path: Path, lines: Sequence[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{x}\n" for x in lines), encoding="utf-8")
    return path


def entry(key: str, vector: Sequence[float]) -> str:
    return json.dumps({"key": key, "vector": list(vector)})


# ---- exact_cosine_distance (unit) --------------------------------------------------------------


def _rand(rng: random.Random, n: int = 64) -> list[float]:
    return [rng.uniform(-1, 1) for _ in range(n)]


def test_the_distance_of_a_vector_to_itself_is_zero_and_to_its_negation_is_two() -> None:
    rng = random.Random(21)  # noqa: S311 - test data, not security
    for _ in range(50):
        v = _rand(rng)
        assert abs(exact_cosine_distance(v, v)) < 1e-12
        assert abs(exact_cosine_distance(v, [-x for x in v]) - 2.0) < 1e-12


def test_orthogonal_vectors_are_at_distance_one() -> None:
    assert exact_cosine_distance([1.0, 0.0, 0.0], [0.0, 5.0, 0.0]) == 1.0


def test_the_distance_is_symmetric_and_ignores_a_power_of_two_scale_exactly() -> None:
    rng = random.Random(22)  # noqa: S311 - test data, not security
    for _ in range(50):
        q, v = _rand(rng), _rand(rng)
        assert exact_cosine_distance(q, v) == exact_cosine_distance(v, q)
        assert exact_cosine_distance([4 * x for x in q], v) == exact_cosine_distance(q, v)


def test_inputs_are_rounded_to_float32_before_use() -> None:
    q = [0.1, 0.2, 0.3]
    v = [0.7, -0.4, 0.25]
    assert exact_cosine_distance(q, v) == exact_cosine_distance(
        [to_float32(x) for x in q], [to_float32(x) for x in v]
    )


@pytest.mark.parametrize(
    ("q", "v"),
    [([0.0, 0.0], [1.0, 0.0]), ([1.0, 0.0], [0.0, 0.0]), ([0.0, 0.0], [0.0, 0.0])],
)
def test_any_zero_vector_is_infinitely_far(q: list[float], v: list[float]) -> None:
    assert exact_cosine_distance(q, v) == math.inf


def test_a_vector_that_underflows_to_zero_in_float32_is_treated_as_zero() -> None:
    assert exact_cosine_distance([1e-60, 0.0], [1.0, 0.0]) == math.inf


# ---- the pool guard and its constants (unit) ---------------------------------------------------


def test_the_constants_leave_room_above_pgvectors_measured_error() -> None:
    assert RERANK_FACTOR >= 2
    assert RERANK_MARGIN > 10 * 3.7e-7


def test_the_guard_is_safe_exactly_at_the_margin_and_not_a_hair_below() -> None:
    exact = 0.25
    assert _pool_is_safe(exact + RERANK_MARGIN, exact)
    assert not _pool_is_safe(math.nextafter(exact + RERANK_MARGIN, 0.0), exact)
    assert not _pool_is_safe(exact, exact)
    assert not _pool_is_safe(exact - 1.0, exact)


@pytest.mark.parametrize("last", [math.inf, -math.inf, math.nan])
def test_the_guard_never_trusts_a_non_finite_pool_edge(last: float) -> None:
    assert not _pool_is_safe(last, 0.1)


# ---- numbers and keys (unit) -------------------------------------------------------------------


def test_negative_zero_keeps_its_sign_and_subnormals_survive() -> None:
    assert format_float32(-0.0).startswith("-")
    assert math.copysign(1, to_float32(float(format_float32(-0.0)))) == -1
    for v in (to_float32(1e-45), to_float32(1.1754942e-38), -to_float32(1e30)):
        assert to_float32(float(format_float32(v))) == v


def test_shortest_form_is_used_for_simple_values() -> None:
    assert format_float32(0.5) == "0.5"
    assert format_float32(1.0) == "1"
    assert len(format_float32(to_float32(0.1))) <= 5


def test_random_bit_patterns_round_trip() -> None:
    rng = random.Random(23)  # noqa: S311 - test data, not security
    for _ in range(5000):
        bits = rng.getrandbits(32)
        v = cast(float, struct.unpack("<f", struct.pack("<I", bits))[0])
        if not math.isfinite(v):
            continue
        assert to_float32(float(format_float32(v))) == v


def test_keys_are_64_letters_a_to_p_for_any_text() -> None:
    for text in ("", "x", "naïve café 日本語 🧥", "0123456789", "a" * 10_000, "line\nbreak"):
        assert re.fullmatch(r"[a-p]{64}", frozen_key(text))
    assert frozen_key("a") != frozen_key("b")
    assert frozen_key("a") == frozen_key("a")


def test_the_path_maps_slashes_and_colons_and_honours_the_directory(tmp_path: Path) -> None:
    path = frozen_path("fastembed:org/model-x:384", tmp_path)
    assert path == tmp_path / "fastembed-org-model-x-384.jsonl"


# ---- load_frozen (unit) ------------------------------------------------------------------------

GOOD = unit([1.0, 2.0, 3.0, 4.0])


def test_an_empty_file_loads_as_no_entries(tmp_path: Path) -> None:
    path = tmp_path / "e.jsonl"
    path.write_text("", encoding="utf-8")
    assert load_frozen(path, 4) == {}


def test_a_good_file_loads_every_entry_as_exact_float32(tmp_path: Path) -> None:
    other = unit([4.0, 1.0, 0.0, 2.0])
    path = write_lines(tmp_path / "f.jsonl", [entry("g" * 64, GOOD), entry("h" * 64, other)])
    loaded = load_frozen(path, 4)
    assert list(loaded) == ["g" * 64, "h" * 64]
    assert list(loaded["g" * 64]) == GOOD


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "[]",
        "null",
        '"just a string"',
        '{"key": "' + "G" * 64 + '", "vector": [1, 0, 0, 0]}',
        '{"key": "' + "q" * 64 + '", "vector": [1, 0, 0, 0]}',
        '{"key": "' + "g" * 65 + '", "vector": [1, 0, 0, 0]}',
        '{"key": 5, "vector": [1, 0, 0, 0]}',
        '{"key": "' + "g" * 64 + '"}',
        '{"vector": [1, 0, 0, 0]}',
        '{"key": "' + "g" * 64 + '", "vector": null}',
        '{"key": "' + "g" * 64 + '", "vector": {"a": 1}}',
        '{"key": "' + "g" * 64 + '", "vector": [1, 0, 0, 0, 0]}',
        '{"key": "' + "g" * 64 + '", "vector": []}',
        '{"key": "' + "g" * 64 + '", "vector": [Infinity, 0, 0, 0]}',
        '{"key": "' + "g" * 64 + '", "vector": [-Infinity, 0, 0, 0]}',
        '{"key": "' + "g" * 64 + '", "vector": [null, 0, 0, 1]}',
        '{"key": "' + "g" * 64 + '", "vector": [0, 0, 0, 0]}',
        '{"key": "' + "g" * 64 + '", "vector": [1e39, 0, 0, 0]}',
    ],
)
def test_load_fails_closed_on_malformed_lines(tmp_path: Path, text: str) -> None:
    path = write_lines(tmp_path / "bad.jsonl", [text])
    with pytest.raises(FrozenVectorError):
        load_frozen(path, 4)


def test_one_bad_line_among_good_ones_refuses_the_whole_file(tmp_path: Path) -> None:
    path = write_lines(
        tmp_path / "mixed.jsonl", [entry("g" * 64, GOOD), "{oops", entry("h" * 64, GOOD)]
    )
    with pytest.raises(FrozenVectorError):
        load_frozen(path, 4)


def test_the_norm_tolerance_accepts_float32_noise_and_refuses_real_error(tmp_path: Path) -> None:
    near = [1.0 + 1e-5, 0.0, 0.0, 0.0]
    far = [1.01, 0.0, 0.0, 0.0]
    load_frozen(write_lines(tmp_path / "near.jsonl", [entry(KEY, near)]), 4)
    with pytest.raises(FrozenVectorError):
        load_frozen(write_lines(tmp_path / "far.jsonl", [entry(KEY, far)]), 4)


def test_a_dim_mismatch_refuses_the_file(tmp_path: Path) -> None:
    path = write_lines(tmp_path / "d.jsonl", [entry(KEY, GOOD)])
    with pytest.raises(FrozenVectorError):
        load_frozen(path, 3)


def test_an_edited_file_is_reread(tmp_path: Path) -> None:
    path = write_lines(tmp_path / "r.jsonl", [entry("g" * 64, GOOD)])
    assert set(load_frozen(path, 4)) == {"g" * 64}
    write_lines(path, [entry("g" * 64, GOOD), entry("h" * 64, GOOD)])
    assert set(load_frozen(path, 4)) == {"g" * 64, "h" * 64}


# ---- add_frozen (unit) -------------------------------------------------------------------------


def test_add_creates_the_file_and_directory_sorted_by_key(tmp_path: Path) -> None:
    path = tmp_path / "new" / "dir" / "m.jsonl"
    entries = {"i" * 64: GOOD, "g" * 64: GOOD, "h" * 64: GOOD}
    assert add_frozen(path, entries, 4) == 3
    keys = [json.loads(x)["key"] for x in path.read_text(encoding="utf-8").splitlines()]
    assert keys == ["g" * 64, "h" * 64, "i" * 64]
    assert not list(path.parent.glob("*.tmp"))


def test_adding_nothing_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / "none.jsonl"
    assert add_frozen(path, {}, 4) == 0
    assert not path.exists()


def test_adding_the_same_vector_again_is_a_no_op_that_leaves_bytes_alone(tmp_path: Path) -> None:
    path = tmp_path / "same.jsonl"
    add_frozen(path, {KEY: GOOD}, 4)
    before = path.read_bytes()
    assert add_frozen(path, {KEY: GOOD}, 4) == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "key", ["", "g" * 63, "g" * 65, "G" * 64, "q" * 64, "g" * 63 + "1", "../" + "g" * 61]
)
def test_add_refuses_a_malformed_key(tmp_path: Path, key: str) -> None:
    path = tmp_path / "k.jsonl"
    with pytest.raises(FrozenVectorError):
        add_frozen(path, {key: GOOD}, 4)
    assert not path.exists()


@pytest.mark.parametrize(
    "vector", [[1.0, 0.0, 0.0], [2.0, 0.0, 0.0, 0.0], [0.0] * 4, [math.nan, 0.0, 0.0, 1.0]]
)
def test_add_refuses_a_bad_vector_and_leaves_the_file_untouched(
    tmp_path: Path, vector: list[float]
) -> None:
    path = tmp_path / "v.jsonl"
    add_frozen(path, {KEY: GOOD}, 4)
    before = path.read_bytes()
    with pytest.raises(FrozenVectorError):
        add_frozen(path, {"h" * 64: GOOD, "i" * 64: vector}, 4)
    assert path.read_bytes() == before  # the good one beside the bad one was not written either
    assert not list(tmp_path.glob("*.tmp"))


def test_a_changed_vector_for_an_existing_key_is_refused_even_by_one_ulp(tmp_path: Path) -> None:
    path = tmp_path / "u.jsonl"
    add_frozen(path, {KEY: GOOD}, 4)
    before = path.read_bytes()
    nudged = [math.nextafter(GOOD[0], 2.0), *GOOD[1:]]
    nudged = [to_float32(x) for x in nudged]
    if nudged == GOOD:  # float32 spacing is wider than a float64 ulp
        nudged[0] = to_float32(GOOD[0] * (1 + 2**-22))
    assert nudged != GOOD
    with pytest.raises(FrozenVectorError):
        add_frozen(path, {KEY: nudged}, 4)
    assert path.read_bytes() == before


def test_adding_in_pieces_equals_adding_at_once(tmp_path: Path) -> None:
    rng = random.Random(24)  # noqa: S311 - test data, not security
    keys = sorted({"".join(rng.choice("abcdefghijklmnop") for _ in range(64)) for _ in range(30)})
    entries = {k: unit(_rand(rng, 4)) for k in keys}
    once = tmp_path / "once.jsonl"
    add_frozen(once, entries, 4)
    pieces = tmp_path / "pieces.jsonl"
    shuffled = list(entries)
    rng.shuffle(shuffled)
    for chunk in (shuffled[:7], shuffled[7:19], shuffled[19:]):
        add_frozen(pieces, {k: entries[k] for k in chunk}, 4)
    assert pieces.read_bytes() == once.read_bytes()


# ---- CachedEmbedder (unit) ---------------------------------------------------------------------


def test_an_empty_request_returns_nothing_and_calls_nothing(tmp_path: Path) -> None:
    inner = Recording()
    write_lines(frozen_path(inner.id, tmp_path), [entry(frozen_key("known"), GOOD)])
    assert CachedEmbedder(inner, tmp_path).embed([]) == []
    assert inner.calls == []


def test_all_hits_never_call_the_model_and_keep_the_order(tmp_path: Path) -> None:
    inner = Recording()
    other = unit([4.0, 3.0, 2.0, 1.0])
    write_lines(
        frozen_path(inner.id, tmp_path),
        sorted([entry(frozen_key("a"), GOOD), entry(frozen_key("b"), other)]),
    )
    wrapped = CachedEmbedder(inner, tmp_path)
    assert wrapped.embed(["b", "a", "b"]) == [other, GOOD, other]
    assert inner.calls == []
    assert (wrapped.hits, wrapped.misses) == (3, 0)


def test_misses_interleaved_with_hits_land_in_their_own_positions(tmp_path: Path) -> None:
    inner = Recording()
    write_lines(frozen_path(inner.id, tmp_path), [entry(frozen_key("hit"), GOOD)])
    texts = ["m1", "hit", "m2", "m2", "hit", "m3"]
    out = CachedEmbedder(inner, tmp_path).embed(texts)
    assert inner.calls == [["m1", "m2", "m2", "m3"]]
    reference = UnitFake(inner.id, 4)
    for text, vector in zip(texts, out, strict=True):
        assert vector == (GOOD if text == "hit" else reference.embed([text])[0])


def test_with_no_file_the_call_is_one_straight_pass_through(tmp_path: Path) -> None:
    inner = Recording()
    wrapped = CachedEmbedder(inner, tmp_path)  # tmp_path holds no file for this id
    assert wrapped.embed(["x", "y"]) == UnitFake(inner.id, 4).embed(["x", "y"])
    assert inner.calls == [["x", "y"]]
    assert list(tmp_path.iterdir()) == []


def test_mutating_a_returned_vector_does_not_change_the_next_answer(tmp_path: Path) -> None:
    inner = Recording()
    write_lines(frozen_path(inner.id, tmp_path), [entry(frozen_key("k"), GOOD)])
    wrapped = CachedEmbedder(inner, tmp_path)
    [first] = wrapped.embed(["k"])
    first[0] = 99.0
    assert wrapped.embed(["k"]) == [GOOD]


def test_the_id_and_dim_are_the_inners_and_wrapping_a_wrapper_is_the_same_object(
    tmp_path: Path,
) -> None:
    inner = Recording("test:extra:4", 4)
    wrapped = CachedEmbedder(inner, tmp_path)
    assert (wrapped.id, wrapped.dim) == ("test:extra:4", 4)
    assert cached(wrapped) is wrapped
    assert cached(inner).id == inner.id


# ---- re-ranking against a real index (integration) ---------------------------------------------


@pytest.fixture
def embedder(conn: psycopg.Connection, fixtures_dir: Path) -> FakeEmbedder:
    fake = FakeEmbedder()
    ingest(conn, fixtures_dir, fake)
    return fake


def ids(chunks: Sequence[Any]) -> list[str]:
    return [c.chunk_id for c in chunks]


class _Cursor:
    def __init__(self, cursor: psycopg.Cursor[Any], owner: Shuffling) -> None:
        self._cursor, self._owner = cursor, owner

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        self._cursor.close()

    def execute(self, query: Any, params: Any = None) -> _Cursor:
        self._cursor.execute(query, params)
        return self

    def fetchall(self) -> Rows:
        rows = list(self._cursor.fetchall())
        dense = [r for r in rows if r[0] == "dense"]
        if dense:
            self._owner.rng.shuffle(dense)
            return dense + [r for r in rows if r[0] != "dense"]
        self._owner.rng.shuffle(rows)
        return rows


class Shuffling:
    """A connection that returns every dense row set in a fresh random order."""

    def __init__(self, conn: psycopg.Connection, seed: int) -> None:
        self._conn = conn
        self.rng = random.Random(seed)  # noqa: S311 - test data, not security

    def cursor(self) -> _Cursor:
        return _Cursor(self._conn.cursor(), self)

    def execute(self, query: Any, params: Any = None) -> Any:
        return self._conn.execute(query, params)


def shuffled(conn: psycopg.Connection, seed: int) -> psycopg.Connection:
    return cast(psycopg.Connection, Shuffling(conn, seed))


def _exact_order(
    conn: psycopg.Connection, vector: list[float], region: str, audience: str = "shopper"
) -> list[str]:
    visible = ["public"] if audience == "shopper" else ["public", "internal"]
    rows = conn.execute(
        "SELECT chunk_id, embedding::real[]::float8[] FROM rag_chunks "
        "WHERE visibility = ANY(%s) AND region && ARRAY[%s, 'ALL']",
        (visible, region),
    ).fetchall()
    ranked = sorted(rows, key=lambda r: (exact_cosine_distance(vector, r[1]), r[0]))
    return [r[0] for r in ranked]


@pytest.mark.integration
@pytest.mark.parametrize("k", [1, 2, 3, 7, 25])
def test_dense_top_k_is_the_exact_order_of_the_stored_vectors(
    conn: psycopg.Connection, embedder: FakeEmbedder, k: int
) -> None:
    for query in QUERIES:
        [vector] = embedder.embed([query])
        found = dense_search(conn, embedder, query, audience="shopper", k=k, region="UK")
        assert ids(found) == _exact_order(conn, vector, "UK")[:k]


@pytest.mark.integration
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("k", [1, 5])
def test_every_random_row_order_gives_the_same_answer(
    conn: psycopg.Connection, embedder: FakeEmbedder, seed: int, k: int
) -> None:
    for query in QUERIES:
        for region in ("US", "EU"):
            kwargs: dict[str, Any] = {"region": region, "k": k}
            plain = retrieve_context(conn, embedder, query, **kwargs)
            moved = retrieve_context(shuffled(conn, seed), embedder, query, **kwargs)
            assert ids(moved) == ids(plain)
            dplain = dense_search(conn, embedder, query, audience="shopper", **kwargs)
            dmoved = dense_search(
                shuffled(conn, seed), embedder, query, audience="shopper", **kwargs
            )
            assert ids(dmoved) == ids(dplain)


@pytest.mark.integration
def test_results_are_unique_and_never_more_than_k(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    for k in (1, 4, 9):
        for query in QUERIES:
            for found in (
                retrieve_context(conn, embedder, query, region="CA", k=k),
                dense_search(conn, embedder, query, audience="shopper", k=k, region="CA"),
                keyword_search(conn, query, audience="shopper", k=k, region="CA"),
            ):
                assert len(found) <= k
                assert len(set(ids(found))) == len(found)


@pytest.mark.integration
def test_bad_arguments_still_raise_the_documented_errors(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    with pytest.raises(ValueError):
        retrieve_context(conn, embedder, "x", region="US", k=0)
    with pytest.raises(ValueError):
        retrieve_context(conn, embedder, "x", region=None)  # a shopper must name a region
    with pytest.raises(ValueError):
        retrieve_context(conn, embedder, "x", region="MARS")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        dense_search(conn, embedder, "x", audience="admin", k=3)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        dense_search(conn, embedder, "x", audience="shopper", k=0)


# ---- hard rule 7 on every path -----------------------------------------------------------------


def _forced_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval, "RERANK_MARGIN", 10.0)


def _violations(conn: psycopg.Connection, chunk_ids: Sequence[str], region: str) -> list[str]:
    rows = conn.execute(
        "SELECT chunk_id, visibility, region FROM rag_chunks WHERE chunk_id = ANY(%s)",
        (list(chunk_ids),),
    ).fetchall()
    seen = {r[0] for r in rows}
    bad = [c for c in chunk_ids if c not in seen]
    bad += [r[0] for r in rows if r[1] != "public" or not r[2] or not ({region, "ALL"} & set(r[2]))]
    return bad


def _paths(
    conn: psycopg.Connection, embedder: FakeEmbedder, query: str, region: str, k: int
) -> list[list[str]]:
    kw: dict[str, Any] = {"region": region, "k": k}
    return [
        ids(retrieve_context(conn, embedder, query, **kw)),
        ids(retrieve_context(shuffled(conn, 9), embedder, query, **kw)),
        ids(dense_search(conn, embedder, query, audience="shopper", **kw)),
        ids(dense_search(shuffled(conn, 9), embedder, query, audience="shopper", **kw)),
        ids(keyword_search(conn, query, audience="shopper", **kw)),
    ]


@pytest.mark.integration
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("region", ["US", "CA", "UK", "EU"])
def test_a_shopper_never_sees_an_internal_or_foreign_region_chunk(
    conn: psycopg.Connection,
    embedder: FakeEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    fallback: bool,
    region: str,
) -> None:
    if fallback:
        _forced_fallback(monkeypatch)
    queries = (QUERIES[0], "internal only staff playbook", "escalation refund override")
    for query in queries:
        for k in (5, 60):
            for found in _paths(conn, embedder, query, region, k):
                assert _violations(conn, found, region) == []


@pytest.mark.integration
@pytest.mark.parametrize("fallback", [False, True])
def test_a_planted_internal_or_foreign_chunk_that_matches_perfectly_is_never_returned(
    conn: psycopg.Connection,
    embedder: FakeEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    fallback: bool,
) -> None:
    """Planted rows copy a visible chunk, so they tie with it at the top of every ranking."""
    if fallback:
        _forced_fallback(monkeypatch)
    source, text = _a_us_product_chunk(conn)
    _copy_chunk(conn, source, ["product:aaa-internal:0", "product:aaa-eu-only:0"])
    conn.execute(
        "UPDATE rag_chunks SET visibility = 'internal' WHERE chunk_id = 'product:aaa-internal:0'"
    )
    conn.execute(
        "UPDATE rag_chunks SET region = ARRAY['EU'] WHERE chunk_id = 'product:aaa-eu-only:0'"
    )
    for found in _paths(conn, embedder, text, "US", 50):
        assert "product:aaa-internal:0" not in found
        assert "product:aaa-eu-only:0" not in found
    eu = ids(retrieve_context(conn, embedder, text, region="EU", k=50))
    assert "product:aaa-eu-only:0" in eu  # the region's own chunk is still found there
    assert "product:aaa-internal:0" not in eu


@pytest.mark.integration
def test_a_staff_caller_with_no_region_can_see_internal_chunks_on_the_dense_path(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    source, text = _a_us_product_chunk(conn)
    _copy_chunk(conn, source, ["product:aaa-internal:0"])
    conn.execute(
        "UPDATE rag_chunks SET visibility = 'internal' WHERE chunk_id = 'product:aaa-internal:0'"
    )
    staff = dense_search(conn, embedder, text, audience="staff", k=50)
    shopper = dense_search(conn, embedder, text, audience="shopper", k=50)
    assert "product:aaa-internal:0" in ids(staff)
    assert "product:aaa-internal:0" not in ids(shopper)


@pytest.mark.integration
@pytest.mark.parametrize("fallback", [False, True])
def test_a_zero_vector_row_that_a_shopper_may_not_see_never_surfaces(
    conn: psycopg.Connection,
    embedder: FakeEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    fallback: bool,
) -> None:
    if fallback:
        _forced_fallback(monkeypatch)
    source, _ = _a_us_product_chunk(conn)
    _copy_chunk(conn, source, ["product:zero-hidden:0"])
    conn.execute(
        "UPDATE rag_chunks SET visibility = 'internal', embedding = %s::vector "
        "WHERE chunk_id = 'product:zero-hidden:0'",
        ("[" + ",".join(["0"] * 384) + "]",),
    )
    for query in QUERIES:
        big = ids(dense_search(conn, embedder, query, audience="shopper", k=5000, region="US"))
        assert "product:zero-hidden:0" not in big
        assert _violations(conn, big, "US") == []


@pytest.mark.integration
@pytest.mark.parametrize("fallback", [False, True])
def test_a_visible_zero_vector_row_changes_nothing_but_the_tail(
    conn: psycopg.Connection,
    embedder: FakeEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    fallback: bool,
) -> None:
    before = {
        q: ids(dense_search(conn, embedder, q, audience="shopper", k=8, region="US"))
        for q in QUERIES
    }
    if fallback:
        _forced_fallback(monkeypatch)
    source, _ = _a_us_product_chunk(conn)
    _copy_chunk(conn, source, ["product:zero-visible:0"])
    conn.execute(
        "UPDATE rag_chunks SET embedding = %s::vector WHERE chunk_id = 'product:zero-visible:0'",
        ("[" + ",".join(["0"] * 384) + "]",),
    )
    for query in QUERIES:
        after = dense_search(conn, embedder, query, audience="shopper", k=8, region="US")
        assert ids(after) == before[query]


@pytest.mark.integration
def test_tied_copies_fill_the_top_k_in_code_point_order(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    source, text = _a_us_product_chunk(conn)
    names = ("product:b:0", "product:B:0", "product:a:0", "product:_:0", "product:é:0")
    _copy_chunk(conn, source, names)
    tied = sorted({source, *names})
    [vector] = embedder.embed([text])
    exact = _exact_order(conn, vector, "US")
    assert exact[: len(tied)] == tied or set(exact[: len(tied)]) != set(tied)
    for k in (2, 3, 4):
        found = ids(dense_search(conn, embedder, text, audience="shopper", k=k, region="US"))
        assert found == exact[:k]
        assert found == [c for c in exact if c in set(found)]


# ---- helpers copied from the sibling tests (they live in another test module) ------------------


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
        values = {"chunk_id": new, "source_type": new.split(":")[0], "source_id": new.split(":")[1]}
        picks = [sql.Literal(values[c]) if c in values else sql.Identifier(c) for c in columns]
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
