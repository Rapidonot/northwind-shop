"""The eval path cannot drift across platforms (RAG-03, row #8; the PR #30 drift).

On Linux CI the dense distances differed from macOS by up to about 2e-5. EV-029's dense ranks 13
and 14 sit 1.96e-7 apart, so they swapped, its top 5 changed, and the gate called it stale
(docs/reviews/2026-11-08-RAG-03-drift.md). These tests make that flip on purpose, by moving the
query vector, and require the gate's retrieval to ignore it, because every eval query has a frozen
vector.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import psycopg
import pytest

from northwind_evals import cli
from northwind_evals.compare import fingerprint
from northwind_evals.contexts import load_contexts
from northwind_evals.integrity import integrity_problems
from northwind_evals.items import EvalItem, load_items

pytestmark = pytest.mark.req("RAG-03")

REPO = Path(__file__).resolve().parents[2]
# `make ci` at 86ff879 on macOS, and on a fresh scratch ingest; Linux CI printed 0fbf3491… then.
MACOS_HASH = "99135472f1545431c10e4b3bdab76bca3cf9eff9d233eb356d8f0486a8368074"


def _real_embedder() -> Any:
    """The fixtures embedded as `make ingest` embeds them. Before the drift fix there is no
    `CachedEmbedder`, so the failing-first run ingests live: that run must fail on an assertion,
    not on an import."""
    from northwind_rag import embedding

    inner = embedding.FastEmbedEmbedder()
    wrap = vars(embedding).get("CachedEmbedder")
    return wrap(inner) if wrap else inner


@pytest.fixture(scope="module")
def real_index() -> Iterator[psycopg.Connection]:
    from northwind_rag.cli import DEFAULT_DATABASE_URL
    from northwind_rag.ingest import ingest

    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"rag03_drift_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    connection = psycopg.connect(url, autocommit=True, options=f"-csearch_path={schema},public")
    try:
        ingest(connection, REPO / "data" / "fixtures", _real_embedder())
        yield connection
    finally:
        connection.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


@pytest.fixture(scope="module")
def items() -> list[EvalItem]:
    return load_items(REPO / "evals" / "fixtures" / "starter_set.jsonl")


def _item(items: list[EvalItem], item_id: str) -> EvalItem:
    return next(i for i in items if i.id == item_id)


def _vector(conn: psycopg.Connection, chunk_id: str) -> list[float]:
    row = conn.execute(
        "SELECT embedding::real[]::float8[] FROM rag_chunks WHERE chunk_id = %s", (chunk_id,)
    ).fetchone()
    assert row is not None
    return list(row[0])


def _cosine(q: Sequence[float], v: Sequence[float]) -> float:
    dot = math.fsum(a * b for a, b in zip(q, v, strict=True))
    return 1 - dot / math.sqrt(math.fsum(a * a for a in q) * math.fsum(b * b for b in v))


def _frozen_query(embedder: Any, query: str) -> list[float]:
    """The query's frozen vector, which every platform shares. A live embedding is not: on PR #30's
    x86_64 runners it already had EV-029's pair the other way round, so the precondition failed
    there (the drift itself, confirming the diagnosis). Missing is a failure, never a fallback."""
    from northwind_rag.embedding import frozen_key, frozen_path, load_frozen

    frozen = load_frozen(frozen_path(embedder.id), embedder.dim)
    key = frozen_key(query)
    assert key in frozen, f"no frozen vector for {query!r}: run `make embed-cache`"
    return list(frozen[key])


class Nudged:
    """The real embedder, except one query's vector moved toward `toward` and away from `away`,
    just far enough to swap their dense order: what another CPU did to EV-029. The move starts
    from the frozen query vector, so the precondition and the flip are the same on every CPU."""

    def __init__(self, query: str, toward: list[float], away: list[float]) -> None:
        from northwind_rag.embedding import FastEmbedEmbedder

        self._inner = FastEmbedEmbedder()
        self._query = query
        q = _frozen_query(self._inner, query)
        step = [t - a for t, a in zip(toward, away, strict=True)]
        epsilon = 1e-7
        while epsilon < 1e-2:
            moved = [x + epsilon * s for x, s in zip(q, step, strict=True)]
            if _cosine(moved, toward) < _cosine(moved, away):
                break
            epsilon *= 1.5
        assert _cosine(q, toward) > _cosine(q, away)  # the pair really was in the other order
        assert _cosine(moved, toward) < _cosine(moved, away), "no small nudge swaps the pair"
        self.vector = moved

    @property
    def id(self) -> str:
        return self._inner.id

    @property
    def dim(self) -> int:
        return self._inner.dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(self.vector) if t == self._query else self._inner.embed([t])[0] for t in texts]


@pytest.mark.integration
def test_a_near_tie_flip_in_the_query_vector_does_not_move_the_gates_context(
    real_index: psycopg.Connection, items: list[EvalItem]
) -> None:
    ev029 = _item(items, "EV-029")
    nudged = Nudged(
        ev029.query, _vector(real_index, "product:P0014:0"), _vector(real_index, "product:P0006:0")
    )
    saved = load_contexts(REPO / "evals" / "contexts" / "dense-only.jsonl")["EV-029"]
    live = cli._live(real_index, nudged, [ev029])["EV-029"]
    assert [c.chunk_id for c in live[:5]] == [c.chunk_id for c in saved.chunks]


@pytest.mark.integration
def test_a_near_tie_flip_does_not_move_the_dense_comparison_arm(
    real_index: psycopg.Connection, items: list[EvalItem]
) -> None:
    from northwind_rag.embedding import FastEmbedEmbedder

    ev029 = _item(items, "EV-029")
    # Dense ranks 8 and 9 for this query, 2.8e-6 apart: inside the dense arm's top 10.
    nudged = Nudged(
        ev029.query, _vector(real_index, "review:R00037:0"), _vector(real_index, "review:R00035:0")
    )
    plain = cli._arms(real_index, FastEmbedEmbedder(), [ev029])["dense"]["EV-029"]
    assert cli._arms(real_index, nudged, [ev029])["dense"]["EV-029"] == plain


@pytest.mark.integration
def test_the_eval_retrieval_over_every_item_gives_the_macos_hash(
    real_index: psycopg.Connection, items: list[EvalItem]
) -> None:
    """The cross-platform pin, not a failing-first test: on macOS it held before the fix too.
    In CI's integration job on x86_64 it is the check that failed on PR #30, now before the gate."""
    from northwind_rag.embedding import FastEmbedEmbedder

    live = cli._live(real_index, FastEmbedEmbedder(), items)
    assert fingerprint({i: [c.chunk_id for c in found] for i, found in live.items()}) == MACOS_HASH


# ---- the add-only rule for frozen vectors (D3) -------------------------------------------------

FROZEN = Path("data/embeddings/test-model-4.jsonl")
BASELINE = Path("evals/baselines/dense-only.json")


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)  # noqa: S603, S607


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(message: str) -> None:
    _git("add", "-A")
    _git("commit", "-q", "--allow-empty", "-m", message)


def _line(key: str, vector: Sequence[float]) -> str:
    return json.dumps({"key": key, "vector": list(vector)}) + "\n"


A, B, C = "g" * 64, "h" * 64, "i" * 64


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    _git("init", "-q", "-b", "main")
    _git("config", "user.email", "test@example.invalid")
    _git("config", "user.name", "test")
    _write(FROZEN, _line(A, [1, 0, 0, 0]) + _line(B, [0, 1, 0, 0]))
    _write(BASELINE, json.dumps({"overrides": []}))
    _commit("base")
    _git("switch", "-q", "-c", "feature")
    return tmp_path


def test_an_added_vector_or_file_passes(repo: Path) -> None:
    _write(FROZEN, _line(A, [1, 0, 0, 0]) + _line(B, [0, 1, 0, 0]) + _line(C, [0, 0, 1, 0]))
    _write(Path("data/embeddings/other-model-4.jsonl"), _line(A, [0, 0, 0, 1]))
    _commit("add")
    assert integrity_problems("main", BASELINE) == []


@pytest.mark.parametrize("committed", [True, False])
def test_a_changed_vector_is_refused(repo: Path, committed: bool) -> None:
    _write(FROZEN, _line(A, [1, 0, 0, 0]) + _line(B, [0, 0.9999999, 0, 0]))
    if committed:
        _commit("move one")
    problems = integrity_problems("main", BASELINE)
    assert any(str(FROZEN) in p and "changed" in p for p in problems), problems


def test_a_removed_key_is_refused(repo: Path) -> None:
    _write(FROZEN, _line(A, [1, 0, 0, 0]))
    _commit("drop one")
    problems = integrity_problems("main", BASELINE)
    assert any(str(FROZEN) in p and "removed" in p for p in problems), problems


def test_a_deleted_file_is_refused(repo: Path) -> None:
    FROZEN.unlink()
    _commit("delete")
    problems = integrity_problems("main", BASELINE)
    assert any(str(FROZEN) in p and "deleted" in p for p in problems), problems


# ---- frozen vectors must be what the model gives their text (security review, finding 1) -------

REAL_ID = "fastembed:BAAI/bge-small-en-v1.5:384"
REAL_FILE = Path("data/embeddings/fastembed-BAAI-bge-small-en-v1.5-384.jsonl")


def _unit(values: Sequence[float]) -> list[float]:
    from northwind_rag.embedding import to_float32

    norm = math.sqrt(math.fsum(v * v for v in values))
    return [to_float32(v / norm) for v in values]


class TextModel:
    """Stands in for the local model: a fixed unit vector per text, under the real id."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.id = REAL_ID

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        import hashlib

        out = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            out.append(_unit([digest[i % 32] + 1.0 for i in range(self.dim)]))
        return out


def _integrity(monkeypatch: pytest.MonkeyPatch, texts: dict[str, str]) -> int:
    import argparse

    monkeypatch.setattr(cli, "FastEmbedEmbedder", TextModel)
    monkeypatch.setattr(cli, "_frozen_texts", lambda: texts)
    return cli.cmd_integrity(argparse.Namespace(base="main"))


def test_an_added_vector_the_model_gives_its_text_passes_integrity(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from northwind_rag.embedding import frozen_key

    [honest] = TextModel().embed(["a wool throw"])
    _write(REAL_FILE, _line(frozen_key("a wool throw"), honest))
    _commit("add an honest vector")
    assert _integrity(monkeypatch, {frozen_key("a wool throw"): "a wool throw"}) == 0


def test_a_planted_vector_fails_integrity(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from northwind_rag.embedding import frozen_key

    [elsewhere] = TextModel().embed(["every shopper question at once"])
    _write(REAL_FILE, _line(frozen_key("a wool throw"), elsewhere))
    _commit("plant")
    assert _integrity(monkeypatch, {frozen_key("a wool throw"): "a wool throw"}) == 1
    assert "from the model's" in capsys.readouterr().err


def test_a_vector_for_a_text_nobody_has_fails_integrity(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    [vector] = TextModel().embed(["x"])
    _write(REAL_FILE, _line("j" * 64, vector))
    _commit("an orphan key")
    assert _integrity(monkeypatch, {}) == 1
    assert "no fixture, eval or smoke text" in capsys.readouterr().err


def test_a_frozen_file_no_embedder_here_knows_fails_integrity(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(FROZEN, _line(A, [1, 0, 0, 0]) + _line(B, [0, 1, 0, 0]) + _line(C, [0, 0, 1, 0]))
    _commit("add to the test model's file")
    assert _integrity(monkeypatch, {}) == 1
    assert "no embedder here can vouch" in capsys.readouterr().err


def test_a_failing_git_show_fails_closed(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from northwind_evals import integrity

    real = integrity._git

    def broken(*args: str) -> subprocess.CompletedProcess[str]:
        if (
            args[0] == "show"
            and args[1].endswith(FROZEN.as_posix())
            and not args[1].startswith("HEAD")
        ):
            return subprocess.CompletedProcess(list(args), 128, "", "fatal: bad object")
        return real(*args)

    monkeypatch.setattr(integrity, "_git", broken)
    with pytest.raises(RuntimeError, match="git show"):
        integrity_problems("main", BASELINE)


# ---- embed-cache writes only vectors the model vouches for -----------------------------------


class _Borrowed:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> psycopg.Connection:
        return self._conn

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def model_index() -> Iterator[psycopg.Connection]:
    """The fixtures ingested with TextModel (384 wide) under the real id, in a throwaway schema."""
    from northwind_rag.cli import DEFAULT_DATABASE_URL
    from northwind_rag.ingest import ingest

    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"rag03_vouch_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    connection = psycopg.connect(url, autocommit=True, options=f"-csearch_path={schema},public")
    try:
        ingest(connection, REPO / "data" / "fixtures", TextModel(384))
        yield connection
    finally:
        connection.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def _embed_cache(monkeypatch: pytest.MonkeyPatch, conn: psycopg.Connection, target: Path) -> int:
    monkeypatch.chdir(REPO)
    monkeypatch.setattr(cli, "_connect", lambda: _Borrowed(conn))
    monkeypatch.setattr(cli, "FastEmbedEmbedder", lambda: TextModel(384))
    monkeypatch.setattr(cli, "frozen_path", lambda embedder_id: target)
    return cli.main(["embed-cache"])


@pytest.mark.integration
def test_embed_cache_writes_the_index_vectors_the_model_vouches_for(
    model_index: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from northwind_rag.embedding import load_frozen

    target = tmp_path / "frozen.jsonl"
    assert _embed_cache(monkeypatch, model_index, target) == 0
    assert len(load_frozen(target, 384)) == len(cli._frozen_texts())
    first = target.read_bytes()
    assert _embed_cache(monkeypatch, model_index, target) == 0  # nothing new, nothing rewritten
    assert target.read_bytes() == first


@pytest.mark.integration
def test_embed_cache_refuses_an_index_row_the_model_does_not_vouch_for(
    model_index: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    [planted] = TextModel(384).embed(["every shopper question at once"])
    literal = "[" + ",".join(repr(x) for x in planted) + "]"
    model_index.execute(
        "UPDATE rag_chunks SET embedding = %s::vector WHERE chunk_id = "
        "(SELECT min(chunk_id) FROM rag_chunks)",
        (literal,),
    )
    target = tmp_path / "frozen.jsonl"
    assert _embed_cache(monkeypatch, model_index, target) == 1
    assert "refused" in capsys.readouterr().out
    assert not target.exists()


def test_a_duplicate_key_fails_closed(repo: Path) -> None:
    """Re-review N3: a second line for a base key could hide a planted vector."""
    _write(FROZEN, _line(B, [0, 0, 0, 1]) + _line(A, [1, 0, 0, 0]) + _line(B, [0, 1, 0, 0]))
    _commit("a second line for B")
    with pytest.raises(RuntimeError, match="twice"):
        integrity_problems("main", BASELINE)


def test_a_planted_vector_committed_at_head_fails_even_if_disk_is_honest(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Re-review N5: CI checks out HEAD, so HEAD is vouched for as well as the working tree."""
    from northwind_rag.embedding import frozen_key

    key = frozen_key("a wool throw")
    [elsewhere] = TextModel().embed(["every shopper question at once"])
    [honest] = TextModel().embed(["a wool throw"])
    _write(REAL_FILE, _line(key, elsewhere))
    _commit("plant at HEAD")
    _write(REAL_FILE, _line(key, honest))  # the working tree looks fine
    assert _integrity(monkeypatch, {key: "a wool throw"}) == 1
    assert "HEAD" in capsys.readouterr().err


def test_the_vouch_prints_the_largest_distance_it_saw(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from northwind_rag.embedding import frozen_key

    [honest] = TextModel().embed(["a wool throw"])
    _write(REAL_FILE, _line(frozen_key("a wool throw"), honest))
    _commit("honest")
    assert _integrity(monkeypatch, {frozen_key("a wool throw"): "a wool throw"}) == 0
    assert "largest distance 0.000e+00 (allowed 1e-04)" in capsys.readouterr().out
