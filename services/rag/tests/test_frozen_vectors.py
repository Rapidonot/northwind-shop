"""Frozen vectors: every platform embeds the fixtures and eval queries identically (RAG-03).

The same text embedded on arm64 and x86_64, or in a different batch, gives vectors that differ by
up to about 4e-4 per component, enough to flip near-tied dense ranks
(docs/reviews/2026-11-08-RAG-03-drift.md). `data/embeddings/` holds the vectors that produced every
saved context, keyed by a digit-free digest of the text. `CachedEmbedder` serves them and falls back
to the model for any other text.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from pathlib import Path

import psycopg
import pytest

from northwind_rag import embedding, store
from northwind_rag.embedding import (
    CachedEmbedder,
    FastEmbedEmbedder,
    FrozenVectorError,
    add_frozen,
    cached,
    check_frozen,
    export_chunk_vectors,
    format_float32,
    frozen_key,
    frozen_path,
    load_frozen,
    to_float32,
)
from northwind_rag.ingest import ingest
from northwind_rag.retrieval import retriever_id
from northwind_rag.sources import build_chunks
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("RAG-03")

REPO = Path(__file__).resolve().parents[3]
REAL = frozen_path(FastEmbedEmbedder().id)


def unit(values: Sequence[float]) -> list[float]:
    norm = math.sqrt(math.fsum(v * v for v in values))
    return [to_float32(v / norm) for v in values]


class UnitFake(FakeEmbedder):
    """FakeEmbedder with unit-norm float32 vectors, the shape the frozen file accepts."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [unit(v) for v in super().embed(texts)]


class Counting(UnitFake):
    def __init__(self, id: str = "test:unit:4", dim: int = 4) -> None:  # noqa: A002
        super().__init__(id, dim)
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return super().embed(texts)


# ---- keys and numbers (unit) ------------------------------------------------------------------


def test_keys_are_the_text_digest_with_no_digits() -> None:
    digest = hashlib.sha256(b"a wool throw").hexdigest()
    assert frozen_key("a wool throw") == digest.translate(str.maketrans("0123456789", "ghijklmnop"))
    assert not any(ch.isdigit() for ch in frozen_key("0123456789" * 9))


def test_the_enrichment_cache_shares_the_digit_free_mapping() -> None:
    from northwind_rag import enrichment

    assert vars(enrichment)["digit_free"] is embedding.digit_free


def test_the_file_is_named_from_the_embedder_id() -> None:
    assert REAL.name == "fastembed-BAAI-bge-small-en-v1.5-384.jsonl"
    assert REAL.parent == REPO / "data" / "embeddings"


def test_every_float32_reads_back_bit_exact_from_its_written_form() -> None:
    import random

    rng = random.Random(5)  # noqa: S311 - test data, not security
    values = [to_float32(rng.uniform(-1, 1) * 10 ** rng.randint(-12, 0)) for _ in range(20000)]
    for v in [*values, 0.0, -0.0, to_float32(1e-45), to_float32(0.1)]:
        text = format_float32(v)
        assert to_float32(float(text)) == v
        assert not re.search(r"\d{13,}", text)  # never a run the PAN check could read
        json.loads(text)  # a valid JSON number


def test_a_value_that_cannot_be_written_bit_exact_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embedding, "_MAX_DIGITS", 3)
    with pytest.raises(FrozenVectorError):
        format_float32(to_float32(0.123456789))


# ---- loading (unit) ---------------------------------------------------------------------------


def write_file(directory: Path, embedder_id: str, lines: Sequence[str]) -> Path:
    path = frozen_path(embedder_id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def line(key: str, vector: Sequence[float]) -> str:
    return json.dumps({"key": key, "vector": list(vector)})


GOOD = unit([1.0, 2.0, 3.0, 4.0])


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("not JSON", "{"),
        ("a digit in the key", line("g" * 63 + "1", GOOD)),
        ("a short key", line("g" * 10, GOOD)),
        ("wrong length", line("g" * 64, GOOD[:3])),
        ("NaN", '{"key": "' + "g" * 64 + '", "vector": [NaN, 0, 0, 1]}'),
        ("a string", '{"key": "' + "g" * 64 + '", "vector": ["1", 0, 0, 0]}'),
        ("a bool", '{"key": "' + "g" * 64 + '", "vector": [true, 0, 0, 0]}'),
        ("not unit norm", line("g" * 64, [2.0, 0.0, 0.0, 0.0])),
        ("an extra field", '{"key": "' + "g" * 64 + '", "vector": [1, 0, 0, 0], "x": 1}'),
    ],
)
def test_load_refuses_each_bad_form(tmp_path: Path, label: str, text: str) -> None:
    path = write_file(tmp_path, "test:unit:4", [text])
    with pytest.raises(FrozenVectorError):
        load_frozen(path, 4)


def test_load_refuses_a_duplicate_key(tmp_path: Path) -> None:
    path = write_file(tmp_path, "test:unit:4", [line("g" * 64, GOOD), line("g" * 64, GOOD)])
    with pytest.raises(FrozenVectorError):
        load_frozen(path, 4)


# ---- CachedEmbedder (unit) --------------------------------------------------------------------


def test_the_cached_embedder_keeps_the_inner_id_so_nothing_goes_stale() -> None:
    inner = FastEmbedEmbedder()
    wrapped = CachedEmbedder(inner)
    assert (wrapped.id, wrapped.dim) == (inner.id, inner.dim)
    baseline = json.loads((REPO / "evals/baselines/dense-only.json").read_text(encoding="utf-8"))
    assert retriever_id(wrapped) == retriever_id(inner) == baseline["retriever_id"]


def test_hits_come_from_the_file_and_misses_go_to_the_model_in_one_call(tmp_path: Path) -> None:
    inner = Counting()
    frozen = unit([4.0, 3.0, 2.0, 1.0])
    write_file(tmp_path, inner.id, [line(frozen_key("known"), frozen)])
    wrapped = CachedEmbedder(inner, tmp_path)
    out = wrapped.embed(["a", "known", "b"])
    assert out[1] == frozen
    assert out[0] == UnitFake(inner.id, 4).embed(["a"])[0]
    assert inner.calls == [["a", "b"]]
    assert (wrapped.hits, wrapped.misses) == (1, 2)


def test_another_embedder_passes_through_and_reads_no_file(tmp_path: Path) -> None:
    path = write_file(tmp_path, "test:unit:4", ["{ not even json"])
    other = Counting("test:other:4")
    assert CachedEmbedder(other, tmp_path).embed(["x"]) == UnitFake("test:other:4", 4).embed(["x"])
    assert path.exists()  # present, but only for test:unit:4, and never parsed


def test_wrapping_is_idempotent() -> None:
    once = cached(FastEmbedEmbedder())
    assert cached(once) is once


# ---- the committed file (unit) ----------------------------------------------------------------


def _eval_queries() -> list[str]:
    path = REPO / "evals/fixtures/starter_set.jsonl"
    return [json.loads(x)["query"] for x in path.read_text(encoding="utf-8").splitlines()]


def _smoke_questions() -> list[str]:
    portal = json.loads((REPO / "apps/portal/e2e/questions.json").read_text(encoding="utf-8"))
    path = REPO / "services/assistant/tests/contradictions.json"
    return [
        *portal.values(),
        *(row["question"] for row in json.loads(path.read_text(encoding="utf-8"))),
    ]


def test_the_committed_file_covers_every_chunk_eval_query_and_smoke_question() -> None:
    frozen = load_frozen(REAL, 384)
    chunk_keys = {frozen_key(c.embed_text) for c in build_chunks(REPO / "data" / "fixtures")}
    query_keys = {frozen_key(q) for q in [*_eval_queries(), *_smoke_questions()]}
    assert chunk_keys <= set(frozen), "run `make embed-cache`"
    assert query_keys <= set(frozen), "run `make embed-cache`"
    assert not chunk_keys & query_keys  # a query never takes a batch-made chunk vector
    assert set(frozen) == chunk_keys | query_keys  # nothing else rides along


def test_the_committed_file_is_canonical() -> None:
    lines = REAL.read_text(encoding="utf-8").splitlines()
    keys = [json.loads(x)["key"] for x in lines]
    assert keys == sorted(set(keys))


# ---- ingest, export and check (integration) ---------------------------------------------------


@pytest.mark.integration
def test_ingest_through_the_cache_writes_exactly_the_frozen_vectors(
    conn: psycopg.Connection, fixtures_dir: Path
) -> None:
    wrapped = CachedEmbedder(FastEmbedEmbedder())
    ingest(conn, fixtures_dir, wrapped)
    assert wrapped.misses == 0
    frozen = load_frozen(REAL, 384)
    rows = conn.execute("SELECT text, embedding::real[]::float8[] FROM rag_chunks").fetchall()
    assert len(rows) == len(build_chunks(fixtures_dir))
    assert all(list(frozen[frozen_key(text)]) == vector for text, vector in rows)


@pytest.fixture
def fake_index(conn: psycopg.Connection, fixtures_dir: Path) -> UnitFake:
    fake = UnitFake("test:index:384", 384)
    ingest(conn, fixtures_dir, fake)
    return fake


@pytest.mark.integration
def test_export_and_add_round_trip_and_check_passes(
    conn: psycopg.Connection, fake_index: UnitFake, fixtures_dir: Path, tmp_path: Path
) -> None:
    chunks = build_chunks(fixtures_dir)[:40]
    path = frozen_path(fake_index.id, tmp_path)
    vectors = export_chunk_vectors(conn, chunks, fake_index.id)
    assert add_frozen(path, vectors, 384) == len({frozen_key(c.embed_text) for c in chunks})
    assert check_frozen(conn, chunks, path, fake_index.id, 384) == []
    first = path.read_text(encoding="utf-8")
    assert add_frozen(path, vectors, 384) == 0  # nothing new
    assert path.read_text(encoding="utf-8") == first


@pytest.mark.integration
def test_adding_keeps_every_existing_line_byte_identical(
    conn: psycopg.Connection, fake_index: UnitFake, fixtures_dir: Path, tmp_path: Path
) -> None:
    chunks = build_chunks(fixtures_dir)[:60]
    path = frozen_path(fake_index.id, tmp_path)
    add_frozen(path, export_chunk_vectors(conn, chunks[:30], fake_index.id), 384)
    before = set(path.read_text(encoding="utf-8").splitlines())
    add_frozen(path, export_chunk_vectors(conn, chunks, fake_index.id), 384)
    after = path.read_text(encoding="utf-8").splitlines()
    assert before <= set(after)
    assert after == sorted(after)


@pytest.mark.integration
def test_an_existing_key_is_never_rewritten(
    conn: psycopg.Connection, fake_index: UnitFake, fixtures_dir: Path, tmp_path: Path
) -> None:
    chunks = build_chunks(fixtures_dir)[:5]
    path = frozen_path(fake_index.id, tmp_path)
    vectors = export_chunk_vectors(conn, chunks, fake_index.id)
    add_frozen(path, vectors, 384)
    key = next(iter(vectors))
    moved = {key: unit([*vectors[key][:-1], vectors[key][-1] + 0.01])}
    with pytest.raises(FrozenVectorError):
        add_frozen(path, moved, 384)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("label", "change"),
    [
        ("text", "UPDATE rag_chunks SET text = text || ' changed' WHERE chunk_id = %s"),
        (
            "context",
            "UPDATE rag_chunks SET context = 'a sentence', embed_hash = 'h' WHERE chunk_id = %s",
        ),
        ("embedder", "UPDATE rag_chunks SET embedder_id = 'other:model:384' WHERE chunk_id = %s"),
        ("missing", "DELETE FROM rag_chunks WHERE chunk_id = %s"),
    ],
)
def test_export_refuses_a_row_that_is_not_this_chunk_from_this_embedder(
    conn: psycopg.Connection, fake_index: UnitFake, fixtures_dir: Path, label: str, change: str
) -> None:
    chunks = build_chunks(fixtures_dir)[:3]
    conn.execute(change, (chunks[0].chunk_id,))
    with pytest.raises(FrozenVectorError):
        export_chunk_vectors(conn, chunks, fake_index.id)


@pytest.mark.integration
def test_check_fails_on_one_changed_component(
    conn: psycopg.Connection, fake_index: UnitFake, fixtures_dir: Path, tmp_path: Path
) -> None:
    chunks = build_chunks(fixtures_dir)[:10]
    path = frozen_path(fake_index.id, tmp_path)
    add_frozen(path, export_chunk_vectors(conn, chunks, fake_index.id), 384)
    conn.execute(
        "UPDATE rag_chunks SET embedding = ((embedding::real[])[1:383] "
        "|| ((embedding::real[])[384] + 1e-6::real))::vector WHERE chunk_id = %s",
        (chunks[0].chunk_id,),
    )
    problems = check_frozen(conn, chunks, path, fake_index.id, 384)
    assert len(problems) == 1 and chunks[0].chunk_id in problems[0]


# ---- the model vouches for every vector (security review, finding 1) --------------------------


def test_the_model_vouches_for_an_honest_vector_within_noise() -> None:
    from northwind_rag.embedding import unvouched

    inner = UnitFake("test:unit:8", 8)
    [honest] = inner.embed(["a wool throw"])
    nudged = unit([x + 1e-6 for x in honest])  # far more than batch noise moves a component
    texts = {frozen_key("a wool throw"): "a wool throw"}
    assert unvouched({frozen_key("a wool throw"): nudged}, texts, inner) == []


def test_a_vector_from_another_text_is_not_vouched_for() -> None:
    from northwind_rag.embedding import unvouched

    inner = UnitFake("test:unit:8", 8)
    [elsewhere] = inner.embed(["every shopper question at once"])
    texts = {frozen_key("a wool throw"): "a wool throw"}
    [problem] = unvouched({frozen_key("a wool throw"): elsewhere}, texts, inner)
    assert "from the model's" in problem


def test_a_key_with_no_known_text_is_not_vouched_for() -> None:
    from northwind_rag.embedding import unvouched

    inner = Counting("test:unit:8", 8)
    [problem] = unvouched({"j" * 64: unit([1.0] * 8)}, {}, inner)
    assert "no fixture, eval or smoke text" in problem
    assert inner.calls == []  # nothing to embed


def test_the_export_query_never_selects_context() -> None:
    """Low 3 of the security review: context may appear only as the `IS NULL` predicate."""
    selected, _, where = store._PLAIN_ROWS.partition("FROM")
    assert re.findall(r"\b\w+\b", selected.replace("SELECT", "")) == [
        *("chunk_id", "text", "embedder_id"),
        *("embedding", "real", "float8"),  # embedding::real[]::float8[]
    ]
    assert re.findall(r"\bcontext\b[^\n]*", where) == ["context IS NULL"]


@pytest.mark.parametrize("digits", [400, 5000])
def test_a_huge_integer_component_fails_closed(tmp_path: Path, digits: int) -> None:
    """Re-review N4: beyond float64, or too long for `json`, is still a FrozenVectorError."""
    text = '{"key": "' + "g" * 64 + '", "vector": [' + "9" * digits + ", 0, 0, 0]}"
    path = write_file(tmp_path, "test:unit:4", [text])
    with pytest.raises(FrozenVectorError):
        load_frozen(path, 4)


def test_the_vouch_tolerance_is_the_reviewed_one() -> None:
    """Pinned: never raise it to pass (row review, low 2; open item N1 says how to tighten it)."""
    assert embedding.VOUCH_TOLERANCE == 1e-4
