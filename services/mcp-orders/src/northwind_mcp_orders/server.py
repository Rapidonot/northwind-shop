"""The northwind-orders MCP server (row #27, CHK-06; CC-10; PRD §7).

The same shape as `northwind_mcp_catalog.server`:
- Streamable HTTP on 127.0.0.1, with the SDK's DNS-rebinding protection.
- Behind the shared token gate (`northwind_common.mcp_gate`), bound to this server: a token minted
  for northwind-rag or northwind-catalog gets a bare 401 here.
- Handlers take the caller (audience, region, agent and the signed-in shopper, `shp`) only from the
  verified token. No schema takes a shopper or a region.
- A caller sees only what `northwind_common.tool_scopes` gives its agent on this server: the
  assistant's `get_order` and `request_return`. Anything else, including a tool that does not exist,
  gets one `permission` refusal.
- Errors are the structured envelope with fixed text. Logs name codes, never arguments or tokens.

No tool writes an order, and none lists one. `request_return` (PR 2, CHK-07) records the shopper's
own return request in memory, through `ReturnDesk`, and nothing else.
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

from northwind_common import mcp_gate, order_tool
from northwind_common.errors import ToolError
from northwind_common.mcp_gate import ASGIApp
from northwind_common.mcp_token import ORDERS_SERVER, Claims
from northwind_common.tool_scopes import allowed_tools
from northwind_mcp_orders.book import OrderBook
from northwind_mcp_orders.policy_data import PolicyData
from northwind_mcp_orders.returns import ReturnDesk

log = logging.getLogger("northwind_mcp_orders")

SERVER_NAME = ORDERS_SERVER


def not_allowed() -> ToolError:
    return ToolError(
        "permission", False, "not_allowed", "That tool is not available to this caller."
    )


def tool_failed() -> ToolError:
    return ToolError("transient", True, "tool_failed", "The tool failed. Try again later.")


ToolHandler = Callable[["OrdersServer", Claims, Mapping[str, Any]], Awaitable[t.CallToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    tool: t.Tool
    handler: ToolHandler


def error_result(error: ToolError) -> t.CallToolResult:
    envelope = error.to_envelope()
    return t.CallToolResult(
        content=[t.TextContent(type="text", text=json.dumps(envelope))],
        structured_content=envelope,
        is_error=True,
    )


def json_result(tool: str, value: Any) -> t.CallToolResult:
    """A success is text only: the JSON inside an `<order_data tool="…">` label, so the model reads
    it as data (hard rule 6). The format is `northwind_common.order_tool`'s."""
    text = order_tool.label(tool, value)
    return t.CallToolResult(content=[t.TextContent(type="text", text=text)], is_error=False)


def claims_of(ctx: Any) -> Claims:
    request = getattr(ctx, "request", None)
    state = getattr(request, "scope", {}).get("state", {}) if request is not None else {}
    claims = state.get(mcp_gate.CLAIMS_KEY) if isinstance(state, dict) else None
    if not isinstance(claims, Claims):
        raise not_allowed()
    return claims


class OrdersServer:
    def __init__(
        self,
        book: OrderBook,
        *,
        key: bytes,
        tools: Mapping[str, ToolSpec] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.book = book
        # Refuses to start on policy documents that lost a fact (D14, decision 0024).
        self.desk = ReturnDesk(book, PolicyData.from_fixtures())
        self._key = key
        self._tools = dict(tools or {})
        self._clock = clock
        self.calls = 0  # tool calls that reached a handler; the 401 tests read it

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
            if error.category == "transient":
                log.warning("tool failed: %s", error.code)
            return error_result(error)
        except Exception as error:  # noqa: BLE001 - its text must not reach the caller
            log.error("tool failed: %s", type(error).__name__)
            return error_result(tool_failed())

    def mcp_server(self) -> Server[Any]:
        return Server(SERVER_NAME, on_list_tools=self.list_tools, on_call_tool=self.call_tool)

    def app(self) -> ASGIApp:
        inner = self.mcp_server().streamable_http_app(host="127.0.0.1")
        return mcp_gate.token_gate(inner, key=self._key, clock=self._clock, server=SERVER_NAME)
