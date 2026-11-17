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


# ---- row #12: a re-recorded answerer replaces its own refinements -------------------------------


def baseline_with(prompt: str) -> str:
    answerer = f"assistant:v1:claude-sonnet-5-5:prompt-{prompt}:check-0cb61e75"
    return json.dumps({"overrides": [], "answerer_id": answerer})


@pytest.fixture
def promoted_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    write(BASELINE, [baseline_with("aaaaaaaa")])
    write(REFINEMENTS, [entry("EV-1", 1), entry("EV-2", 1)])
    commit("base")
    git("switch", "-q", "-c", "feature")
    return tmp_path


@pytest.mark.req("RAG-09")
def test_a_new_answerer_prompt_lets_its_refinements_change_and_reports_each(
    promoted_repo: Path,
) -> None:
    from northwind_evals.integrity import replaced_refinements

    write(BASELINE, [baseline_with("bbbbbbbb")])
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-3", 1)])
    commit("promote a re-recorded answerer")
    assert integrity_problems("main", BASELINE) == []
    replaced = replaced_refinements("main", BASELINE)
    assert [(r["item_id"], r["n"]) for r in replaced] == [("EV-1", 1), ("EV-2", 1)]
    changed, dropped = replaced
    assert len(changed["old"]) == 16 and changed["new"] not in ("-", changed["old"])
    assert dropped["new"] == "-"


@pytest.mark.req("RAG-09")
def test_the_same_answerer_prompt_keeps_refinements_add_only(promoted_repo: Path) -> None:
    from northwind_evals.integrity import replaced_refinements

    write(BASELINE, [baseline_with("aaaaaaaa").replace("0cb61e75", "11111111")])  # check part only
    write(REFINEMENTS, [entry("EV-1", 1, "Tampered.")])
    commit("edit")
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (HEAD)" in problems
    assert f"{REFINEMENTS}: refinement EV-2#1 removed (HEAD)" in problems
    assert replaced_refinements("main", BASELINE) == []


@pytest.mark.req("RAG-09")
def test_an_uncommitted_baseline_change_does_not_open_the_exemption(promoted_repo: Path) -> None:
    # Both the committed and the on-disk baseline must hold the new prompt part.
    write(BASELINE, [baseline_with("bbbbbbbb")])
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded.")])
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (disk)" in problems


@pytest.mark.req("RAG-09")
def test_an_unreadable_answerer_id_keeps_refinements_add_only(promoted_repo: Path) -> None:
    write(BASELINE, [json.dumps({"overrides": [], "answerer_id": "assistant:v1:m"})])
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded.")])
    commit("no prompt part")
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (HEAD)" in integrity_problems(
        "main", BASELINE
    )


@pytest.mark.req("RAG-09")
def test_cmd_integrity_prints_each_replaced_refinement(
    promoted_repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import argparse

    from northwind_evals import cli

    monkeypatch.setattr(cli, "added_frozen_vectors", lambda _commit: {})
    monkeypatch.setattr(cli, "added_known_failures", lambda _commit, _path: [])
    write(BASELINE, [baseline_with("bbbbbbbb")])
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded.")])
    commit("promote")
    assert cli.cmd_integrity(argparse.Namespace(base="main")) == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if "refinement replaced:" in line]
    assert len(lines) == 2 and '"item_id": "EV-1"' in lines[0] and '"new": "-"' in lines[1]
