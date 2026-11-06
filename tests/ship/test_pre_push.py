"""AUTO-1: tools/pre-push.proposed.sh, run two ways.

Directly, with the lines git sends on stdin (`<local ref> <local sha> <remote ref> <remote sha>`),
and installed as `.git/hooks/pre-push` in a real clone with a real `git push` to a bare origin.
The script is copied alone into an empty directory (or the hooks directory of a repo that has no
tools/), so it also has to be self-contained. Commits are made with plumbing in the bare origin
and fetched into the clone, so case-variant paths stay distinct on a case-insensitive filesystem.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("AUTO-1")

_spec = importlib.util.spec_from_file_location("ship_shared", Path(__file__).with_name("shared.py"))
assert _spec is not None and _spec.loader is not None
shared = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("ship_shared", shared)
_spec.loader.exec_module(shared)

REPO: Path = shared.REPO
REFUSED_PATHS: list[str] = shared.REFUSED_PATHS
ZERO = "0" * 40
SCRIPT = REPO / "tools" / "pre-push.proposed.sh"
URL = "file:///origin.git"

EXTRA_REFUSED = [".github/workflows/ci.yml", ".github/x", ".GitHub/x"]
IGNORED = ["a.log", "sub/b.log", "ignored-dir/x"]
BASE_FILES = {
    "README": "one\n",
    ".gitignore": "*.log\nignored-dir/\n",
    "Makefile": "all:\n",
    "docs/x.md": "docs\n",
}


@dataclass
class Hook:
    root: Path
    origin: Path
    clone: Path
    sh_env: dict[str, str]
    base: str
    standalone: Path

    def git(self, cwd: Path, *args: str) -> str:
        return str(shared.git(cwd, self.sh_env, *args))

    def commit(
        self,
        changes: dict[str, str | None],
        parent: str | None = None,
        links: dict[str, str] | None = None,
    ) -> str:
        sha = str(
            shared.make_commit(self.origin, self.sh_env, parent or self.base, changes, links=links)
        )
        ref = f"refs/tmp/{sha}"
        self.git(self.origin, "update-ref", ref, sha)
        self.git(self.clone, "fetch", "-q", "origin", f"{ref}:{ref}")
        return sha

    def remote_branch(self, sha: str | None, name: str = "feat") -> None:
        if sha is None:
            self.git(self.origin, "update-ref", "-d", f"refs/heads/{name}")
        else:
            self.git(self.origin, "update-ref", f"refs/heads/{name}", sha)

    def remote_sha(self, name: str = "feat") -> str | None:
        probe = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--verify", "-q", f"refs/heads/{name}"],  # noqa: S607
            cwd=self.origin,
            capture_output=True,
            text=True,
            env=self.sh_env,
            check=False,
        )
        return probe.stdout.strip() or None

    def env_with(self, allow: str | None) -> dict[str, str]:
        return self.sh_env if allow is None else {**self.sh_env, "NW_ALLOW_CONTROL": allow}

    def direct(
        self, lines: list[str], allow: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [str(self.standalone), "origin", URL],
            cwd=self.clone,
            capture_output=True,
            text=True,
            check=False,
            env=self.env_with(allow),
            input="".join(f"{line}\n" for line in lines),
            timeout=60,
        )

    def push(self, *refspecs: str, allow: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["git", "push", "-q", "origin", *refspecs],  # noqa: S607
            cwd=self.clone,
            capture_output=True,
            text=True,
            check=False,
            env=self.env_with(allow),
            timeout=60,
        )


def build(tmp_path: Path, gitignore: bool = True) -> Hook:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sh_env = shared.tool_env(tmp_path, bin_dir)
    seed = tmp_path / "seed"
    seed.mkdir()
    for name, text in BASE_FILES.items():
        if name == ".gitignore" and not gitignore:
            continue
        target = seed / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    for args in (("init", "-q", "-b", "main"), ("add", "."), ("commit", "-q", "-m", "base")):
        shared.git(seed, sh_env, *args)
    origin = tmp_path / "origin.git"
    shared.git(tmp_path, sh_env, "clone", "-q", "--bare", str(seed), str(origin))
    clone = tmp_path / "clone"
    shared.git(tmp_path, sh_env, "clone", "-q", str(origin), str(clone))
    base = shared.git(clone, sh_env, "rev-parse", "origin/main")
    # the same file, twice: alone in an empty directory, and as the clone's pre-push hook
    alone = tmp_path / "hookdir"
    alone.mkdir()
    standalone = alone / "pre-push"
    shutil.copy(SCRIPT, standalone)
    standalone.chmod(0o755)
    installed = clone / ".git" / "hooks" / "pre-push"
    shutil.copy(SCRIPT, installed)
    installed.chmod(0o755)
    return Hook(tmp_path, origin, clone, sh_env, base, standalone)


@pytest.fixture
def hook(tmp_path: Path) -> Hook:
    return build(tmp_path)


@pytest.fixture(params=["direct", "installed"])
def mode(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def attempt(
    hook: Hook,
    mode: str,
    local: str,
    remote: str | None = None,
    allow: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Push `local` to refs/heads/feat, which is at `remote` (or does not exist yet)."""
    hook.remote_branch(remote)
    if mode == "direct":
        line = f"refs/heads/feat {local} refs/heads/feat {remote or ZERO}"
        return hook.direct([line], allow)
    return hook.push(f"{local}:refs/heads/feat", allow=allow)


def refused(hook: Hook, done: subprocess.CompletedProcess[str], before: str | None = None) -> None:
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert hook.remote_sha() == before, "the ref moved although the hook refused"


def passed(done: subprocess.CompletedProcess[str]) -> None:
    assert done.returncode == 0, done.stdout + done.stderr


# -- what it refuses ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [*REFUSED_PATHS, *EXTRA_REFUSED])
def test_refuses_the_path_and_names_it(hook: Hook, mode: str, path: str) -> None:
    done = attempt(hook, mode, hook.commit({path: "x\n"}))
    refused(hook, done)
    assert path in done.stdout + done.stderr


@pytest.mark.req("RAG-12")
def test_refuses_a_tracked_symlink_and_names_it(hook: Hook, mode: str) -> None:
    """Row 5b, in step with tools/refused-paths.sh: a link checks out clean, so name it here."""
    path = shared.NOT_REFUSED_PATHS[0]  # passes as a plain file, so only the mode can refuse it
    passed(attempt(hook, mode, hook.commit({path: "x\n"})))
    hook.remote_branch(None)
    done = attempt(hook, mode, hook.commit({}, links={path: "/elsewhere"}))
    refused(hook, done)
    assert path in done.stdout + done.stderr


@pytest.mark.parametrize("path", shared.NON_ASCII_PATHS)
def test_refuses_a_path_outside_printable_ascii(hook: Hook, mode: str, path: str) -> None:
    refused(hook, attempt(hook, mode, hook.commit({path: "x\n"})))


@pytest.mark.parametrize("path", shared.NOT_REFUSED_PATHS)
def test_push_does_not_refuse_the_path(hook: Hook, mode: str, path: str) -> None:
    passed(attempt(hook, mode, hook.commit({path: "x\n"})))


def test_check_ignore_error_is_refused(hook: Hook) -> None:
    local = hook.commit({"docs/y.md": "y\n"})
    real = shutil.which("git")
    shim = hook.root / "bin" / "git"
    shim.write_text(
        f'#!/bin/bash\nfor a in "$@"; do [ "$a" = check-ignore ] && exit 128; done\n'
        f'exec {real} "$@"\n'
    )
    shim.chmod(0o755)
    done = attempt(hook, "direct", local)
    refused(hook, done)
    assert "check-ignore failed" in done.stderr


@pytest.mark.parametrize("path", IGNORED)
def test_refuses_a_path_main_ignores(hook: Hook, mode: str, path: str) -> None:
    done = attempt(hook, mode, hook.commit({path: "x\n"}))
    refused(hook, done)
    assert path in done.stdout + done.stderr


def test_a_clean_push_passes(hook: Hook, mode: str) -> None:
    sha = hook.commit({"docs/y.md": "y\n"})
    passed(attempt(hook, mode, sha))
    if mode == "installed":
        assert hook.remote_sha() == sha


def test_a_worktree_gitignore_cannot_unignore_a_path(hook: Hook, mode: str) -> None:
    (hook.clone / ".gitignore").write_text("!*.log\n")
    done = attempt(hook, mode, hook.commit({"a.log": "x\n"}))
    refused(hook, done)


def test_a_worktree_only_ignore_pattern_does_not_refuse(hook: Hook, mode: str) -> None:
    (hook.clone / ".gitignore").write_text("*.log\nignored-dir/\nonly-local/\n")
    passed(attempt(hook, mode, hook.commit({"only-local/x": "x\n"})))


def test_an_escape_in_a_path_prints_as_caret_bracket(hook: Hook, mode: str) -> None:
    done = attempt(hook, mode, hook.commit({".github/x\x1b[31m": "x\n"}))
    refused(hook, done)
    out = done.stdout + done.stderr
    assert "\x1b" not in out
    assert "^[" in out


# -- which changes count -----------------------------------------------------------------------


def test_an_existing_branch_is_diffed_against_the_remote_sha(hook: Hook, mode: str) -> None:
    tip = hook.commit({".github/x": "x\n"})
    refused(hook, attempt(hook, mode, tip, remote=hook.base), before=hook.base)


def test_a_control_path_already_on_the_remote_branch_does_not_refuse_a_clean_push(
    hook: Hook, mode: str
) -> None:
    on_remote = hook.commit({".github/x": "x\n"})
    later = hook.commit({"docs/y.md": "y\n"}, parent=on_remote)
    passed(attempt(hook, mode, later, remote=on_remote))


def test_a_new_branch_is_diffed_against_origin_main(hook: Hook, mode: str) -> None:
    first = hook.commit({".github/x": "x\n"})
    second = hook.commit({"docs/y.md": "y\n"}, parent=first)  # clean on its own
    refused(hook, attempt(hook, mode, second))


def test_a_new_clean_branch_passes(hook: Hook, mode: str) -> None:
    first = hook.commit({"docs/y.md": "y\n"})
    second = hook.commit({"docs/z.md": "z\n"}, parent=first)
    passed(attempt(hook, mode, second))


def test_deleting_a_remote_branch_passes(hook: Hook, mode: str) -> None:
    on_remote = hook.commit({".github/x": "x\n"})
    hook.remote_branch(on_remote)
    if mode == "direct":
        done = hook.direct([f"(delete) {ZERO} refs/heads/feat {on_remote}"])
    else:
        done = hook.push(":feat")
    passed(done)
    if mode == "installed":
        assert hook.remote_sha() is None


# -- the override -----------------------------------------------------------------------------


def test_nw_allow_control_one_lets_a_control_path_through(hook: Hook, mode: str) -> None:
    sha = hook.commit({".github/x": "x\n"})
    passed(attempt(hook, mode, sha, allow="1"))


@pytest.mark.parametrize("value", ["0", "", "true", "yes", "11"])
def test_any_other_value_does_not_override(hook: Hook, mode: str, value: str) -> None:
    sha = hook.commit({".github/x": "x\n"})
    refused(hook, attempt(hook, mode, sha, allow=value))


# -- fails closed -----------------------------------------------------------------------------


def test_origin_without_main_is_refused(hook: Hook) -> None:
    sha = hook.commit({"docs/y.md": "y\n"})
    hook.git(hook.origin, "update-ref", "-d", "refs/heads/main")
    done = attempt(hook, "direct", sha)
    refused(hook, done)
    assert "origin has no main" in done.stderr


def test_a_main_not_fetched_yet_is_refused(hook: Hook) -> None:
    sha = hook.commit({"docs/y.md": "y\n"})
    newer = str(shared.make_commit(hook.origin, hook.sh_env, hook.base, {"docs/z.md": "z\n"}))
    hook.git(hook.origin, "update-ref", "refs/heads/main", newer)
    done = attempt(hook, "direct", sha)
    refused(hook, done)
    assert "git fetch origin" in done.stderr


# -- what the sandbox can write in .git must not steer it --------------------------------------


def test_a_moved_origin_main_cannot_hide_a_new_branch(hook: Hook, mode: str) -> None:
    sha = hook.commit({".github/workflows/x.yml": "x\n"})
    hook.git(hook.clone, "update-ref", "refs/remotes/origin/main", sha)
    refused(hook, attempt(hook, mode, sha))


def test_a_shadowing_tag_cannot_stand_in_for_main(hook: Hook, mode: str) -> None:
    sha = hook.commit({".github/workflows/x.yml": "x\n"})
    hook.git(hook.clone, "update-ref", "refs/tags/origin/main", sha)
    refused(hook, attempt(hook, mode, sha))


def test_a_moved_origin_main_cannot_supply_the_gitignore(hook: Hook, mode: str) -> None:
    blank = hook.commit({".gitignore": "\n"})
    hook.git(hook.clone, "update-ref", "refs/remotes/origin/main", blank)
    refused(hook, attempt(hook, mode, hook.commit({"a.log": "x\n"})))


def test_a_replace_ref_cannot_show_a_clean_tree(hook: Hook) -> None:
    bad = hook.commit({".github/workflows/x.yml": "x\n"})
    clean = hook.commit({"docs/y.md": "y\n"})
    hook.git(hook.clone, "replace", bad, clean)
    refused(hook, attempt(hook, "direct", bad))


def test_a_graft_cannot_hide_a_commit(hook: Hook) -> None:
    # Grafted onto the pushed commit, main would descend from it and the diff would be empty.
    bad = hook.commit({".github/workflows/x.yml": "x\n"})
    info = hook.clone / ".git" / "info"
    info.mkdir(exist_ok=True)
    (info / "grafts").write_text(f"{bad}\n{hook.base} {bad}\n")
    refused(hook, attempt(hook, "direct", bad))


@pytest.mark.parametrize("path", shared.NON_ASCII_PATHS)
def test_a_utf8_terminal_does_not_let_a_non_ascii_path_through(hook: Hook, path: str) -> None:
    hook.sh_env.update(shared.UTF8_LOCALE)
    refused(hook, attempt(hook, "direct", hook.commit({path: "x\n"})))


def test_a_shallow_clone_is_refused(hook: Hook) -> None:
    sha = hook.commit({"docs/y.md": "y\n"})
    (hook.clone / ".git" / "shallow").write_text(f"{hook.base}\n")
    done = attempt(hook, "direct", sha)
    refused(hook, done)
    assert "shallow" in done.stderr


def test_gitignore_absent_on_origin_main_is_refused(tmp_path: Path, mode: str) -> None:
    bare = build(tmp_path, gitignore=False)
    sha = bare.commit({"docs/y.md": "y\n"})
    refused(bare, attempt(bare, mode, sha))


def test_a_remote_sha_unknown_locally_is_refused(hook: Hook) -> None:
    sha = hook.commit({"docs/y.md": "y\n"})
    done = hook.direct([f"refs/heads/feat {sha} refs/heads/feat {'1' * 40}"])
    assert done.returncode != 0, done.stdout + done.stderr


# -- several refs in one push ------------------------------------------------------------------


@pytest.mark.parametrize("bad_first", [True, False])
def test_one_bad_ref_refuses_the_push(hook: Hook, mode: str, bad_first: bool) -> None:
    clean = hook.commit({"docs/y.md": "y\n"})
    bad = hook.commit({".github/x": "x\n"})
    pairs = [("bad", bad), ("clean", clean)] if bad_first else [("clean", clean), ("bad", bad)]
    if mode == "direct":
        lines = [f"refs/heads/{n} {sha} refs/heads/{n} {ZERO}" for n, sha in pairs]
        done = hook.direct(lines)
    else:
        done = hook.push(*[f"{sha}:refs/heads/{n}" for n, sha in pairs])
    assert done.returncode != 0, done.stdout + done.stderr
    assert hook.remote_sha("bad") is None


def test_several_clean_refs_pass(hook: Hook, mode: str) -> None:
    one = hook.commit({"docs/y.md": "y\n"})
    two = hook.commit({"docs/z.md": "z\n"})
    if mode == "direct":
        done = hook.direct(
            [f"refs/heads/{n} {sha} refs/heads/{n} {ZERO}" for n, sha in (("a", one), ("b", two))]
        )
    else:
        done = hook.push(f"{one}:refs/heads/a", f"{two}:refs/heads/b")
    passed(done)


# -- self-contained --------------------------------------------------------------------------


def test_the_script_needs_no_other_file(hook: Hook) -> None:
    """Alone in an empty directory, run from a repo with no tools/, it refuses and it passes."""
    assert not (hook.clone / "tools").exists()
    assert [p.name for p in hook.standalone.parent.iterdir()] == ["pre-push"]
    clean = hook.commit({"docs/y.md": "y\n"})
    bad = hook.commit({".github/x": "x\n"})
    passed(hook.direct([f"refs/heads/feat {clean} refs/heads/feat {ZERO}"]))
    assert hook.direct([f"refs/heads/feat {bad} refs/heads/feat {ZERO}"]).returncode != 0
