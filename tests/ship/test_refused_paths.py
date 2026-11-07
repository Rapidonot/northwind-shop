"""AUTO-1: tools/refused-paths.sh called directly, for what `ship` and `runner-sync` cannot reach.

`ship` runs its own diff first, so an error here would otherwise only show through runner-sync.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.req("AUTO-1"), pytest.mark.ship]

_spec = importlib.util.spec_from_file_location("ship_shared", Path(__file__).with_name("shared.py"))
assert _spec is not None and _spec.loader is not None
shared = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("ship_shared", shared)
_spec.loader.exec_module(shared)

SCRIPT = shared.REPO / "tools" / "refused-paths.sh"
GITLINK = "160000"


@dataclass
class Repo:
    path: Path
    bin_dir: Path
    sh_env: dict[str, str]

    def git(self, *args: str) -> str:
        return str(shared.git(self.path, self.sh_env, *args))

    def write(self, name: str, text: str) -> None:
        (self.path / name).write_text(text)

    def fail_subcommand(self, name: str) -> None:
        """Put a git on PATH that exits 128 for one subcommand and is real git otherwise."""
        real = shutil.which("git")
        shim = self.bin_dir / "git"
        shim.write_text(
            f'#!/bin/bash\nfor a in "$@"; do [ "$a" = {name} ] && exit 128; done\n'
            f'exec {real} "$@"\n'
        )
        shim.chmod(0o755)

    def run(self, base: str = "HEAD~1", head: str = "HEAD") -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["/bin/bash", str(SCRIPT), base, head],
            cwd=self.path,
            capture_output=True,
            text=True,
            check=False,
            env=self.sh_env,
        )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sh_env = shared.tool_env(tmp_path, bin_dir)
    path = tmp_path / "repo"
    path.mkdir()
    made = Repo(path, bin_dir, sh_env)
    made.git("init", "-q", "-b", "main")
    made.write(".gitignore", "*.log\n")
    made.write("README", "one\n")
    made.git("add", ".")
    made.git("commit", "-q", "-m", "base")
    made.write("README", "two\n")
    made.git("commit", "-qam", "change")
    return made


def test_a_clean_change_passes(repo: Repo) -> None:
    done = repo.run()
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("path", shared.NON_ASCII_PATHS)
def test_a_path_outside_printable_ascii_is_refused(repo: Repo, path: str) -> None:
    head = str(shared.make_commit(repo.path, repo.sh_env, "HEAD", {path: "x\n"}))
    done = repo.run(base="HEAD", head=head)
    assert done.returncode != 0
    assert "must not reach the runner clone" in done.stderr


@pytest.mark.parametrize("path", shared.NON_ASCII_PATHS)
def test_a_utf8_terminal_does_not_let_a_non_ascii_path_through(repo: Repo, path: str) -> None:
    repo.sh_env.update(shared.UTF8_LOCALE)
    head = str(shared.make_commit(repo.path, repo.sh_env, "HEAD", {path: "x\n"}))
    done = repo.run(base="HEAD", head=head)
    assert done.returncode != 0
    assert "must not reach the runner clone" in done.stderr


def test_a_tracked_path_the_gitignore_ignores_is_refused(repo: Repo) -> None:
    # Without --no-index, git treats a tracked file as not ignored whatever .gitignore says.
    repo.write("kept.log", "one\n")
    repo.git("add", "-f", "kept.log")
    repo.git("commit", "-q", "-m", "force-added")
    repo.write("kept.log", "two\n")
    repo.git("commit", "-qam", "changed")
    done = repo.run()
    assert done.returncode != 0
    assert "kept.log" in done.stderr


def test_check_ignore_error_is_refused(repo: Repo) -> None:
    repo.fail_subcommand("check-ignore")
    done = repo.run()
    assert done.returncode != 0
    assert "check-ignore failed" in done.stderr


def test_refused_paths_error_on_a_bad_revision(repo: Repo) -> None:
    done = repo.run(base="no-such-revision")
    assert done.returncode != 0
    assert "git diff failed" in done.stderr


def test_refused_paths_error_on_ls_tree(repo: Repo) -> None:
    repo.fail_subcommand("ls-tree")
    done = repo.run()
    assert done.returncode != 0
    assert "git ls-tree failed" in done.stderr


@pytest.mark.req("RAG-12")
def test_a_tracked_symlink_is_refused(repo: Repo) -> None:
    """Row 5b: promote trusts the clone's data, and `git status` stays clean over a merged link."""
    (repo.path / "evals-link").symlink_to("/somewhere/a/session/can/write")
    repo.git("add", "evals-link")
    repo.git("commit", "-qm", "link")
    done = repo.run()
    assert done.returncode != 0
    assert "symlink" in done.stderr


@pytest.mark.req("RAG-12")
def test_a_file_changed_into_a_symlink_is_refused(repo: Repo) -> None:
    target = repo.path / "tracked.txt"
    target.write_text("x\n")
    repo.git("add", "tracked.txt")
    repo.git("commit", "-qm", "file")
    target.unlink()
    target.symlink_to("/somewhere/else")
    repo.git("add", "tracked.txt")
    repo.git("commit", "-qm", "now a link")
    assert repo.run().returncode != 0


@pytest.mark.req("AUTO-2")
def test_a_gitlink_anywhere_in_the_tree_is_refused(tmp_path: Path) -> None:
    """A gitlink makes `git status` run a child git in that folder, with that folder's config."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sh_env = shared.tool_env(tmp_path, bin_dir)
    work = tmp_path / "repo"
    work.mkdir()
    shared.git(work, sh_env, "init", "-q", "-b", "main")
    (work / "a.txt").write_text("a\n")
    shared.git(work, sh_env, "add", "-A")
    shared.git(work, sh_env, "commit", "-q", "-m", "base")
    head = shared.git(work, sh_env, "rev-parse", "HEAD")
    shared.git(work, sh_env, "update-index", "--add", "--cacheinfo", f"{GITLINK},{head},sub")
    shared.git(work, sh_env, "commit", "-q", "-m", "gitlink")
    done = subprocess.run(  # noqa: S603
        ["/bin/bash", str(SCRIPT), head, "HEAD"],
        cwd=work,
        env=dict(sh_env),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0
    assert "gitlink" in done.stderr
