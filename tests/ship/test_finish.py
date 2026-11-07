"""AUTO-2, item 2: `make finish`, driven end to end on the rig in test_ship.py.

A real bare origin, a real runner clone and a real checkout; `gh` is the fake from test_ship.py.
These prove how `finish` reacts, not what real `gh` returns.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.req("AUTO-2"), pytest.mark.ship]

_spec = importlib.util.spec_from_file_location("ship_rig", Path(__file__).with_name("test_ship.py"))
assert _spec is not None and _spec.loader is not None
ship_rig = importlib.util.module_from_spec(_spec)
sys.modules["ship_rig"] = ship_rig
_spec.loader.exec_module(ship_rig)

rig = ship_rig.rig  # the fixture
Rig = ship_rig.Rig
PR: str = ship_rig.PR
BRANCH = "req/AUTO-2-finish-and-speed"
BODY = "docs/reviews/pr.md"
BODY_TEXT = "The body the reviewers read.\n"


@dataclass
class Row:
    rig: Rig
    checkout: Path
    reviewed: str
    body: str = BODY

    def git(self, *args: str) -> str:
        return str(self.rig.git(*args, cwd=self.checkout))

    def write(self, name: str, text: str) -> None:
        target = self.checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit_all(self, message: str = "more") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def finish(
        self,
        answers: list[str] | None = None,
        checkout: str | None = None,
        sha: str | None = None,
        body: str | None = None,
        runner_dir: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.rig.write_scenario()
        return ship_rig.interact(
            [
                "make",
                "-s",
                "finish",
                f"CHECKOUT={self.checkout if checkout is None else checkout}",
                f"SHA={self.reviewed if sha is None else sha}",
                f"BODY={self.body if body is None else body}",
                f"RUNNER_DIR={runner_dir or self.rig.clone}",
            ],
            self.rig.clone,
            self.rig.sh_env,
            answers=answers,
        )

    def creates(self) -> list[str]:
        return [c for c in self.rig.gh_calls() if c.startswith("pr create")]

    def label_adds(self) -> list[str]:
        return [c for c in self.rig.gh_calls() if c.startswith("pr edit")]

    def on_origin(self) -> bool:
        heads = self.rig.git("for-each-ref", "--format=%(refname)", "refs/heads")
        return f"refs/heads/{BRANCH}" in heads.split()

    def commits(self) -> int:
        return int(self.git("rev-list", "--count", f"{self.reviewed}..HEAD"))


def make_row(rig: Rig, changes: dict[str, str] | None = None) -> Row:
    checkout = rig.root / "Projects" / "northwind-shop"  # under the rig's $HOME
    checkout.parent.mkdir(exist_ok=True)
    done = ship_rig.run(
        rig.root, "git", "clone", "-q", str(rig.origin), str(checkout), sh_env=rig.sh_env
    )
    done.check_returncode()
    row = Row(rig, checkout, "")
    row.git("switch", "-q", "--no-track", "-c", BRANCH, "origin/main")
    row.write(BODY, BODY_TEXT)
    for name, text in (changes or {"docs/x.md": "the row's work\n"}).items():
        row.write(name, text)
    row.reviewed = row.commit_all("the row")
    row.write("PROGRESS.md", "written by /req-done\n")
    row.write("COVERAGE.md", "written by /req-done\n")
    return row


@pytest.fixture
def row(rig: Rig) -> Row:
    return make_row(rig)


def assert_stopped_before_any_change(
    row: Row, done: subprocess.CompletedProcess[str], why: str
) -> None:
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert why in out, out
    assert row.commits() == 0, "it committed before it refused"
    assert not row.on_origin(), "it pushed before it refused"
    assert row.creates() == []
    assert row.rig.merge_calls() == []


# -- the plain path ---------------------------------------------------------------------------


def test_a_row_goes_from_req_done_to_merged(row: Row) -> None:
    done = row.finish()
    assert done.returncode == 0, done.stdout
    assert row.on_origin()
    assert len(row.creates()) == 1
    assert len(row.rig.merge_calls()) == 1
    assert f"ship: PR #{PR} merged" in done.stdout


def test_the_commit_holds_only_the_two_files_and_a_fixed_subject(row: Row) -> None:
    row.finish()
    assert row.commits() == 1
    assert row.git("log", "-1", "--format=%s") == "AUTO-2: req-done"
    names = row.git("show", "--name-only", "--format=", "HEAD").split()
    assert sorted(names) == ["COVERAGE.md", "PROGRESS.md"]


def test_the_pr_body_is_the_committed_file_and_the_title_comes_from_the_branch(row: Row) -> None:
    row.finish()
    assert (row.rig.scn / "created_body").read_text() == BODY_TEXT
    assert "--title AUTO-2: finish and speed" in row.creates()[0]
    assert "--base main" in row.creates()[0]


def test_ship_still_asks(row: Row) -> None:
    done = row.finish(answers=["n"])
    assert done.returncode != 0
    assert "[y/N]" in done.stdout or "Type the PR number" in done.stdout
    assert row.rig.merge_calls() == []


def test_a_control_surface_row_still_needs_the_pr_number(rig: Rig) -> None:
    row = make_row(rig, {"CLAUDE.md": "changed\n"})
    done = row.finish(answers=["y"])
    assert "Type the PR number" in done.stdout
    assert row.rig.merge_calls() == []


def test_with_nothing_for_req_done_to_commit_it_still_ships(rig: Rig) -> None:
    row = make_row(rig)
    row.git("checkout", "--", "PROGRESS.md", "COVERAGE.md")
    assert row.finish().returncode == 0
    assert row.commits() == 0
    assert len(row.rig.merge_calls()) == 1


# -- a rerun ----------------------------------------------------------------------------------


def test_a_rerun_uses_the_open_pr_and_makes_no_second_commit(row: Row) -> None:
    assert row.finish(answers=["n"]).returncode != 0
    row.rig.open_prs = [int(PR)]
    done = row.finish()
    assert done.returncode == 0, done.stdout
    assert row.commits() == 1
    assert len(row.creates()) == 0  # the scenario was reset, so this counts the rerun only
    assert f"PR #{PR} is already open" in done.stdout
    assert len(row.rig.merge_calls()) == 1


def test_two_open_prs_for_the_branch_are_refused(row: Row) -> None:
    row.rig.open_prs = [7, 8]
    done = row.finish()
    assert done.returncode != 0
    assert "more than one open PR" in done.stdout
    assert row.rig.merge_calls() == []


# -- refusals before anything changes ---------------------------------------------------------


@pytest.mark.parametrize(
    ("checkout", "why"),
    [
        ("relative/path", "CHECKOUT"),
        ("", "CHECKOUT"),
        ("/x/a b", "CHECKOUT"),
        ("/x/../etc", "CHECKOUT"),
        ("/x/$(id)", "CHECKOUT"),
        ("/no/such/checkout", "no such checkout"),
    ],
)
def test_a_bad_checkout_is_refused(row: Row, checkout: str, why: str) -> None:
    assert_stopped_before_any_change(row, row.finish(checkout=checkout), why)


@pytest.mark.parametrize("sha", ["", "abc", "A" * 40, "x; id"])
def test_a_bad_sha_is_refused(row: Row, sha: str) -> None:
    assert_stopped_before_any_change(row, row.finish(sha=sha), "SHA")


@pytest.mark.parametrize(
    "body",
    [
        "",
        "PROGRESS.md",
        "docs/x.md",
        "docs/reviews/../../x.md",
        "docs/reviews/a/b.md",
        "/etc/passwd",
    ],
)
def test_a_body_outside_docs_reviews_is_refused(row: Row, body: str) -> None:
    assert_stopped_before_any_change(row, row.finish(body=body), "BODY")


def test_a_body_missing_at_the_reviewed_commit_is_refused(row: Row) -> None:
    done = row.finish(body="docs/reviews/none.md")
    assert_stopped_before_any_change(row, done, "at the reviewed commit")


def test_an_empty_body_is_refused(rig: Rig) -> None:
    row = make_row(rig, {"docs/reviews/empty.md": ""})
    assert_stopped_before_any_change(row, row.finish(body="docs/reviews/empty.md"), "is empty")


@pytest.mark.parametrize(
    "branch", ["main", "feature/x", "req/lower-case", "settings/x-install", "req/AUTO-2-A_b"]
)
def test_only_a_row_branch_can_finish(row: Row, branch: str) -> None:
    row.git("branch", "-M", branch) if branch != "main" else row.git("switch", "-q", "main")
    if branch == "main":
        row.git("checkout", "--", ".")
    done = row.finish(sha=row.git("rev-parse", "HEAD"))
    out = done.stdout
    assert done.returncode != 0
    assert "not a row branch" in out
    assert row.creates() == []


def test_a_detached_head_is_refused(row: Row) -> None:
    row.git("checkout", "--", "PROGRESS.md", "COVERAGE.md")
    row.git("switch", "-q", "--detach")
    done = row.finish()
    assert done.returncode != 0
    assert "not on a branch" in done.stdout
    assert row.creates() == []


def test_an_uncommitted_change_to_anything_else_is_refused(row: Row) -> None:
    row.write("docs/x.md", "after the review\n")
    assert_stopped_before_any_change(row, row.finish(), "not committed: docs/x.md")


def test_an_untracked_file_is_refused(row: Row) -> None:
    row.write("docs/new.md", "new\n")
    assert_stopped_before_any_change(row, row.finish(), "not committed: docs/new.md")


def test_a_deleted_progress_file_is_refused(row: Row) -> None:
    (row.checkout / "PROGRESS.md").unlink()
    assert_stopped_before_any_change(row, row.finish(), "unexpected state")


def test_a_reviewed_commit_not_on_the_branch_is_refused(row: Row) -> None:
    other = row.rig.commit(changes={"docs/other.md": "x\n"})
    row.git("fetch", "-q", "origin", other)
    assert_stopped_before_any_change(row, row.finish(sha=other), "not on this branch")


def test_a_commit_after_the_review_touching_more_than_the_docs_is_refused(rig: Rig) -> None:
    row = make_row(rig)
    row.git("checkout", "--", "PROGRESS.md", "COVERAGE.md")
    row.write("docs/x.md", "after the review\n")
    row.commit_all()
    done = row.finish()
    assert done.returncode != 0
    assert "changed since the reviewed commit: docs/x.md" in done.stdout
    assert not row.on_origin()
    assert row.creates() == []


def test_an_escape_in_a_path_is_shown_escaped(row: Row) -> None:
    row.write("docs/a\x1b[31m.md", "x\n")
    done = row.finish()
    assert done.returncode != 0
    assert "\x1b" not in done.stdout
    assert "^[" in done.stdout


@pytest.fixture
def open_window(rig: Rig) -> Iterator[int]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        port = int(listener.getsockname()[1])
        rig.sh_env["LLM_RUNNER_PORT"] = str(port)
        yield port


def test_an_open_window_is_refused(row: Row, open_window: int) -> None:
    assert_stopped_before_any_change(row, row.finish(), "spending window")


def test_it_refuses_outside_the_clone(row: Row, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert_stopped_before_any_change(row, row.finish(runner_dir=elsewhere), "only in the clone")


# -- security finding 1: nothing in the checkout may run --------------------------------------


def plant_gitlink(row: Row, marker: Path, commit: bool = False) -> None:
    """A gitlink over a folder whose own config names a program: `git status` runs it."""
    planted = row.rig.root / "planted.sh"
    planted.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    planted.chmod(0o755)
    row.write("sub/.git", "gitdir: ../gd\n")
    row.write("gd/HEAD", "ref: refs/heads/m\n")
    row.write("gd/refs/.keep", "")
    row.write("gd/objects/.keep", "")
    row.write(
        "gd/config",
        f"[core]\n\trepositoryformatversion = 0\n\tbare = false\n\tfsmonitor = {planted}\n",
    )
    head = row.git("rev-parse", "HEAD")
    row.git("update-index", "--add", "--cacheinfo", f"160000,{head},sub")
    if commit:
        row.git("commit", "-q", "-m", "a gitlink")
        row.reviewed = row.git("rev-parse", "HEAD")


@pytest.mark.parametrize("commit", [False, True], ids=["staged", "committed"])
def test_a_planted_gitlink_runs_nothing_and_is_refused(row: Row, commit: bool) -> None:
    marker = row.rig.root / "MARKER"
    plant_gitlink(row, marker, commit)
    done = row.finish()
    assert done.returncode != 0
    assert "gitlink" in done.stdout
    assert not marker.exists(), "planted code ran outside the sandbox"
    assert row.creates() == []


def test_a_gitlink_in_head_but_not_in_the_index_is_refused(row: Row) -> None:
    """Re-review finding A: the index alone is not enough, and `git commit` would descend."""
    marker = row.rig.root / "MARKER"
    plant_gitlink(row, marker, commit=True)
    row.git("update-index", "--force-remove", "sub")
    done = row.finish()
    assert done.returncode != 0
    assert "gitlink" in done.stdout
    assert not marker.exists()
    assert row.creates() == []


def test_a_gitlink_only_at_the_reviewed_commit_is_refused(row: Row) -> None:
    plant_gitlink(row, row.rig.root / "MARKER", commit=True)
    row.git("rm", "-q", "--cached", "sub")
    row.git("commit", "-q", "-m", "gone again")
    done = row.finish()
    assert done.returncode != 0
    assert "gitlink" in done.stdout
    assert row.creates() == []


@pytest.mark.parametrize("staged", [False, True], ids=["file", "index-only"])
def test_a_planted_gitmodules_is_refused(row: Row, staged: bool) -> None:
    """Measured: `ignore = none` in it beats `diff.ignoreSubmodules`, even from the index alone."""
    row.write(".gitmodules", '[submodule "sub"]\n\tpath = sub\n\turl = ./x\n\tignore = none\n')
    if staged:
        row.git("add", ".gitmodules")
        (row.checkout / ".gitmodules").unlink()
    done = row.finish()
    assert done.returncode != 0
    assert "gitmodules" in done.stdout or "not committed" in done.stdout
    assert row.creates() == []


def test_a_tracked_gitmodules_is_refused_at_real_repo_size(row: Row) -> None:
    """Second re-review, measured: `| grep -q` under `pipefail` missed it 5 times in 5. The match
    closes the pipe, `cut` dies of SIGPIPE, the pipeline returns 141, and `if` reads no match."""
    blob = row.git("rev-parse", "HEAD:docs/x.md")
    lines = [f"100644 {blob}\t.gitmodules"]
    lines += [f"100644 {blob}\tzz/some/long/path/name/file-{n}.txt" for n in range(20000)]
    subprocess.run(  # noqa: S603 - fixed arguments, throwaway repo
        ["git", "-C", str(row.checkout), "update-index", "--index-info"],  # noqa: S607
        input="\n".join(lines) + "\n",
        text=True,
        check=True,
        env=row.rig.sh_env,
    )
    done = row.finish()
    assert done.returncode != 0
    assert "a .gitmodules file is tracked" in done.stdout + done.stderr
    assert row.creates() == []


def test_a_docs_file_changed_both_staged_and_unstaged_is_refused(row: Row) -> None:
    """`/req-done` leaves plain edits. This state is how the nothing-to-commit path is reached."""
    row.git("add", "PROGRESS.md")
    row.write("PROGRESS.md", "progress\n")
    assert_stopped_before_any_change(row, row.finish(), "unexpected state of PROGRESS.md")


def test_the_commit_never_takes_the_nothing_to_commit_path() -> None:
    script = (ship_rig.REPO / "tools" / "finish.sh").read_text()
    assert "commit -q --allow-empty" in script


def test_the_script_sets_a_fixed_locale() -> None:
    script = (ship_rig.REPO / "tools" / "finish.sh").read_text()
    assert "export LC_ALL=C" in script


def test_the_commit_checked_is_the_commit_pushed() -> None:
    script = (ship_rig.REPO / "tools" / "finish.sh").read_text()
    assert 'merge-base --is-ancestor "$reviewed" "$head"' in script


def test_the_status_call_ignores_submodules_and_the_fsmonitor() -> None:
    script = (ship_rig.REPO / "tools" / "finish.sh").read_text()
    assert "--ignore-submodules=all" in script
    assert "core.fsmonitor=false" in script
    assert script.index("a gitlink is tracked") < script.index("status --porcelain")


# -- security finding 2: only a checkout the sandbox protects ---------------------------------


def worktree(row: Row, name: str = "northwind-shop-b") -> Path:
    path = row.rig.root / "Projects" / name
    row.git("checkout", "--", "PROGRESS.md", "COVERAGE.md")
    row.git("switch", "-q", "--detach")
    row.git("worktree", "add", "-q", str(path), BRANCH)
    return path


def admin_folder(row: Row, name: str = "northwind-shop-b") -> Path:
    return row.rig.root / "Projects" / "northwind-shop" / ".git" / "worktrees" / name


def planted_common(row: Row, marker: Path) -> Path:
    """A common directory whose config points at planted hooks: what a redirect would load."""
    common = row.checkout / ".git"
    fake = row.rig.root / "fake-common"
    (fake / "hooks").mkdir(parents=True)
    shutil.copytree(common / "objects", fake / "objects")
    shutil.copytree(common / "refs", fake / "refs")
    (fake / "config").write_text(
        f"[core]\n\trepositoryformatversion = 0\n\thooksPath = {fake / 'hooks'}\n"
    )
    for name in ("pre-commit", "post-commit", "pre-push"):
        hook = fake / "hooks" / name
        hook.write_text(f'#!/bin/sh\n: > "{marker}"\n')
        hook.chmod(0o755)
    return fake


def test_a_redirected_commondir_is_refused_before_any_hook_runs(row: Row) -> None:
    """Second re-review, measured: a planted hook ran through `finish`'s own flags."""
    marker = row.rig.root / "MARKER"
    fake = planted_common(row, marker)
    path = worktree(row)
    (admin_folder(row) / "commondir").write_text(f"{fake}\n")
    row.checkout = path
    row.write("PROGRESS.md", "written by /req-done\n")
    done = row.finish()
    assert done.returncode != 0
    assert "commondir" in done.stdout + done.stderr
    assert not marker.exists(), "planted code ran outside the sandbox"
    assert row.creates() == []


def test_a_gitdir_pointing_elsewhere_is_refused(row: Row) -> None:
    path = worktree(row)
    (admin_folder(row) / "gitdir").write_text("/somewhere/else/.git\n")
    row.checkout = path
    row.write("PROGRESS.md", "written by /req-done\n")
    done = row.finish()
    assert done.returncode != 0
    assert "gitdir" in done.stdout + done.stderr
    assert row.creates() == []


def test_a_config_worktree_in_the_admin_folder_is_refused(row: Row) -> None:
    path = worktree(row)
    (admin_folder(row) / "config.worktree").write_text("[core]\n")
    row.checkout = path
    row.write("PROGRESS.md", "written by /req-done\n")
    done = row.finish()
    assert done.returncode != 0
    assert "config.worktree" in done.stdout + done.stderr
    assert row.creates() == []


def test_an_admin_folder_that_is_a_symlink_is_refused(row: Row) -> None:
    path = worktree(row)
    admin = admin_folder(row)
    moved = row.rig.root / "moved-admin"
    admin.rename(moved)
    admin.symlink_to(moved)
    row.checkout = path
    row.write("PROGRESS.md", "written by /req-done\n")
    done = row.finish()
    assert done.returncode != 0
    assert "not a checkout" in done.stdout + done.stderr
    assert row.creates() == []


@pytest.mark.parametrize("name", ["commondir", "config.worktree"])
def test_the_main_checkout_may_not_hold_a_redirect(row: Row, name: str) -> None:
    """Measured: git honours a `commondir` file in a main checkout's `.git` too."""
    (row.checkout / ".git" / name).write_text("/somewhere/else\n")
    done = row.finish()
    assert done.returncode != 0
    assert name in done.stdout + done.stderr
    assert row.creates() == []


def test_git_is_told_its_directories_so_a_swap_after_the_check_changes_nothing() -> None:
    """Measured: with the three variables set, a redirected `commondir` file ran nothing."""
    script = (ship_rig.REPO / "tools" / "finish.sh").read_text()
    for name in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        assert f"{name}=" in script
    assert script.index("GIT_COMMON_DIR=") < script.index("in_checkout ls-files")


def test_a_linked_worktree_can_finish(row: Row) -> None:
    path = worktree(row)
    row.checkout = path
    row.write("PROGRESS.md", "written by /req-done\n")
    done = row.finish()
    assert done.returncode == 0, done.stdout
    assert len(row.rig.merge_calls()) == 1


@pytest.mark.parametrize(
    "name",
    [
        "northwind-shop-b/apps",
        "northwind-shop/sub",
        "other",
        "northwind-shopx",
        "northwind-shop-b/",
    ],
)
def test_only_a_top_level_checkout_is_accepted(row: Row, name: str) -> None:
    worktree(row)
    target = row.rig.root / "Projects" / name
    target.mkdir(parents=True, exist_ok=True)
    plain = str(target) if not name.endswith("/") else f"{target}/"
    done = row.finish(checkout=plain)
    assert done.returncode != 0
    assert "not a checkout" in done.stdout or name.endswith("/")
    assert row.creates() == []


def test_a_checkout_outside_projects_is_refused(row: Row) -> None:
    elsewhere = row.rig.root / "scratch"
    shutil.move(str(row.checkout), str(elsewhere))
    row.checkout = elsewhere
    done = row.finish()
    assert done.returncode != 0
    assert "not a checkout" in done.stdout
    assert row.creates() == []


def test_a_symlink_into_the_projects_folder_is_refused(row: Row) -> None:
    elsewhere = row.rig.root / "scratch"
    shutil.move(str(row.checkout), str(elsewhere))
    link = row.rig.root / "Projects" / "northwind-shop-c"
    link.symlink_to(elsewhere)
    done = row.finish(checkout=str(link))
    assert done.returncode != 0
    assert "not a checkout" in done.stdout
    assert row.creates() == []


def test_a_git_file_pointing_elsewhere_is_refused(row: Row) -> None:
    marker = row.rig.root / "MARKER"
    path = worktree(row)
    planted = row.rig.root / "planted.sh"
    planted.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    planted.chmod(0o755)
    other = row.rig.root / "other-gitdir"
    other.mkdir()
    (other / "HEAD").write_text("ref: refs/heads/m\n")
    (other / "objects").mkdir()
    (other / "refs").mkdir()
    (other / "config").write_text(f"[core]\n\tfsmonitor = {planted}\n")
    (path / ".git").write_text(f"gitdir: {other}\n")
    row.checkout = path
    done = row.finish()
    assert done.returncode != 0
    assert "not a checkout" in done.stdout
    assert not marker.exists()


def test_a_git_directory_in_a_linked_checkout_path_is_refused(row: Row) -> None:
    path = row.rig.root / "Projects" / "northwind-shop-c"
    shutil.move(str(row.checkout), str(path))
    row.checkout = path
    done = row.finish()
    assert done.returncode != 0
    assert "not a checkout" in done.stdout


# -- security finding 5: what is checked is what is pushed ------------------------------------


def test_a_replace_ref_does_not_hide_a_change_from_the_check(row: Row) -> None:
    row.git("checkout", "--", "PROGRESS.md", "COVERAGE.md")
    row.write("docs/x.md", "unreviewed\n")
    bad = row.commit_all("unreviewed")
    row.git("replace", bad, row.reviewed)
    done = row.finish()
    assert done.returncode != 0
    assert "changed since the reviewed commit" in done.stdout
    assert not row.on_origin()


def test_the_push_names_the_commit_not_head() -> None:
    script = (ship_rig.REPO / "tools" / "finish.sh").read_text()
    assert 'push origin "$head:refs/heads/$branch"' in script


# -- the push ---------------------------------------------------------------------------------


def hook(row: Row, script: str) -> Path:
    log = row.rig.root / "hook.log"
    path = row.checkout / ".git" / "hooks" / "pre-push"
    path.write_text(
        f'#!/bin/bash\necho "NW_ALLOW_CONTROL=[$NW_ALLOW_CONTROL]" >> "{log}"\n{script}\n'
    )
    path.chmod(0o755)
    return log


def test_the_hook_runs_and_finish_does_not_set_the_override(row: Row) -> None:
    log = hook(row, "exit 0")
    assert row.finish().returncode == 0
    assert log.read_text() == "NW_ALLOW_CONTROL=[]\n"


def test_a_hook_refusal_stops_before_the_pr(row: Row) -> None:
    hook(row, "echo pre-push: refused >&2; exit 1")
    done = row.finish()
    assert done.returncode != 0
    assert "NW_ALLOW_CONTROL=1" in done.stdout
    assert not row.on_origin()
    assert row.creates() == []


def test_the_override_passes_through_only_when_the_user_typed_it(row: Row) -> None:
    log = hook(row, '[[ "$NW_ALLOW_CONTROL" == 1 ]]')
    assert row.finish(answers=["n"]).returncode != 0
    assert row.creates() == []
    row.rig.sh_env["NW_ALLOW_CONTROL"] = "1"
    assert row.finish().returncode == 0
    assert log.read_text().splitlines()[-1] == "NW_ALLOW_CONTROL=[1]"


def test_a_diverged_branch_is_not_forced(row: Row) -> None:
    other = row.rig.commit(changes={"docs/other.md": "x\n"})
    row.rig.git("update-ref", f"refs/heads/{BRANCH}", other)
    done = row.finish()
    assert done.returncode != 0
    assert "the push failed" in done.stdout
    assert row.rig.git("rev-parse", f"refs/heads/{BRANCH}") == other
    assert row.creates() == []


def test_origin_ending_up_with_another_head_is_refused(row: Row) -> None:
    """A push that reports success must not be taken on trust: labels follow what origin holds."""
    other = row.rig.commit(changes={"docs/other.md": "x\n"})
    hook_file = row.rig.origin / "hooks" / "post-receive"
    hook_file.write_text(f"#!/bin/sh\ngit update-ref refs/heads/{BRANCH} {other}\n")
    hook_file.chmod(0o755)
    done = row.finish()
    assert done.returncode != 0
    assert "origin does not have the checkout's head" in done.stdout
    assert row.rig.merge_calls() == []


def test_more_than_one_merge_base_is_refused(row: Row) -> None:
    rig = row.rig
    tree = rig.git("rev-parse", "main^{tree}")
    left = rig.git("commit-tree", tree, "-p", "main", "-m", "left")
    right = rig.git("commit-tree", tree, "-p", "main", "-m", "right")
    new_main = rig.git("commit-tree", tree, "-p", left, "-p", right, "-m", "one way")
    other_way = rig.git("commit-tree", tree, "-p", right, "-p", left, "-m", "the other way")
    rig.git("update-ref", "refs/heads/main", new_main)
    row.git("fetch", "-q", "origin", other_way)
    row.git("reset", "-q", "--hard", other_way)
    row.reviewed = other_way
    row.write(BODY, BODY_TEXT)
    row.reviewed = row.commit_all("the row, again")
    done = row.finish()
    assert done.returncode != 0
    assert "no single merge base" in done.stdout
    assert "cannot read which labels" not in done.stdout
    assert row.rig.merge_calls() == []


def test_the_script_holds_no_force_and_no_override() -> None:
    script = (ship_rig.REPO / "tools" / "finish.sh").read_text()
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert "--force" not in code
    assert "NW_ALLOW_CONTROL=" not in code.replace("with NW_ALLOW_CONTROL=1 in front", "")
    assert "--add-label gate-change" not in code
    assert "--admin" not in code


# -- gh failures ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "why"),
    [
        ("list_fail", "gh pr list failed"),
        ("create_fail", "gh pr create failed"),
        ("create_no_url", "cannot read the PR number"),
    ],
)
def test_a_gh_failure_stops_before_the_merge(row: Row, flag: str, why: str) -> None:
    row.rig.flags.add(flag)
    done = row.finish()
    assert done.returncode != 0
    assert why in done.stdout
    assert row.rig.merge_calls() == []


def test_a_label_failure_stops_before_the_merge(rig: Rig) -> None:
    row = make_row(rig, {".github/workflows/ci.yml": "name: changed\n"})
    row.rig.flags.add("edit_fail")
    done = row.finish()
    assert done.returncode != 0
    assert "gh pr edit failed" in done.stdout
    assert row.rig.merge_calls() == []


# -- labels (decision 0011): ci-change only ---------------------------------------------------


def test_a_workflow_change_gets_ci_change_and_says_why(rig: Rig) -> None:
    row = make_row(rig, {".github/workflows/ci.yml": "name: changed\n"})
    done = row.finish()
    assert done.returncode == 0, done.stdout
    assert row.label_adds() == [f"pr edit {PR} --add-label ci-change"]
    assert "ci-change, because of .github/workflows/ci.yml" in done.stdout


def test_a_plain_row_gets_no_label(row: Row) -> None:
    row.finish()
    assert row.label_adds() == []


@pytest.mark.parametrize(
    "path",
    [
        "evals/baselines/a.json",
        "evals/fixtures/known_failures.json",
        "evals/fixtures/starter_set.jsonl",
    ],
)
def test_baseline_change_stays_by_hand(rig: Rig, path: str) -> None:
    row = make_row(rig, {path: "changed\n"})
    done = row.finish()
    assert done.returncode != 0
    assert row.label_adds() == []
    assert f"baseline-change needed, because of {path}" in done.stdout
    assert "yours to add" in done.stdout
    assert "baseline-change label" in done.stdout  # ship's refusal
    assert row.rig.merge_calls() == []


def test_with_the_users_baseline_label_it_ships(rig: Rig) -> None:
    row = make_row(rig, {"evals/baselines/a.json": "changed\n"})
    row.rig.labels("baseline-change")
    done = row.finish()
    assert done.returncode == 0, done.stdout
    assert row.label_adds() == []


def test_an_evals_src_change_gets_no_gate_change(rig: Rig) -> None:
    row = make_row(rig, {"evals/src/x.py": "x = 1\n"})
    assert row.finish().returncode == 0
    assert row.label_adds() == []


def test_main_gate_failing_is_refused_with_no_label_added(rig: Rig) -> None:
    row = make_row(rig, {"evals/src/x.py": "x = 1\n"})
    row.rig.checks = [
        *[c for c in ship_rig.passing_checks() if c["name"] != "eval-gate-main"],
        {"name": "eval-gate-main", "bucket": "fail", "workflow": "Guard"},
    ]
    done = row.finish()
    assert done.returncode != 0
    assert "eval-gate-main" in done.stdout
    assert row.label_adds() == []
    assert row.rig.merge_calls() == []


# -- ship.sh --labels -------------------------------------------------------------------------


def labels(rig: Rig, *args: str) -> subprocess.CompletedProcess[str]:
    return ship_rig.run(
        rig.clone, "/bin/bash", "tools/ship.sh", "--labels", *args, sh_env=rig.sh_env
    )


def fetched(rig: Rig) -> tuple[str, str]:
    rig.git("fetch", "-q", "origin", rig.head, cwd=rig.clone)
    return rig.base, rig.head


@pytest.mark.parametrize(
    ("path", "label"),
    [
        (".github/workflows/ci.yml", "ci-change"),
        ("evals/baselines/a.json", "baseline-change"),
        ("evals/fixtures/known_failures.json", "baseline-change"),
        ("evals/fixtures/starter_set.jsonl", "baseline-change"),
    ],
)
def test_the_labels_mode_names_the_label_and_the_path(rig: Rig, path: str, label: str) -> None:
    rig.pr({path: "changed\n"})
    done = labels(rig, *fetched(rig))
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"{label}\t{path}\n"


def test_the_labels_mode_says_nothing_for_a_plain_diff(rig: Rig) -> None:
    rig.pr({"docs/x.md": "changed\n", "evals/src/x.py": "x = 1\n"})
    assert labels(rig, *fetched(rig)).stdout == ""


def test_the_labels_mode_calls_no_gh_and_merges_nothing(rig: Rig) -> None:
    rig.write_scenario()
    rig.pr({".github/workflows/ci.yml": "changed\n"})
    labels(rig, *fetched(rig))
    assert rig.gh_calls() == []


def test_the_labels_mode_escapes_a_newline_in_a_path(rig: Rig) -> None:
    rig.pr({".github/workflows/a\nb.yml": "x\n"})
    out = labels(rig, *fetched(rig)).stdout
    assert out == "ci-change\t.github/workflows/a\\nb.yml\n"


@pytest.mark.parametrize(
    "args",
    [
        ("--output=/tmp/x", "refs/runner/ship-head"),
        ("a" * 40, "--output=/tmp/x"),
        ("nope", "refs/runner/ship-head"),
    ],
)
def test_the_labels_mode_takes_only_a_commit_and_the_ship_ref(
    rig: Rig, args: tuple[str, str]
) -> None:
    done = labels(rig, *args)
    assert done.returncode != 0
    assert "usage" in done.stderr


@pytest.mark.parametrize("args", [(), ("a",), ("a", "b", "c")])
def test_the_labels_mode_needs_exactly_two_refs(rig: Rig, args: tuple[str, ...]) -> None:
    done = labels(rig, *args)
    assert done.returncode != 0
    assert "usage" in done.stderr


def test_a_bad_ref_in_the_labels_mode_fails(rig: Rig) -> None:
    assert labels(rig, "nope", "nada").returncode != 0


def test_the_fake_view_tracks_the_pushed_head(row: Row) -> None:
    """Guards the rig: `ship` reads the head GitHub would report after the push."""
    row.finish(answers=["n"])
    view = json.loads((row.rig.scn / "view.json").read_text())
    assert view["headRefOid"] == row.git("rev-parse", "HEAD")


# -- AUTO-2, extra cases: redirects, shapes of the files, and a normal finish in both shapes ---


def linked_row(row: Row) -> Row:
    row.checkout = worktree(row)
    row.write("PROGRESS.md", "written by /req-done\n")
    return row


def refused_in_linked(row: Row, word: str) -> None:
    done = row.finish()
    assert done.returncode != 0
    assert word in done.stdout + done.stderr
    assert row.creates() == []
    assert not row.on_origin()
    assert row.rig.merge_calls() == []


@pytest.mark.parametrize(
    "text",
    ["../../\n", "../.\n", "..//..\n", "../..\n../..\n", "../.. \n", "", "/abs/elsewhere\n"],
    ids=["slash", "dot", "double-slash", "twice", "trailing-space", "empty", "absolute"],
)
def test_a_commondir_that_is_not_exactly_two_levels_up_is_refused(row: Row, text: str) -> None:
    linked_row(row)
    (admin_folder(row) / "commondir").write_text(text)
    refused_in_linked(row, "commondir")


def test_a_missing_commondir_is_refused(row: Row) -> None:
    linked_row(row)
    (admin_folder(row) / "commondir").unlink()
    refused_in_linked(row, "commondir")


def test_a_commondir_symlink_to_a_file_that_reads_right_is_refused(row: Row) -> None:
    linked_row(row)
    target = row.rig.root / "reads-right"
    target.write_text("../..\n")
    file = admin_folder(row) / "commondir"
    file.unlink()
    file.symlink_to(target)
    refused_in_linked(row, "commondir")


def test_a_dangling_commondir_symlink_is_refused(row: Row) -> None:
    linked_row(row)
    file = admin_folder(row) / "commondir"
    file.unlink()
    file.symlink_to(row.rig.root / "nowhere")
    refused_in_linked(row, "commondir")


def test_a_gitdir_symlink_to_a_file_that_reads_right_is_refused(row: Row) -> None:
    path = linked_row(row).checkout
    target = row.rig.root / "reads-right"
    target.write_text(f"{path.resolve()}/.git\n")
    file = admin_folder(row) / "gitdir"
    file.unlink()
    file.symlink_to(target)
    refused_in_linked(row, "gitdir")


def test_a_missing_gitdir_is_refused(row: Row) -> None:
    linked_row(row)
    (admin_folder(row) / "gitdir").unlink()
    refused_in_linked(row, "gitdir")


def test_a_gitdir_naming_another_checkout_is_refused(row: Row) -> None:
    linked_row(row)
    other = row.rig.root / "Projects" / "northwind-shop-x"
    (admin_folder(row) / "gitdir").write_text(f"{other}/.git\n")
    refused_in_linked(row, "gitdir")


def test_a_gitdir_naming_the_main_checkout_is_refused(row: Row) -> None:
    main = row.checkout
    linked_row(row)
    (admin_folder(row) / "gitdir").write_text(f"{main.resolve()}/.git\n")
    refused_in_linked(row, "gitdir")


def test_a_dangling_config_worktree_symlink_in_the_admin_folder_is_refused(row: Row) -> None:
    linked_row(row)
    (admin_folder(row) / "config.worktree").symlink_to(row.rig.root / "nowhere")
    refused_in_linked(row, "config.worktree")


def test_a_config_worktree_in_the_main_git_is_refused_for_a_linked_checkout(row: Row) -> None:
    main = row.checkout
    linked_row(row)
    (main / ".git" / "config.worktree").write_text("[core]\n")
    refused_in_linked(row, "config.worktree")


def test_a_commondir_in_the_main_git_is_refused_for_a_linked_checkout(row: Row) -> None:
    main = row.checkout
    linked_row(row)
    (main / ".git" / "commondir").write_text("/somewhere/else\n")
    refused_in_linked(row, "commondir")


def test_a_dangling_commondir_symlink_in_the_main_git_is_refused(row: Row) -> None:
    (row.checkout / ".git" / "commondir").symlink_to(row.rig.root / "nowhere")
    done = row.finish()
    assert done.returncode != 0
    assert "commondir" in done.stdout + done.stderr
    assert row.creates() == []


def test_a_planted_config_worktree_in_the_admin_folder_runs_nothing(row: Row) -> None:
    """The file only loads with `extensions.worktreeConfig`, so turn that on, then plant a hook."""
    marker = row.rig.root / "MARKER"
    hooks = row.rig.root / "planted-hooks"
    hooks.mkdir()
    for name in ("pre-commit", "post-commit", "pre-push"):
        (hooks / name).write_text(f'#!/bin/sh\n: > "{marker}"\n')
        (hooks / name).chmod(0o755)
    row.git("config", "extensions.worktreeConfig", "true")
    linked_row(row)
    (admin_folder(row) / "config.worktree").write_text(f"[core]\n\thooksPath = {hooks}\n")
    refused_in_linked(row, "config.worktree")
    assert not marker.exists(), "planted code ran outside the sandbox"


@pytest.mark.parametrize("where", ["docs/.gitmodules", ".gitmodules"])
@pytest.mark.parametrize("stage", ["index", "head", "reviewed"])
def test_a_tracked_gitmodules_is_refused_wherever_it_is_seen(
    row: Row, where: str, stage: str
) -> None:
    row.write(where, '[submodule "s"]\n\tpath = s\n\turl = ./x\n')
    row.git("add", where)
    if stage != "index":
        row.git("commit", "-q", "-m", "modules")
    if stage == "reviewed":
        row.reviewed = row.git("rev-parse", "HEAD")
    if stage != "index":
        row.git("rm", "-q", "--cached", where)
        (row.checkout / where).unlink()
        if stage == "reviewed":
            row.git("commit", "-q", "-m", "gone again")
    if stage == "index":
        (row.checkout / where).unlink()
    done = row.finish()
    assert done.returncode != 0
    assert "gitmodules" in done.stdout + done.stderr or "not committed" in done.stdout
    assert row.creates() == []
    assert not row.on_origin()


def test_a_tracked_gitmodules_in_a_linked_checkout_is_refused(row: Row) -> None:
    row.write(".gitmodules", '[submodule "s"]\n\tpath = s\n\turl = ./x\n\tignore = none\n')
    row.git("add", ".gitmodules")
    row.git("commit", "-q", "-m", "modules")
    row.reviewed = row.git("rev-parse", "HEAD")
    linked_row(row)
    refused_in_linked(row, "gitmodules")


def test_a_gitlink_in_a_linked_checkout_runs_nothing_and_is_refused(row: Row) -> None:
    marker = row.rig.root / "MARKER"
    plant_gitlink(row, marker, commit=True)
    path = worktree(row)
    row.checkout = path
    row.write("PROGRESS.md", "written by /req-done\n")
    done = row.finish()
    assert done.returncode != 0
    assert "gitlink" in done.stdout
    assert not marker.exists()
    assert row.creates() == []


def test_a_main_checkout_finish_commits_pushes_and_opens_the_pr(row: Row) -> None:
    done = row.finish()
    assert done.returncode == 0, done.stdout + done.stderr
    assert row.commits() == 1
    assert row.on_origin()
    assert len(row.creates()) == 1
    assert row.rig.git("rev-parse", f"refs/heads/{BRANCH}") == row.git("rev-parse", "HEAD")


def test_a_linked_worktree_finish_commits_pushes_and_opens_the_pr(row: Row) -> None:
    linked_row(row)
    done = row.finish()
    assert done.returncode == 0, done.stdout + done.stderr
    assert row.commits() == 1
    assert row.on_origin()
    assert len(row.creates()) == 1
    assert row.rig.git("rev-parse", f"refs/heads/{BRANCH}") == row.git("rev-parse", "HEAD")


def test_a_linked_worktree_finish_runs_the_main_checkouts_pre_push_hook(row: Row) -> None:
    log = hook(row, "exit 0")
    linked_row(row)
    assert row.finish().returncode == 0
    assert log.read_text() == "NW_ALLOW_CONTROL=[]\n"


def test_a_linked_worktree_hook_refusal_stops_before_the_pr(row: Row) -> None:
    hook(row, "echo pre-push: refused >&2; exit 1")
    linked_row(row)
    done = row.finish()
    assert done.returncode != 0
    assert "NW_ALLOW_CONTROL=1" in done.stdout
    assert not row.on_origin()
    assert row.creates() == []


# -- third read --------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["commondir", "gitdir"])
def test_a_fifo_in_the_admin_folder_is_refused_not_read(row: Row, name: str) -> None:
    """`$(cat fifo)` would block for ever."""
    path = worktree(row)
    file = admin_folder(row) / name
    file.unlink()
    os.mkfifo(file)
    row.checkout = path
    row.write("PROGRESS.md", "written by /req-done\n")
    done = row.finish()
    assert done.returncode != 0
    assert name in done.stdout + done.stderr
    assert row.creates() == []


def test_a_symlinked_main_checkout_is_refused_for_a_linked_worktree(row: Row) -> None:
    path = worktree(row)
    main = row.rig.root / "Projects" / "northwind-shop"
    moved = row.rig.root / "moved-main"
    main.rename(moved)
    main.symlink_to(moved)
    row.checkout = path
    row.write("PROGRESS.md", "written by /req-done\n")
    done = row.finish()
    assert done.returncode != 0
    assert "not a checkout" in done.stdout + done.stderr
    assert row.creates() == []


def test_a_commondir_swapped_after_the_check_changes_nothing(row: Row) -> None:
    """The pin's claim, run: a hook in the real common directory swaps it mid-run, as a process left
    behind by a session could. Without the pin, the push would then load the planted hooks."""
    marker = row.rig.root / "MARKER"
    fake = planted_common(row, marker)
    admin = admin_folder(row)
    swap = row.checkout / ".git" / "hooks" / "pre-commit"
    swap.write_text(f'#!/bin/sh\nprintf "%s\\n" "{fake}" > "{admin / "commondir"}"\n')
    swap.chmod(0o755)
    path = worktree(row)
    row.checkout = path
    row.write("PROGRESS.md", "written by /req-done\n")
    done = row.finish()
    assert (admin / "commondir").read_text() == f"{fake}\n", "the swap did not happen"
    assert not marker.exists(), f"planted code ran outside the sandbox: {done.stdout}"
    assert done.returncode == 0, done.stdout + done.stderr  # unpinned, git would see other refs
    assert len(row.rig.merge_calls()) == 1


def test_the_real_pre_push_hook_runs_under_the_pin_and_leaves_the_main_config_alone(
    row: Row,
) -> None:
    """Third read: every other hook here is a stub, so the pin had never met the real one."""
    common = row.checkout / ".git"
    hook = common / "hooks" / "pre-push"
    hook.write_text((ship_rig.REPO / "tools" / "pre-push.proposed.sh").read_text())
    hook.chmod(0o755)
    before = (common / "config").read_bytes()
    path = worktree(row)
    row.checkout = path
    row.write("PROGRESS.md", "written by /req-done\n")
    done = row.finish()
    assert done.returncode == 0, done.stdout + done.stderr
    assert (common / "config").read_bytes() == before
    assert len(row.rig.merge_calls()) == 1


def test_the_hook_clears_the_pin_before_its_empty_repo() -> None:
    script = (ship_rig.REPO / "tools" / "pre-push.proposed.sh").read_text()
    assert script.count("unset GIT_DIR GIT_COMMON_DIR GIT_WORK_TREE GIT_INDEX_FILE") == 2


def test_a_grep_failure_is_not_read_as_no_match() -> None:
    script = (ship_rig.REPO / "tools" / "finish.sh").read_text()
    assert script.count("|| [[ $? -eq 1 ]]") == 2
    assert "grep '^160000 ' \"$work/lines\" || true" not in script
