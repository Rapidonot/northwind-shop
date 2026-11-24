"""The order tool's output contract between mcp-orders and the assistant (row #27, CHK-06; hard
rules 2, 6 and 7).

The server (`northwind_mcp_orders`) labels every successful result, and the assistant reads the
order's status back with the same code, so neither depends on the other (as with `catalog_tool`).
What the model sees for a success is

    <order_data tool="get_order">
    {"order_id":"O000064","status":"delivered",...}
    </order_data>

The JSON is ASCII, its strings NFKC-normalised first, and `<`, `>` and `&` are escaped, so no value
can close the tag or open another one (#10b security review L6). Errors are not labelled: they are
the fixed structured envelope, which holds no free text.

`STATUSES` are the shopper-facing statuses `get_order` can return; `CONFIRMED_STATUSES` the ones of
an order the store has confirmed, which the output check lets the assistant call confirmed or placed
when this turn's `get_order` returned one for that order (decision 0015's row #27 amendment).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from northwind_common.localenv import PORT_SPAN, worktree_root
from northwind_common.mcp_token import ORDERS_SERVER
from northwind_common.tool_scopes import GET_ORDER, ORDER_TOOLS

SERVER_NAME = ORDERS_SERVER
TAG = "order_data"
TOOL_NAMES = tuple(sorted(ORDER_TOOLS))
ALLOWED_TOOLS = tuple(f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES)
GET_ORDER_TOOL = f"mcp__{SERVER_NAME}__{GET_ORDER}"

# The store has not confirmed it yet (pending or paying), or a person is looking at it (escalated).
UNCONFIRMED_STATUSES = frozenset({"processing", "on_hold"})
# Confirmed: no shipment yet, then the carrier's scans, last scan first in the mapping's order.
CONFIRMED_STATUSES = frozenset(
    {"confirmed", "label_created", "picked_up", "in_transit", "out_for_delivery", "delivered"}
)
STATUSES = UNCONFIRMED_STATUSES | CONFIRMED_STATUSES
ORDER_ID = re.compile(r"O\d{6}", re.ASCII)  # fullmatch

# Where mcp-orders listens in this worktree: `MCP_ORDERS_PORT`, else derived from the worktree's
# path as `localenv` derives mcp-rag's and mcp-catalog's ports, on its own base so none collide.
# Here and not in `localenv`, so this row adds no edit there.
MCP_ORDERS_PORT_BASE = 50000


def mcp_orders_port(cwd: Path | None = None) -> int:
    override = os.environ.get("MCP_ORDERS_PORT")
    if override:
        return int(override)
    digest = hashlib.sha256(str(worktree_root(cwd)).encode("utf-8")).digest()
    return MCP_ORDERS_PORT_BASE + int.from_bytes(digest[:4], "big") % PORT_SPAN


def mcp_orders_url(cwd: Path | None = None) -> str:
    """Where the gateway's signed-in turns reach mcp-orders: `MCP_ORDERS_URL`, else this
    worktree's port on 127.0.0.1."""
    return os.environ.get("MCP_ORDERS_URL") or f"http://127.0.0.1:{mcp_orders_port(cwd)}/mcp"


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
        raise ValueError("unknown order tool")
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


def order_status(text: str, is_error: bool) -> tuple[str, str] | None:
    """(order ID, status) from a `get_order` success, or None for anything else: an error, text
    that is not exactly one label, a malformed ID or an unknown status. The output check is vouched
    only by this (hard rule 2)."""
    if is_error:
        return None
    labelled = unlabel(text)
    if labelled is None or labelled[0] != GET_ORDER or not isinstance(labelled[1], dict):
        return None
    order_id, status = labelled[1].get("order_id"), labelled[1].get("status")
    if not isinstance(order_id, str) or not ORDER_ID.fullmatch(order_id):
        return None
    if not isinstance(status, str) or status not in STATUSES:
        return None
    return order_id, status
