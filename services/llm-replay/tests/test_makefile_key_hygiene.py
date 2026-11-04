"""The Makefile never hands the API key to recipes, and never creates a key file in a linked
worktree (FOUND-2; hard rule 4; addendum (b))."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
KEY_FILE = "." + "env"
FAKE_KEY = "sk-ant-" + "makefilefake0000000000"
PROBE = (
    "\nprobe:\n"
    '\t@env | grep -E "^(ANTHROPIC_API_KEY|DATABASE_URL)=" || true\n'
    '\t@echo "makevar=[$(ANTHROPIC_API_KEY)]"\n'
)


def run(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(  # noqa: S603
        ["make", "-s", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout


@pytest.mark.req("FOUND-2")
@pytest.mark.parametrize("parent_has_key", [False, True])
def test_recipes_never_see_the_key(tmp_path: Path, parent_has_key: bool) -> None:
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text() + PROBE)
    (tmp_path / KEY_FILE).write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\nDATABASE_URL=postgresql://x\n")
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    if parent_has_key:
        env["ANTHROPIC_API_KEY"] = FAKE_KEY
    out = run(tmp_path, "probe", env=env)
    assert "DATABASE_URL=postgresql://x" in out  # non-secret values still reach recipes
    assert FAKE_KEY not in out
    assert "makevar=[]" in out


@pytest.mark.req("CC-27")
def test_unreadable_key_file_does_not_stop_make(tmp_path: Path) -> None:
    """Claude's sandbox denies reads of the key file; every target must still run."""
    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text() + PROBE)
    # The sandbox fails even a stat, which a chmod 0 file does not: make would pass on one.
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / KEY_FILE).write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\n")
    (tmp_path / KEY_FILE).symlink_to(locked / KEY_FILE)
    locked.chmod(0)
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    try:
        assert "makevar=[]" in run(tmp_path, "probe", env=env)
    finally:
        locked.chmod(0o700)  # or pytest cannot clean up tmp_path


def git(cwd: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.mark.req("FOUND-2")
def test_setup_creates_no_key_file_in_a_linked_worktree(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not available")
    main = tmp_path / "main"
    main.mkdir()
    shutil.copy(REPO / "Makefile", main / "Makefile")
    git(main, "init", "-q")
    git(main, "add", "Makefile")
    git(main, "commit", "-q", "-m", "init")
    wt = tmp_path / "wt"
    git(main, "worktree", "add", "-q", "--detach", str(wt))
    in_wt = run(wt, "-n", "setup")
    in_main = run(main, "-n", "setup")
    assert "linked worktree: no" in in_wt and "cp env.example" not in in_wt
    assert "cp env.example" in in_main
