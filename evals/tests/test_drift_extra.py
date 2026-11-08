"""RAG-03 drift fix, extra eval-side checks: frozen queries, the add-only rule, embed-cache."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path

import psycopg
import pytest

from northwind_evals import cli
from northwind_evals.integrity import integrity_problems
from northwind_evals.items import EvalItem, load_items
from northwind_rag.embedding import FastEmbedEmbedder, cached, frozen_key, load_frozen

pytestmark = pytest.mark.req("RAG-03")

REPO = Path(__file__).resolve().parents[2]


class NoModel:
    """Carries the real embedder's id and dim but refuses to embed: a frozen query must never
    reach it, whatever embedder the caller passes."""

    def __init__(self) -> None:
        real = FastEmbedEmbedder()
        self._id, self._dim = real.id, real.dim

    @property
    def id(self) -> str:
        return self._id

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError(f"the model was asked to embed {len(texts)} text(s)")


def _fresh_index() -> Iterator[psycopg.Connection]:
    from northwind_rag.cli import DEFAULT_DATABASE_URL
    from northwind_rag.ingest import ingest

    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"rag03_extra_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    connection = psycopg.connect(url, autocommit=True, options=f"-csearch_path={schema},public")
    try:
        ingest(connection, REPO / "data" / "fixtures", cached(FastEmbedEmbedder()))
        yield connection
    finally:
        connection.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


@pytest.fixture(scope="module")
def real_index() -> Iterator[psycopg.Connection]:
    yield from _fresh_index()


@pytest.fixture
def own_index() -> Iterator[psycopg.Connection]:
    yield from _fresh_index()


@pytest.fixture(scope="module")
def items() -> list[EvalItem]:
    return load_items(REPO / "evals" / "fixtures" / "starter_set.jsonl")


@pytest.mark.integration
def test_live_never_asks_the_model_for_a_frozen_query(
    real_index: psycopg.Connection, items: list[EvalItem]
) -> None:
    got = cli._live(real_index, NoModel(), items)
    assert set(got) == {i.id for i in items}
    assert all(got[i.id] for i in items)


@pytest.mark.integration
def test_live_gives_the_same_answer_for_a_bare_and_a_wrapped_embedder(
    real_index: psycopg.Connection, items: list[EvalItem]
) -> None:
    wrapped = cli._live(real_index, cached(FastEmbedEmbedder()), items[:10])
    bare = cli._live(real_index, NoModel(), items[:10])
    assert {k: [c.chunk_id for c in v] for k, v in bare.items()} == {
        k: [c.chunk_id for c in v] for k, v in wrapped.items()
    }


@pytest.mark.integration
def test_arms_never_asks_the_model_for_a_frozen_query_and_names_all_three_arms(
    real_index: psycopg.Connection, items: list[EvalItem]
) -> None:
    arms = cli._arms(real_index, NoModel(), items[:12])
    assert set(arms) == {"dense", "keyword", "hybrid"}
    for ranked in arms.values():
        assert set(ranked) == {i.id for i in items[:12]}


@pytest.mark.integration
def test_every_eval_query_has_a_frozen_vector() -> None:
    frozen = load_frozen(
        REPO / "data" / "embeddings" / "fastembed-BAAI-bge-small-en-v1.5-384.jsonl", 384
    )
    items = load_items(REPO / "evals" / "fixtures" / "starter_set.jsonl")
    assert [i.id for i in items if frozen_key(i.query) not in frozen] == []


# ---- embed-cache against a throwaway schema ----------------------------------------------------


class _Borrowed:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> psycopg.Connection:
        return self._conn

    def __exit__(self, *exc: object) -> None:
        return None


def _check(monkeypatch: pytest.MonkeyPatch, conn: psycopg.Connection) -> int:
    monkeypatch.chdir(REPO)
    monkeypatch.setattr(cli, "_connect", lambda: _Borrowed(conn))
    return cli.main(["embed-cache", "--check"])


@pytest.mark.integration
def test_embed_cache_check_passes_on_an_index_made_from_the_frozen_vectors(
    own_index: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _check(monkeypatch, own_index) == 0
    out = capsys.readouterr().out
    assert "0 problem(s)" in out


@pytest.mark.integration
def test_embed_cache_check_fails_and_writes_nothing_when_a_vector_differs(
    own_index: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = REPO / "data" / "embeddings" / "fastembed-BAAI-bge-small-en-v1.5-384.jsonl"
    before = path.read_bytes()
    row = own_index.execute("SELECT chunk_id FROM rag_chunks ORDER BY chunk_id LIMIT 1").fetchone()
    assert row is not None
    own_index.execute(
        "UPDATE rag_chunks SET embedding = ((embedding::real[])[1:383] "
        "|| ((embedding::real[])[384] + 1e-3::real))::vector WHERE chunk_id = %s",
        (row[0],),
    )
    assert _check(monkeypatch, own_index) == 1
    out = capsys.readouterr().out
    assert row[0] in out and "1 problem(s)" in out
    assert path.read_bytes() == before


@pytest.mark.integration
def test_embed_cache_check_fails_for_a_chunk_missing_from_the_index(
    own_index: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    row = own_index.execute("SELECT chunk_id FROM rag_chunks ORDER BY chunk_id LIMIT 1").fetchone()
    assert row is not None
    own_index.execute("DELETE FROM rag_chunks WHERE chunk_id = %s", (row[0],))
    assert _check(monkeypatch, own_index) == 1
    assert row[0] in capsys.readouterr().out


def test_embed_cache_is_a_subcommand_with_a_check_flag() -> None:
    parser_help = subprocess.run(  # noqa: S603
        ["uv", "run", "python", "-m", "northwind_evals.cli", "embed-cache", "--help"],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )
    assert parser_help.returncode == 0
    assert "--check" in parser_help.stdout


# ---- the add-only rule, edge cases -------------------------------------------------------------

FROZEN = Path("data/embeddings/test-model-4.jsonl")
BASELINE = Path("evals/baselines/dense-only.json")
A, B, C = "g" * 64, "h" * 64, "i" * 64


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


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    _git("init", "-q", "-b", "main")
    _git("config", "user.email", "test@example.invalid")
    _git("config", "user.name", "test")
    _write(FROZEN, _line(A, [1, 0, 0, 0]) + _line(B, [0, 1, 0, 0]))
    _write(Path("data/embeddings/README.md"), "notes\n")
    _write(BASELINE, json.dumps({"overrides": []}))
    _commit("base")
    _git("switch", "-q", "-c", "feature")
    return tmp_path


def test_a_deleted_file_on_disk_only_is_refused(repo: Path) -> None:
    FROZEN.unlink()
    problems = integrity_problems("main", BASELINE)
    assert any(str(FROZEN) in p and "deleted" in p for p in problems), problems


def test_a_removed_key_on_disk_only_is_refused(repo: Path) -> None:
    _write(FROZEN, _line(A, [1, 0, 0, 0]))
    problems = integrity_problems("main", BASELINE)
    assert any(str(FROZEN) in p and "removed" in p for p in problems), problems


def test_a_reformatted_line_for_the_same_vector_counts_as_changed(repo: Path) -> None:
    _write(FROZEN, _line(A, [1.0, 0.0, 0.0, 0.0]) + _line(B, [0, 1, 0, 0]))
    problems = integrity_problems("main", BASELINE)
    assert any(A in p and "changed" in p for p in problems), problems


def test_a_committed_change_undone_on_disk_is_still_refused_at_head(repo: Path) -> None:
    original = FROZEN.read_text(encoding="utf-8")
    _write(FROZEN, _line(A, [0, 0, 1, 0]) + _line(B, [0, 1, 0, 0]))
    _commit("move")
    _write(FROZEN, original)  # the working tree looks fine; HEAD does not
    problems = integrity_problems("main", BASELINE)
    assert any(A in p and "changed" in p for p in problems), problems


def test_only_the_broken_file_is_named(repo: Path) -> None:
    other = Path("data/embeddings/other-4.jsonl")
    _write(other, _line(A, [1, 0, 0, 0]))
    _commit("second file")
    _git("switch", "-q", "main")
    _git("merge", "-q", "--ff-only", "feature")
    _git("switch", "-q", "-c", "feature2")
    _write(FROZEN, _line(A, [1, 0, 0, 0]) + _line(B, [0, 0, 1, 0]))
    problems = integrity_problems("main", BASELINE)
    assert any(str(FROZEN) in p for p in problems)
    assert not any(str(other) in p for p in problems)


def test_a_new_key_in_an_existing_file_and_a_non_jsonl_edit_pass(repo: Path) -> None:
    _write(FROZEN, FROZEN.read_text(encoding="utf-8") + _line(C, [0, 0, 1, 0]))
    _write(Path("data/embeddings/README.md"), "other notes\n")
    _commit("grow")
    assert integrity_problems("main", BASELINE) == []


def test_a_repo_with_no_frozen_files_at_the_base_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _git("init", "-q", "-b", "main")
    _git("config", "user.email", "test@example.invalid")
    _git("config", "user.name", "test")
    _write(BASELINE, json.dumps({"overrides": []}))
    _commit("base")
    _git("switch", "-q", "-c", "feature")
    _write(FROZEN, _line(A, [1, 0, 0, 0]))
    _commit("first vectors")
    assert integrity_problems("main", BASELINE) == []
