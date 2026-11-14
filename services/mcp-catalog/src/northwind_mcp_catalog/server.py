"""The northwind-catalog MCP server (CC-10, row #10b; PRD §7).

The same shape as `northwind_mcp_rag.server`:
- Streamable HTTP on 127.0.0.1, with the SDK's DNS-rebinding protection.
- Behind the shared token gate (`northwind_common.mcp_gate`), bound to this server. A token minted
  for northwind-rag gets a bare 401 here (CC-10 security review L4).
- Handlers take the caller (audience, region, agent) only from the verified token.
- A caller sees only what `northwind_common.tool_scopes` gives its agent on this server. Anything
  else, including a tool that does not exist, gets one `permission` refusal.
- Errors are the structured envelope with fixed text.
- There are no `instructions` and no resources: the category tree and attribute dictionary (Q3) are
  not built here.
- Logs name categories and codes, never arguments, tokens or exception text.

What the server can call is `TOOLS`: the five read-only tools. The live store's `set_price` and
`set_stock` are not tools, so no request reaches them.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import mcp.types as t
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError

from northwind_common import catalog_tool, mcp_gate
from northwind_common.errors import ToolError
from northwind_common.mcp_gate import ASGIApp
from northwind_common.mcp_token import CATALOG_SERVER, Claims
from northwind_common.tool_scopes import allowed_tools
from northwind_mcp_catalog.live import LiveCatalog

log = logging.getLogger("northwind_mcp_catalog")

SERVER_NAME = CATALOG_SERVER


def not_allowed() -> ToolError:
    return ToolError(
        "permission", False, "not_allowed", "That tool is not available to this caller."
    )


def tool_failed() -> ToolError:
    return ToolError("transient", True, "tool_failed", "The tool failed. Try again later.")


ToolHandler = Callable[["CatalogServer", Claims, Mapping[str, Any]], Awaitable[t.CallToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    tool: t.Tool
    handler: ToolHandler


def error_result(error: ToolError) -> t.CallToolResult:
    """The envelope as JSON text, which the model sees, and as `structuredContent` for MCP
    clients, as in mcp-rag (S0 item 5)."""
    envelope = error.to_envelope()
    return t.CallToolResult(
        content=[t.TextContent(type="text", text=json.dumps(envelope))],
        structured_content=envelope,
        is_error=True,
    )


def json_result(tool: str, value: Any) -> t.CallToolResult:
    """A success is text only, with no `structuredContent` (S0 item 5): the JSON inside a
    `<catalog_data tool="…">` label, so the model reads it as data (row #12, #10b security review
    L6, hard rule 6). The format is `northwind_common.catalog_tool`'s."""
    text = catalog_tool.label(tool, value)
    return t.CallToolResult(content=[t.TextContent(type="text", text=text)], is_error=False)


def claims_of(ctx: Any) -> Claims:
    """The verified caller, set by the token gate. A request that reached a handler without one is
    refused (it cannot happen through `CatalogServer.app`)."""
    request = getattr(ctx, "request", None)
    state = getattr(request, "scope", {}).get("state", {}) if request is not None else {}
    claims = state.get(mcp_gate.CLAIMS_KEY) if isinstance(state, dict) else None
    if not isinstance(claims, Claims):
        raise not_allowed()
    return claims


class CatalogServer:
    def __init__(
        self,
        catalog: LiveCatalog,
        *,
        key: bytes,
        tools: Mapping[str, ToolSpec] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.catalog = catalog
        self._key = key
        self._tools = dict(tools or {})
        self._clock = clock
        self.calls = 0  # tool calls that reached a handler; the contract's 401 tests read it

    def _offered(self, claims: Claims) -> list[str]:
        allowed = allowed_tools(claims.agent, SERVER_NAME)
        return sorted(name for name in self._tools if name in allowed)

    async def list_tools(self, ctx: Any, params: Any) -> t.ListToolsResult:
        try:
            claims = claims_of(ctx)
        except ToolError as error:
            raise MCPError(-32001, error.message, error.to_envelope()) from None
        return t.ListToolsResult(tools=[self._tools[name].tool for name in self._offered(claims)])

    async def call_tool(self, ctx: Any, params: t.CallToolRequestParams) -> t.CallToolResult:
        try:
            claims = claims_of(ctx)
            if params.name not in self._offered(claims):
                log.warning("tool refused: permission")
                raise not_allowed()
            self.calls += 1
            return await self._tools[params.name].handler(self, claims, params.arguments or {})
        except ToolError as error:
            return error_result(error)
        except Exception as error:  # noqa: BLE001 - its text must not reach the caller
            log.error("tool failed: %s", type(error).__name__)
            return error_result(tool_failed())

    def mcp_server(self) -> Server[Any]:
        return Server(SERVER_NAME, on_list_tools=self.list_tools, on_call_tool=self.call_tool)

    def app(self) -> ASGIApp:
        inner = self.mcp_server().streamable_http_app(host="127.0.0.1")
        return mcp_gate.token_gate(inner, key=self._key, clock=self._clock, server=SERVER_NAME)
