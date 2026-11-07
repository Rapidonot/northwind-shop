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


def link(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)


@pytest.mark.req("AUTO-2")
def test_a_cassette_turned_into_a_symlink_is_refused(repo: Path) -> None:
    CASSETTE.unlink()
    link(CASSETTE, "../../elsewhere.json")
    commit("type change")
    problems = integrity_problems("main", BASELINE)
    assert f"{CASSETTE}: an existing cassette was modified" in problems
    assert f"{CASSETTE}: a cassette may not be a symlink" in problems


@pytest.mark.req("AUTO-2")
def test_an_uncommitted_type_change_is_refused(repo: Path) -> None:
    CASSETTE.unlink()
    link(CASSETTE, "../../elsewhere.json")
    assert f"{CASSETTE}: a cassette may not be a symlink" in integrity_problems("main", BASELINE)


@pytest.mark.req("AUTO-2")
def test_a_cassette_added_as_a_symlink_is_refused(repo: Path) -> None:
    added = CASSETTE.with_name("new.json")
    link(added, "../../elsewhere.json")
    commit("add a link")
    assert integrity_problems("main", BASELINE) == [f"{added}: a cassette may not be a symlink"]


@pytest.mark.req("AUTO-2")
def test_an_untracked_symlink_under_cassettes_is_refused(repo: Path) -> None:
    added = CASSETTE.with_name("new.json")
    link(added, "../../elsewhere.json")
    assert integrity_problems("main", BASELINE) == [f"{added}: a cassette may not be a symlink"]


@pytest.mark.req("AUTO-2")
def test_a_directory_symlink_under_cassettes_is_refused(repo: Path) -> None:
    folder = Path("cassettes/linked")
    link(folder, "..")
    assert integrity_problems("main", BASELINE) == [f"{folder}: a cassette may not be a symlink"]


@pytest.mark.req("AUTO-2")
def test_a_symlink_outside_cassettes_is_not_this_checks_business(repo: Path) -> None:
    link(Path("docs/link"), "../../elsewhere.json")
    commit("a link elsewhere")
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


# --- known failures added against the base (5a, F5) ---------------------------------------------

KNOWN = Path("evals/fixtures/known_failures.json")
PREFIX = "known failure added:"


def kf(item_id: str, approved_by: str = "model", reason: str = "a reason") -> dict[str, str]:
    return {
        "item_id": item_id,
        "reason": reason,
        "fix_item": "SA-09",
        "approved_by": approved_by,
        "approved_on": "2026-11-04",
    }


def write_known(entries: list[dict[str, str]]) -> None:
    write(KNOWN, json.dumps(entries, indent=2))


@pytest.fixture
def known_repo(repo: Path) -> Path:
    """The base branch holds known_failures.json with one entry; `feature` starts at its tip."""
    git("switch", "-q", "main")
    write_known([kf("EV-1")])
    commit("base known failures")
    git("switch", "-q", "feature")
    git("reset", "-q", "--hard", "main")
    return repo


def added_lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    out = capsys.readouterr().out
    return [line.strip() for line in out.splitlines() if line.strip().startswith(PREFIX)]


def run_integrity() -> int:
    return cli.main(["integrity", "--base", "main"])


def test_a_committed_known_failure_is_printed_and_printing_alone_passes(
    known_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_known([kf("EV-1"), kf("EV-2")])
    commit("add a known failure")
    assert run_integrity() == 0
    [line] = added_lines(capsys)
    assert '"EV-2"' in line
    assert '"EV-1"' not in line


def test_an_uncommitted_known_failure_is_printed(
    known_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_known([kf("EV-1"), kf("EV-3")])
    assert run_integrity() == 0
    [line] = added_lines(capsys)
    assert '"EV-3"' in line


def test_a_changed_known_failure_counts_as_added(
    known_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_known([kf("EV-1", approved_by="human")])
    assert run_integrity() == 0
    [line] = added_lines(capsys)
    assert '"EV-1"' in line
    assert "human" in line


def test_nothing_added_prints_no_known_failure_line(
    known_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_integrity() == 0
    assert added_lines(capsys) == []


def test_a_removed_known_failure_prints_nothing(
    known_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_known([])
    assert run_integrity() == 0
    assert added_lines(capsys) == []


def test_a_newline_in_a_reason_stays_on_one_output_line(
    known_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_known([kf("EV-1"), kf("EV-2", reason="first\nsecond line")])
    assert run_integrity() == 0
    out = capsys.readouterr().out
    [line] = [x for x in out.splitlines() if x.strip().startswith(PREFIX)]
    assert "second line" in line
    assert not [x for x in out.splitlines() if x.strip().startswith("second line")]


def test_a_malformed_known_failures_file_fails_the_command(
    known_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(KNOWN, "{not json")
    assert run_integrity() == 1
    assert "eval-integrity FAILED" in capsys.readouterr().err


def test_a_base_without_the_file_counts_every_entry_as_added(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_known([kf("EV-1"), kf("EV-2")])
    assert run_integrity() == 0
    assert len(added_lines(capsys)) == 2


def test_an_entry_in_head_is_printed_when_the_file_on_disk_lacks_it(
    known_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What a failed switch under `denyWrite` leaves: HEAD holds the entry, the disk does not."""
    before = KNOWN.read_text(encoding="utf-8")
    write_known([kf("EV-1"), kf("EV-2")])
    commit("add a known failure")
    write(KNOWN, before)
    assert cli.main(["integrity", "--base", "main"]) == 0
    assert '"item_id": "EV-2"' in capsys.readouterr().out


def test_a_missing_file_adds_nothing(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_integrity() == 0
    assert added_lines(capsys) == []


def test_added_known_failures_returns_the_new_entries(known_repo: Path) -> None:
    from northwind_evals.integrity import added_known_failures, merge_base

    write_known([kf("EV-1"), kf("EV-2")])
    assert added_known_failures(merge_base("main"), KNOWN) == [kf("EV-2")]
    KNOWN.unlink()
    assert added_known_failures(merge_base("main"), KNOWN) == []
