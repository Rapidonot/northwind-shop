"""Helpers shared by tests/ship/test_ship.py and tests/ship/test_pre_push.py.

Loaded by path (pytest runs with --import-mode=importlib, so sibling imports do not resolve).
Not collected: the name does not start with `test_`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NULL_SHA = "0" * 40

# The local env file's name is built, so that no source line holds it literally.
ENV_FILE = "." + "env"

# Paths that `ship`, `runner-sync` and the pre-push hook all refuse.
REFUSED_PATHS = [
    "GNUmakefile",
    "gnumakefile",
    "GNUMakefile",
    "makefile",
    "MAKEFILE",
    "sub/makefile",
    ENV_FILE,
    ENV_FILE + ".local",
    "sub/" + ENV_FILE,
    ENV_FILE.upper(),
    ".venv-runner/lib/python3.12/site-packages/x.pth",
    ".venv/x",
    ".VENV/x",
    "sub/.venv/x",
]

# Outside printable ASCII. The first two fold to `s` and `k` on case-insensitive APFS, so the first
# lands in services/ while every ASCII match sees another directory.
NON_ASCII_PATHS = [
    "ſervices/common/brotli.py",
    "tools/Key.py",
    "docs/café.md",
    "docs/a\x1bb.md",
    "docs/a\nb.md",
]

# What the user's terminal has. In a UTF-8 locale `[:print:]` can accept these characters.
UTF8_LOCALE = {"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}

# Paths that look close to the refused ones and must pass.
NOT_REFUSED_PATHS = ["Makefile", "sub/Makefile", "env.example", "docs/venv.md"]


def git(cwd: Path, sh_env: Mapping[str, str], *args: str, stdin: str = "") -> str:
    done = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=dict(sh_env),
        input=stdin,
    )
    return done.stdout.strip()


def make_commit(
    repo: Path,
    sh_env: Mapping[str, str],
    parent: str,
    changes: Mapping[str, str | None] | None = None,
    renames: Mapping[str, str] | None = None,
    bulk: int = 0,
    message: str = "change",
    links: Mapping[str, str] | None = None,
) -> str:
    """A commit on `parent` plus `changes` (None deletes a path), made with plumbing only.

    No working tree and no index outside `repo`, so a case-variant path stays a distinct path on a
    case-insensitive filesystem. Run it against a bare repo: no core.ignorecase to fold names.
    """
    index = repo / "plumbing.index"
    index.unlink(missing_ok=True)
    idx_env = {**sh_env, "GIT_INDEX_FILE": str(index)}
    subprocess.run(  # noqa: S603
        ["git", "read-tree", parent],  # noqa: S607
        cwd=repo,
        check=True,
        env=idx_env,
    )
    lines: list[str] = []
    for path, content in (changes or {}).items():
        if content is None:
            lines.append(f"0 {NULL_SHA}\t{path}\0")
            continue
        blob = git(repo, sh_env, "hash-object", "-w", "--stdin", stdin=content)
        lines.append(f"100644 {blob}\t{path}\0")
    for path, target in (links or {}).items():
        blob = git(repo, sh_env, "hash-object", "-w", "--stdin", stdin=target)
        lines.append(f"120000 {blob}\t{path}\0")
    for old, new in (renames or {}).items():
        blob = git(repo, sh_env, "rev-parse", f"{parent}:{old}")
        lines.append(f"0 {NULL_SHA}\t{old}\0")
        lines.append(f"100644 {blob}\t{new}\0")
    if bulk:
        blob = git(repo, sh_env, "hash-object", "-w", "--stdin", stdin="bulk\n")
        lines += [f"100644 {blob}\tdocs/bulk/f{i:05d}.txt\0" for i in range(bulk)]
    subprocess.run(  # noqa: S603
        ["git", "update-index", "--add", "-z", "--index-info"],  # noqa: S607
        cwd=repo,
        check=True,
        text=True,
        env=idx_env,
        input="".join(lines),
    )
    tree = subprocess.run(  # noqa: S603
        ["git", "write-tree"],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=idx_env,
    ).stdout.strip()
    index.unlink(missing_ok=True)
    return git(repo, sh_env, "commit-tree", tree, "-p", parent, "-m", message)


def tool_env(tmp_path: Path, bin_dir: Path) -> dict[str, str]:
    """A minimal environment: shims first, then the real git/jq/make, a writable TMPDIR."""
    (tmp_path / "tmp").mkdir(exist_ok=True)
    tool_dirs = {
        str(Path(shutil.which(t) or f"/usr/bin/{t}").parent) for t in ("git", "jq", "make")
    }
    path = os.pathsep.join([str(bin_dir), *sorted(tool_dirs), "/usr/bin", "/bin"])
    return {
        "PATH": path,
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path / "tmp"),
        "LLM_RUNNER_PORT": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
