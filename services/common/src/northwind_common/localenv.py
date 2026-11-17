"""Where local LLM state lives when the lab is built in parallel git worktrees (FOUND-2 addendum).

- One ledger for every worktree: `LLM_LEDGER_PATH`, default `~/.northwind/ledger.jsonl`. Budget
  reservations take a file lock on it, so worktrees cannot jointly overspend.
- One key file, outside every checkout: `LLM_ENV_FILE`, default
  `~/.northwind/secrets/anthropic.env`. Only the runner reads the key from it (decision 0005).
- A dev proxy port per worktree: `LLM_PROXY_PORT`, default derived from the worktree's path, so
  two worktrees' dev proxies never collide and each stays on a stable port. Tests use random ports.
- One runner: a clean clone at `~/.northwind/runner`, on `LLM_RUNNER_PORT` (default 19990, below
  the dev proxy span).
- An mcp-rag port per worktree (CC-10): `MCP_RAG_PORT`, default derived from the worktree's path in
  its own span, above the proxy's; `MCP_RAG_URL` overrides the whole URL. Tests use random ports.
- An mcp-catalog port per worktree (#10b): `MCP_CATALOG_PORT`, default derived the same way in a
  span above mcp-rag's.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

PORT_BASE = 20000
PORT_SPAN = 10000
RUNNER_PORT = 19990
MCP_RAG_PORT_BASE = 30000
MCP_CATALOG_PORT_BASE = 40000


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


def northwind_home() -> Path:
    return Path.home() / ".northwind"


def env_file() -> Path:
    override = os.environ.get("LLM_ENV_FILE")
    if override:
        return Path(override).expanduser()
    return northwind_home() / "secrets" / "anthropic.env"


def ledger_path() -> Path:
    override = os.environ.get("LLM_LEDGER_PATH")
    return Path(override).expanduser() if override else northwind_home() / "ledger.jsonl"


def runner_checkout() -> Path:
    return northwind_home() / "runner"


def runner_port() -> int:
    return int(os.environ.get("LLM_RUNNER_PORT") or RUNNER_PORT)


def runner_url() -> str:
    return f"http://127.0.0.1:{runner_port()}"


def proxy_port(cwd: Path | None = None) -> int:
    override = os.environ.get("LLM_PROXY_PORT")
    if override:
        return int(override)
    return PORT_BASE + _worktree_offset(cwd)


def _worktree_offset(cwd: Path | None) -> int:
    digest = hashlib.sha256(str(worktree_root(cwd)).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % PORT_SPAN


def mcp_rag_port(cwd: Path | None = None) -> int:
    override = os.environ.get("MCP_RAG_PORT")
    if override:
        return int(override)
    return MCP_RAG_PORT_BASE + _worktree_offset(cwd)


def mcp_rag_url(cwd: Path | None = None) -> str:
    return os.environ.get("MCP_RAG_URL") or f"http://127.0.0.1:{mcp_rag_port(cwd)}/mcp"


def mcp_catalog_port(cwd: Path | None = None) -> int:
    """mcp-catalog's port for this worktree (#10b): `MCP_CATALOG_PORT`, else derived from the path
    like mcp-rag's, on its own base so the two never collide."""
    override = os.environ.get("MCP_CATALOG_PORT")
    if override:
        return int(override)
    return MCP_CATALOG_PORT_BASE + _worktree_offset(cwd)


def mcp_catalog_url(cwd: Path | None = None) -> str:
    """Where the gateway's turns reach mcp-catalog (row #12): `MCP_CATALOG_URL`, else this
    worktree's port on 127.0.0.1."""
    return os.environ.get("MCP_CATALOG_URL") or f"http://127.0.0.1:{mcp_catalog_port(cwd)}/mcp"


def database_name(url: str) -> str:
    """Only the database name in a connection URL, for a startup line that proves which database a
    server reads (row #16: the scratch database). Never the URL, which can hold a password; a URL
    that does not parse says so without echoing it."""
    import re
    from urllib.parse import unquote, urlsplit

    try:
        if url.startswith(("postgresql://", "postgres://")):
            name = unquote(urlsplit(url).path.lstrip("/"))
        else:  # libpq keyword form, `dbname=… host=…`
            found = re.search(r"(?:^|\s)dbname\s*=\s*'?([^'\s]+)", url)
            name = found.group(1) if found else ""
    except ValueError:
        return "(unreadable)"
    return name if re.fullmatch(r"[A-Za-z0-9_.-]{1,63}", name) else "(unreadable)"


def announce_database(server: str, url: str) -> None:
    """One stderr line naming the server and its database (`database_name`), at startup."""
    import sys

    print(f"{server}: database {database_name(url)}", file=sys.stderr, flush=True)
