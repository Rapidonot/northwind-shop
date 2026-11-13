"""`make eval-integrity` holds recorded refinements add-only (row #13b, the user's decision).

An existing `evals/contexts/refinements.ndjson` entry is a recording: like a cassette or a frozen
vector, it may never change or disappear against the base branch. Added entries pass.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from northwind_evals.integrity import integrity_problems

pytestmark = pytest.mark.req("NFR-03")

BASELINE = Path("evals/baselines/dense-only.json")
REFINEMENTS = Path("evals/contexts/refinements.ndjson")


def git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)  # noqa: S603, S607


def entry(item_id: str, n: int, text: str = "Great.") -> str:
    chunk = {"chunk_id": "R9#0", "source_id": "R9", "source_type": "review", "text": text}
    record = {"chunks": [chunk], "item_id": item_id, "n": n, "query": "q"}
    return json.dumps(record, sort_keys=True)


def write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def commit(message: str) -> None:
    git("add", "-A")
    git("commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    write(BASELINE, [json.dumps({"overrides": []})])
    write(REFINEMENTS, [entry("EV-1", 1)])
    commit("base")
    git("switch", "-q", "-c", "feature")
    return tmp_path


def test_an_unchanged_file_passes(repo: Path) -> None:
    assert integrity_problems("main", BASELINE) == []


def test_an_added_entry_passes(repo: Path) -> None:
    write(REFINEMENTS, [entry("EV-1", 1), entry("EV-2", 1)])
    commit("add")
    assert integrity_problems("main", BASELINE) == []


def test_a_committed_changed_entry_is_refused(repo: Path) -> None:
    write(REFINEMENTS, [entry("EV-1", 1, "Tampered.")])
    commit("edit")
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (HEAD)" in problems
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (disk)" in problems


def test_an_uncommitted_changed_entry_is_refused(repo: Path) -> None:
    write(REFINEMENTS, [entry("EV-1", 1, "Tampered.")])
    assert integrity_problems("main", BASELINE) == [
        f"{REFINEMENTS}: refinement EV-1#1 changed (disk)"
    ]


def test_a_deleted_entry_is_refused(repo: Path) -> None:
    write(REFINEMENTS, [entry("EV-2", 1)])
    commit("drop one")
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: refinement EV-1#1 removed (HEAD)" in problems


def test_a_deleted_file_is_refused(repo: Path) -> None:
    git("rm", "-q", str(REFINEMENTS))
    commit("drop the file")
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: deleted (HEAD); refinements may only be added" in problems
    assert f"{REFINEMENTS}: deleted (disk); refinements may only be added" in problems


def test_a_duplicate_entry_is_refused(repo: Path) -> None:
    write(REFINEMENTS, [entry("EV-1", 1), entry("EV-1", 1)])
    with pytest.raises(RuntimeError, match="twice"):
        integrity_problems("main", BASELINE)


def test_a_base_with_no_refinements_file_has_nothing_to_protect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    write(BASELINE, [json.dumps({"overrides": []})])
    commit("base")
    git("switch", "-q", "-c", "feature")
    write(REFINEMENTS, [entry("EV-1", 1)])
    commit("first recording")
    assert integrity_problems("main", BASELINE) == []
