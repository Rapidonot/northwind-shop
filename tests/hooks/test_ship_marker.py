"""AUTO-2, item 7a: `make test` may skip the ship tests, GitHub CI never does.

The filter is a shell snippet in the Makefile. It runs here in a throwaway repo, through a probe
target appended to a copy of the real Makefile. The marker and the environment route run on the
real pytest.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("AUTO-2")

REPO = Path(__file__).resolve().parents[2]
FILTER = '-m "not integration and not e2e and not ship"'
PROBE = '\nprobe:\n\t@echo "[$$($(SHIP_FILTER))]"\n'
WATCHED = ["tools/x.sh", "tests/ship/x.py", ".github/workflows/x.yml", "Makefile"]


def collect(*args: str, addopts: str = "") -> list[str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    if addopts:
        env["PYTEST_ADDOPTS"] = addopts
    done = subprocess.run(  # noqa: S603
        ["uv", "run", "pytest", "--collect-only", "-q", *args],  # noqa: S607
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode in (0, 5), done.stdout + done.stderr
    return [line for line in done.stdout.splitlines() if "::" in line]


def test_ci_keeps_a_bare_pytest_step() -> None:
    """A filter on this line, or in `addopts`, would take the tests out of GitHub CI."""
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert re.search(r"^\s*- run: uv run pytest$", ci, re.M)


def test_addopts_does_not_name_the_marker() -> None:
    pyproject = (REPO / "pyproject.toml").read_text()
    addopts = re.search(r"^addopts = (.*)$", pyproject, re.M)
    assert addopts and "ship" not in addopts.group(1)


def test_plain_pytest_collects_the_ship_tests() -> None:
    assert collect("tests/ship/test_refused_paths.py")


def test_the_filter_leaves_only_the_guard_tests_in_tests_ship() -> None:
    left = collect("tests/ship", addopts=FILTER)
    assert left, "the filter removed everything"
    assert {line.split("::")[0] for line in left} == {"tests/ship/test_guard.py"}


@pytest.mark.parametrize(
    "name", ["test_ship.py", "test_pre_push.py", "test_refused_paths.py", "test_finish.py"]
)
def test_each_ship_file_is_behind_the_marker(name: str) -> None:
    assert collect(f"tests/ship/{name}")
    assert collect(f"tests/ship/{name}", addopts=FILTER) == []


def test_a_command_line_marker_beats_the_filter_in_the_environment() -> None:
    """`make test-integration` must not lose its selection to a filter left in the environment."""
    plain = collect("services/rag", "-m", "integration")
    assert plain
    assert collect("services/rag", "-m", "integration", addopts=FILTER) == plain


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def write(repo: Path, name: str, text: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo whose `origin/main` is its HEAD, holding one file under each watched path."""
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "test")
    write(tmp_path, "Makefile", (REPO / "Makefile").read_text() + PROBE)
    for name in WATCHED[:-1]:
        write(tmp_path, name, "one\n")
    write(tmp_path, "docs/x.md", "one\n")
    commit_all(tmp_path, "base")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def probe(repo: Path) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": str(repo)}
    done = subprocess.run(  # noqa: S603
        ["make", "-s", "probe"],  # noqa: S607
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def test_an_unchanged_tree_prints_the_filter(repo: Path) -> None:
    assert probe(repo) == f"[{FILTER}]"


def test_a_change_elsewhere_keeps_the_filter(repo: Path) -> None:
    write(repo, "docs/x.md", "two\n")
    write(repo, "docs/new.md", "new\n")
    assert "not ship" in probe(repo)


@pytest.mark.parametrize("name", WATCHED)
def test_an_uncommitted_change_runs_the_tests(repo: Path, name: str) -> None:
    write(repo, name, (repo / name).read_text() + "\n# two\n")
    assert probe(repo) == "[]"


@pytest.mark.parametrize("name", WATCHED)
def test_a_committed_change_runs_the_tests(repo: Path, name: str) -> None:
    write(repo, name, (repo / name).read_text() + "\n# two\n")
    commit_all(repo, "change")
    assert probe(repo) == "[]"


@pytest.mark.parametrize("name", ["tools/new.sh", "tests/ship/new.py", ".github/new.yml"])
def test_an_untracked_file_runs_the_tests(repo: Path, name: str) -> None:
    write(repo, name, "new\n")
    assert probe(repo) == "[]"


def test_a_deleted_file_runs_the_tests(repo: Path) -> None:
    (repo / "tools" / "x.sh").unlink()
    assert probe(repo) == "[]"


def test_no_merge_base_runs_the_tests(repo: Path) -> None:
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert probe(repo) == "[]"


def test_outside_a_repo_the_tests_run(tmp_path: Path) -> None:
    write(tmp_path, "Makefile", (REPO / "Makefile").read_text() + PROBE)
    assert probe(tmp_path) == "[]"
