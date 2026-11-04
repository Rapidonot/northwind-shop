"""`make runner-check`: measured from inside the sandbox, nothing the runner loads is writable.

The runner is only as clean as what it imports. This asks the runner's own Python where it loads
code from, then tries to write in each place. It has to run sandboxed: from a terminal every write
succeeds, so it reports failure there, correctly (CC-27, decision 0005).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterable
from pathlib import Path

from northwind_common import localenv

PROBE = (
    "import json, sys; print(json.dumps({'paths': [sys.executable, sys.prefix, sys.base_prefix,"
    " *sys.path]}))"
)


def load_paths(python: Path) -> list[Path]:
    """Every directory `python -I` loads from, and where its interpreter really lives."""
    done = subprocess.run(  # noqa: S603 - the runner's own interpreter, fixed arguments
        [str(python), "-I", "-c", PROBE], capture_output=True, text=True, check=True, timeout=30
    )
    found = {python.parent, python.resolve().parent}
    for name in json.loads(done.stdout)["paths"]:
        if name:
            path = Path(name)
            found.update({path, path.resolve()})
    tool = shutil.which("uv")
    if tool:
        found.add(Path(tool).resolve().parent)
    # A missing entry such as python312.zip can still be created in its parent.
    return sorted({p if p.is_dir() else p.parent for p in found})


def dir_is_writable(directory: Path) -> bool:
    probe = directory / f".runner-check-{uuid.uuid4().hex}"
    try:
        probe.touch(exist_ok=False)
    except OSError:
        return False
    probe.unlink()
    return True


def file_is_writable(path: Path) -> bool:
    """Open for append and close: nothing changes even when this succeeds."""
    try:
        with path.open("a"):
            return True
    except OSError:
        return False


def is_hidden(path: Path) -> bool:
    """A stat only: this never opens the key file. A sandbox denial raises PermissionError. A
    file that simply is not there proves nothing, so `check` reports it."""
    try:
        path.stat()
    except PermissionError:
        return True
    except FileNotFoundError:
        raise
    return False


def present(path: Path) -> bool:
    """A sandbox denial of a stat raises on Python 3.12, and a denied path is not a problem."""
    try:
        path.stat()
    except OSError:
        return False
    return True


def excluded_commands(settings_file: Path, *, must_enable: bool = True) -> list[str]:
    """Problems with one settings file. An exclusion runs code the session can edit outside the
    sandbox, where it can read the key: that is F1 again."""
    try:
        sandbox = json.loads(settings_file.read_text()).get("sandbox", {})
    except (OSError, ValueError):
        return [f"UNREADABLE {settings_file}"]
    problems = []
    if sandbox.get("excludedCommands"):
        problems.append(f"EXCLUSIONS {settings_file}: commit the new settings, then merge them")
    if must_enable and not sandbox.get("enabled"):
        problems.append(f"NO SANDBOX {settings_file}")
    return problems


def checkouts() -> list[Path]:
    done = subprocess.run(  # noqa: S603 - fixed git arguments, no shell
        ["git", "worktree", "list", "--porcelain"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return [Path(line[9:]) for line in done.stdout.splitlines() if line.startswith("worktree ")]


def check(directories: Iterable[Path], files: Iterable[Path], hidden: Iterable[Path]) -> list[str]:
    """One line per writable or visible path; empty means every attempt was refused."""
    problems = [f"WRITABLE {d}" for d in directories if present(d) and dir_is_writable(d)]
    problems += [f"WRITABLE {f}" for f in files if present(f) and file_is_writable(f)]
    for path in hidden:
        try:
            if not is_hidden(path):
                problems.append(f"VISIBLE {path}")
        except FileNotFoundError:
            problems.append(f"MISSING {path}: nothing was proved about it")
    return problems


def main(python: Path) -> int:
    if not os.access(python, os.X_OK):
        print(f"error: no runner interpreter at {python}: clone and `make runner-sync` first")
        return 2
    home = localenv.northwind_home()
    git_dir = localenv.main_checkout() / ".git"
    directories = [
        *load_paths(python),
        home,
        home / "runner",
        home / "uv-cache",
        git_dir / "hooks",  # your terminal's git runs these, so a write here is code outside
        Path.home() / ".config" / "uv",
    ]
    ledger = localenv.ledger_path()
    files = [ledger, ledger.with_suffix(".lock"), git_dir / "config", Path.home() / ".gitconfig"]
    problems = check(directories, files, [localenv.env_file()])
    for checkout in checkouts():
        problems += excluded_commands(checkout / ".claude" / "settings.json")
        local = checkout / ".claude" / "settings.local.json"
        if present(local):
            problems += excluded_commands(local, must_enable=False)
    for line in problems:
        print(line)
    print(f"runner-check: {len(directories)} directories, {len(problems)} problem(s)")
    print("this proves the settings of this checkout: run it in each one")
    return 1 if problems else 0
