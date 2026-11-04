"""`make eval-integrity` against a real git history (RAG-12, decision 0003)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from northwind_evals import cli
from northwind_evals.integrity import integrity_problems

pytestmark = pytest.mark.req("RAG-12")

BASELINE = Path("evals/baselines/dense-only.json")
CASSETTE = Path("cassettes/eval-answers/abc.json")
OVERRIDE = {"on": "2026-11-04", "reason": "agreed", "accepted": ["relevance regressed"]}


def git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)  # noqa: S603, S607


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(message: str) -> None:
    git("add", "-A")
    git("commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A base branch with one cassette and a baseline holding one override."""
    monkeypatch.chdir(tmp_path)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    write(CASSETTE, '{"body": "behaviour_ok false"}')
    write(BASELINE, json.dumps({"overrides": [OVERRIDE]}))
    commit("base")
    git("switch", "-q", "-c", "feature")
    return tmp_path


def test_a_clean_branch_passes(repo: Path) -> None:
    assert integrity_problems("main", BASELINE) == []


def test_a_committed_cassette_edit_is_refused(repo: Path) -> None:
    write(CASSETTE, '{"body": "behaviour_ok true"}')
    commit("flip a verdict")
    assert integrity_problems("main", BASELINE) == [
        f"{CASSETTE}: an existing cassette was modified"
    ]


def test_an_uncommitted_cassette_edit_is_refused(repo: Path) -> None:
    write(CASSETTE, '{"body": "behaviour_ok true"}')
    assert integrity_problems("main", BASELINE)


def test_deleting_and_re_recording_under_the_same_name_is_refused(repo: Path) -> None:
    CASSETTE.unlink()
    commit("delete")
    write(CASSETTE, '{"body": "a luckier verdict"}')
    commit("re-record")
    assert integrity_problems("main", BASELINE)


def test_a_new_cassette_and_a_deleted_one_are_both_allowed(repo: Path) -> None:
    write(Path("cassettes/eval-answers/new.json"), "{}")
    CASSETTE.unlink()
    commit("re-recorded after a prompt change")
    assert integrity_problems("main", BASELINE) == []


def test_adding_an_override_is_allowed(repo: Path) -> None:
    write(BASELINE, json.dumps({"overrides": [OVERRIDE, {**OVERRIDE, "reason": "later"}]}))
    assert integrity_problems("main", BASELINE) == []


@pytest.mark.parametrize(
    "overrides", [[], [{**OVERRIDE, "reason": "rewritten"}]], ids=["removed", "rewritten"]
)
def test_removing_or_rewriting_an_override_is_refused(repo: Path, overrides: list[object]) -> None:
    write(BASELINE, json.dumps({"overrides": overrides}))
    assert integrity_problems("main", BASELINE) == ["recorded overrides were removed or changed"]


def test_deleting_the_baseline_is_refused(repo: Path) -> None:
    BASELINE.unlink()
    assert integrity_problems("main", BASELINE) == [f"{BASELINE} is gone"]


def test_a_base_branch_with_no_baseline_yet_has_nothing_to_protect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    commit("empty")
    write(BASELINE, json.dumps({"overrides": []}))
    assert integrity_problems("main", BASELINE) == []


def test_an_unknown_base_branch_fails_closed(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["integrity", "--base", "origin/nothing"]) == 1
    assert "FAILED" in capsys.readouterr().err


def test_the_command_reports_a_modified_cassette(repo: Path) -> None:
    write(CASSETTE, '{"body": "behaviour_ok true"}')
    assert cli.main(["integrity", "--base", "main"]) == 1
