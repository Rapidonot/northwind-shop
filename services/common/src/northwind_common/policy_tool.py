"""The policy tool's output contract between mcp-policy and the assistant (row #27 PR 2, CHK-07;
hard rules 2, 6 and 7). As `order_tool`: the server labels every successful result, and the JSON
inside cannot close its tag, because it is ASCII, NFKC-normalised, and `<`, `>` and `&` are escaped.

    <policy_data tool="evaluate_return_eligibility">
    {"outcome":"eligible","reason":"within_window",...}
    </policy_data>
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from northwind_common.localenv import worktree_root
from northwind_common.mcp_token import POLICY_SERVER
from northwind_common.tool_scopes import EVALUATE_RETURN, POLICY_TOOLS

SERVER_NAME = POLICY_SERVER
TAG = "policy_data"
TOOL_NAMES = tuple(sorted(POLICY_TOOLS))
ALLOWED_TOOLS = tuple(f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES)
EVALUATE_RETURN_TOOL = f"mcp__{SERVER_NAME}__{EVALUATE_RETURN}"

# Where mcp-policy listens in this worktree: `MCP_POLICY_PORT`, else derived from the worktree's
# path below every other derived range (the dev proxy's starts at 20000, the runner is on 19990).
MCP_POLICY_PORT_BASE = 10000
MCP_POLICY_PORT_SPAN = 8000


def mcp_policy_port(cwd: Path | None = None) -> int:
    override = os.environ.get("MCP_POLICY_PORT")
    if override:
        return int(override)
    digest = hashlib.sha256(str(worktree_root(cwd)).encode("utf-8")).digest()
    return MCP_POLICY_PORT_BASE + int.from_bytes(digest[:4], "big") % MCP_POLICY_PORT_SPAN


def mcp_policy_url(cwd: Path | None = None) -> str:
    return os.environ.get("MCP_POLICY_URL") or f"http://127.0.0.1:{mcp_policy_port(cwd)}/mcp"


_LABELLED = re.compile(
    rf'\A<{TAG} tool="(?P<tool>[a-z_]{{1,32}})">\n(?P<body>[^<>&]*)\n</{TAG}>\Z', re.ASCII
)


def _normalised(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFKC", value)
    if isinstance(value, dict):
        return {_normalised(k): _normalised(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_normalised(v) for v in value]
    return value


def label(tool: str, value: Any) -> str:
    """A success result as the model sees it."""
    if tool not in TOOL_NAMES:
        raise ValueError("unknown policy tool")
    body = json.dumps(_normalised(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    body = body.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<{TAG} tool="{tool}">\n{body}\n</{TAG}>'


def unlabel(text: str) -> tuple[str, Any] | None:
    """The tool name and value of a labelled result, or None for anything else."""
    found = _LABELLED.match(text)
    if found is None or found["tool"] not in TOOL_NAMES:
        return None
    try:
        return found["tool"], json.loads(found["body"])
    except ValueError:
        return None
