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

What the server can call is `TOOLS`: the five read-only catalog tools and, since row #16 (CHK-01,
decision 0019), the four cart tools, so the server is no longer read-only. The cart tools write only
the caller's own cart (`carts`), keyed on the token's session and region; they never write the
catalog. The live store's `set_price` and `set_stock` are not tools, so no request reaches them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar, Token
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
from northwind_mcp_catalog.cart_store import CartStore
from northwind_mcp_catalog.holds import Holds, NoHolds, subtract, unavailable
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
    # Row #18 step 0, security review M2 of option B: the call reads stock, so the server takes its
    # one holds reading before the handler runs, off the event loop.
    reads_stock: bool = False


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


# The holds reading of the tool call being handled (`CatalogServer.reading`): (region, reading).
# A reading is None when the reader failed: the call raises `unavailable()` only if it reads stock
# (option B's re-review N-L1), so a validation error or a price-only filter answers as before.
_READING: ContextVar[tuple[str, Mapping[str, int] | None] | None] = ContextVar(
    "holds_reading", default=None
)


class _Reading:
    """A plain context manager, not `contextlib.contextmanager`: that one sets `__traceback__` on
    the exception leaving the block, and a `ToolError` is a frozen dataclass."""

    def __init__(self, held: Mapping[str, int] | None, region: str) -> None:
        self.held, self.region = held, region
        self._token: Token[tuple[str, Mapping[str, int] | None] | None] | None = None

    def __enter__(self) -> None:
        self._token = _READING.set((self.region, self.held))

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            _READING.reset(self._token)


class CatalogServer:
    def __init__(
        self,
        catalog: LiveCatalog,
        *,
        key: bytes,
        tools: Mapping[str, ToolSpec] | None = None,
        clock: Callable[[], float] = time.time,
        holds: Holds | None = None,
    ) -> None:
        self.catalog = catalog
        self.holds: Holds = holds or NoHolds()  # row #18, step 0 (c): BE-04's live holds
        self.carts = CartStore()  # row #16: shoppers' carts, keyed on the token's session
        self._key = key
        self._tools = dict(tools or {})
        self._clock = clock
        self.calls = 0  # tool calls that reached a handler; the contract's 401 tests read it

    def on_hand(self, variant_id: str, region: str, held: Mapping[str, int] | None = None) -> int:
        """Live stock in `region` less the units BE-04 holds live there (`holds.py`). Pass `held`,
        one `holds.live(region)` read, to price many variants on one reading; inside `reading`,
        that call's one reading is used."""
        if held is None:
            held = self.held(region)
        return subtract(self.catalog.stock(variant_id, region), held, variant_id)

    def held(self, region: str) -> Mapping[str, int]:
        """The call's holds reading for `region` (`reading`), or a fresh one outside it."""
        taken = _READING.get()
        if taken is not None and taken[0] == region:
            if taken[1] is None:
                raise unavailable()
            return taken[1]
        return self.holds.live(region)

    def reading(self, region: str) -> _Reading:
        """One holds reading for a whole tool call, taken before the call changes anything: a cart
        write takes it before its lock, so a reader that fails fails the call before the cart moves,
        and the result's view can't fail after the write (row #18 step 0, security review M1)."""
        return _Reading(self.held(region), region)  # the call's reading, when it has one

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
            spec = self._tools[params.name]
            if spec.reads_stock and claims.region and not isinstance(self.holds, NoHolds):
                # A database read blocks: never on the one event loop every shopper shares (M2).
                held: Mapping[str, int] | None
                try:
                    held = await asyncio.to_thread(self.holds.live, claims.region)
                except ToolError:
                    held = None  # raised only if the handler reads stock (N-L1)
                with _Reading(held, claims.region):
                    return await spec.handler(self, claims, params.arguments or {})
            return await spec.handler(self, claims, params.arguments or {})
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
