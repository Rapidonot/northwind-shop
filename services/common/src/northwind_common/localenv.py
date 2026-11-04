"""Where local LLM state lives when the lab is built in parallel git worktrees (FOUND-2 addendum).

- One ledger for every worktree: `LLM_LEDGER_PATH`, default `~/.northwind/ledger.jsonl`. Budget
  reservations take a file lock on it, so worktrees cannot jointly overspend.
- One key file: `LLM_ENV_FILE`, default the **main checkout's** `.env`, found from git's common
  dir. `.env` is never copied into worktrees, and only the proxy reads this file.
- A dev proxy port per worktree: `LLM_PROXY_PORT`, default derived from the worktree's path, so
  two worktrees' dev proxies never collide and each stays on a stable port. Tests use random ports.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

PORT_BASE = 20000
PORT_SPAN = 10000


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(  # noqa: S603 - fixed git arguments, no shell
            ["git", *args],  # noqa: S607 - git from PATH is the developer's own
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def worktree_root(cwd: Path | None = None) -> Path:
    """The checkout (main or linked worktree) that contains `cwd`."""
    here = (cwd or Path.cwd()).resolve()
    top = _git(["rev-parse", "--show-toplevel"], here)
    return Path(top) if top else here


def main_checkout(cwd: Path | None = None) -> Path:
    """The main checkout, also when `cwd` is inside a linked worktree."""
    here = (cwd or Path.cwd()).resolve()
    common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], here)
    return Path(common).parent if common else worktree_root(here)


def is_linked_worktree(cwd: Path | None = None) -> bool:
    return worktree_root(cwd) != main_checkout(cwd)


def env_file(cwd: Path | None = None) -> Path:
    override = os.environ.get("LLM_ENV_FILE")
    return Path(override).expanduser() if override else main_checkout(cwd) / ".env"


def ledger_path() -> Path:
    override = os.environ.get("LLM_LEDGER_PATH")
    return Path(override).expanduser() if override else Path.home() / ".northwind" / "ledger.jsonl"


def proxy_port(cwd: Path | None = None) -> int:
    override = os.environ.get("LLM_PROXY_PORT")
    if override:
        return int(override)
    digest = hashlib.sha256(str(worktree_root(cwd)).encode("utf-8")).digest()
    return PORT_BASE + int.from_bytes(digest[:4], "big") % PORT_SPAN
