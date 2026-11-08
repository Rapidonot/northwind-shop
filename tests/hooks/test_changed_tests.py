"""AUTO-2, item 7c: which tests `make check` runs for which paths.

`make check` is for during a row and is never evidence: only `make ci` is. So a miss here costs a
surprise at `make ci`, not a hole, but what cannot be mapped must run everything.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("AUTO-2")

REPO = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("changed_tests", REPO / "tools" / "changed_tests.py")
assert _spec is not None and _spec.loader is not None
changed = importlib.util.module_from_spec(_spec)
sys.modules["changed_tests"] = changed
_spec.loader.exec_module(changed)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("services/rag/src/northwind_rag/store.py", {"services/rag"}),
        ("services/backend/tests/test_x.py", {"services/backend"}),
        ("evals/src/northwind_evals/cli.py", {"evals"}),
        ("data/src/northwind_data/cli.py", {"data"}),
        ("data/enrichment/public.jsonl", {"data", "services/rag", "evals"}),
        ("data/embeddings/fastembed-x.jsonl", {"data", "services/rag", "evals"}),
        ("tests/hooks/test_settings.py", {"tests/hooks/test_settings.py"}),
        ("tests/ship/shared.py", {"tests/ship"}),
        ("tools/ship.sh", {"tests/ship", "tests/hooks"}),
        ("tools/finish.sh", {"tests/ship", "tests/hooks"}),
        ("tools/settings.proposed.json", {"tests/hooks"}),
        ("tools/changed_tests.py", {"tests/hooks"}),
        ("CLAUDE.md", {"tests/hooks"}),
        (".claude/agents/test-writer.md", {"tests/hooks"}),
        ("PROGRESS.md", {"tests/governance"}),
        ("COVERAGE.md", {"tests/governance"}),
        ("docs/governance/threat-model.md", {"tests/governance"}),
        ("docs/reviews/x.md", {"tests/governance"}),
        ("apps/portal/src/App.tsx", {changed.PORTAL}),
    ],
)
def test_a_known_path_maps_to_its_tests(path: str, expected: set[str]) -> None:
    assert changed.targets([path]) == expected


@pytest.mark.parametrize(
    "path",
    [
        "services/common/src/northwind_common/llm.py",
        "services/llm-replay/src/northwind_llm_replay/proxy.py",
        "uv.lock",
        "pyproject.toml",
        "services/rag/pyproject.toml",
        "Makefile",
        "conftest.py",
        "services/rag/tests/conftest.py",
        ".github/workflows/ci.yml",
        "cassettes/eval-answers/x.json",
        "infra/db/migrations/0001_rag.sql",
        "package.json",
        "pnpm-lock.yaml",
        "something/new.txt",
    ],
)
def test_anything_shared_or_unknown_runs_everything(path: str) -> None:
    assert changed.targets([path]) == {changed.ALL}


def test_one_shared_path_among_many_runs_everything() -> None:
    assert changed.targets(["docs/x.md", "uv.lock", "evals/a.py"]) == {changed.ALL}


def test_paths_add_up() -> None:
    assert changed.targets(["docs/x.md", "services/rag/a.py"]) == {
        "tests/governance",
        "services/rag",
    }


def test_nothing_changed_runs_nothing() -> None:
    assert changed.targets([]) == set()


def test_the_plan_line_puts_everything_alone() -> None:
    assert changed.plan({changed.ALL}) == "all"
    assert changed.plan({"tests/hooks", "evals"}) == "evals tests/hooks"
    assert changed.plan(set()) == ""


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # noqa: S603, S607


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "test")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("one\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def test_changed_paths_counts_committed_uncommitted_and_untracked(repo: Path) -> None:
    (repo / "docs" / "x.md").write_text("two\n")
    (repo / "docs" / "new.md").write_text("new\n")
    assert changed.changed_paths(repo) == ["docs/new.md", "docs/x.md"]
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")
    assert changed.changed_paths(repo) == ["docs/new.md", "docs/x.md"]


def test_no_merge_base_runs_everything(repo: Path) -> None:
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert changed.changed_paths(repo) is None
    assert changed.main(repo) == "all"
