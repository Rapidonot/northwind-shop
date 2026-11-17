"""The catalog tools' output contract between mcp-catalog and the assistant (row #12; hard rule 6).

The server (`northwind_mcp_catalog`) labels every successful result, and the assistant reads live
price and stock values back with the same code, so neither depends on the other (as with
`rag_tool`). What the model sees for a success is

    <catalog_data tool="get_price">
    {"amount":"89.00","currency":"USD",...}
    </catalog_data>

The JSON is ASCII (non-ASCII is `\\uXXXX`), its strings are NFKC-normalised first, and `<`, `>`
and `&` are escaped as `\\u003c`, `\\u003e` and `\\u0026`, so no value, however it was written,
can close the tag or open another one (#10b security review L6). Errors are not labelled: they
are the fixed structured envelope, which holds no free text.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from northwind_common.mcp_token import CATALOG_SERVER
from northwind_common.tool_scopes import CATALOG_READ_TOOLS

SERVER_NAME = CATALOG_SERVER
TAG = "catalog_data"
TOOL_NAMES = tuple(sorted(CATALOG_READ_TOOLS))
ALLOWED_TOOLS = tuple(f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES)
PRICE_TOOL = f"mcp__{SERVER_NAME}__get_price"
STOCK_TOOL = f"mcp__{SERVER_NAME}__check_stock"

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
        raise ValueError("unknown catalog tool")
    body = json.dumps(_normalised(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    body = body.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<{TAG} tool="{tool}">\n{body}\n</{TAG}>'


def unlabel(text: str) -> tuple[str, Any] | None:
    """The tool name and value of a labelled result, or None for anything else (an error envelope,
    text that is not exactly one label, a body that is not JSON)."""
    found = _LABELLED.match(text)
    if found is None or found["tool"] not in TOOL_NAMES:
        return None
    try:
        return found["tool"], json.loads(found["body"])
    except ValueError:
        return None
