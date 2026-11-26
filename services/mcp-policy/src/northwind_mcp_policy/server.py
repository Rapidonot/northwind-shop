"""The northwind-policy MCP server (row #27 PR 2, CHK-07; CC-10; PRD §7).

The same shape as `northwind_mcp_orders.server`: Streamable HTTP on 127.0.0.1 behind the shared
token gate, bound to this server; the caller comes only from the verified token; a caller sees only
what `tool_scopes` gives its agent; errors are the structured envelope with fixed text; logs name
codes, never arguments or tokens. It is read-only.

Its resources are the public returns and shipping policies, one per kind and region. A caller reads
only its token's region, so regional policy matches the shopper's region (hard rule 7, RAG-11), and
another region's URI is refused as any unknown one is. Internal documents are never loaded
(`northwind_mcp_orders.policy_data`). Data v3 has no warranty document, so there is no warranty
resource.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import mcp.types as t
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError

from northwind_common import mcp_gate, policy_tool, returns_policy
from northwind_common.errors import ToolError
from northwind_common.mcp_gate import ASGIApp
from northwind_common.mcp_token import POLICY_SERVER, Claims
from northwind_common.tool_scopes import allowed_resources, allowed_tools
from northwind_mcp_orders.book import OrderBook
from northwind_mcp_orders.policy_data import PolicyData, PolicyDocument
from northwind_mcp_orders.server import claims_of, error_result, not_allowed, tool_failed

log = logging.getLogger("northwind_mcp_policy")

SERVER_NAME = POLICY_SERVER


def resource_not_allowed() -> ToolError:
    return ToolError(
        "permission", False, "not_allowed", "That resource is not available to this caller."
    )


# JSON-RPC codes for a refused resource read: the envelope rides in the error's data.
_RPC_CODES = {"permission": -32001, "transient": -32002, "validation": -32602, "business": -32003}


def _rpc_error(error: ToolError) -> MCPError:
    return MCPError(_RPC_CODES[error.category], error.message, error.to_envelope())


ToolHandler = Callable[["PolicyServer", Claims, Mapping[str, Any]], Awaitable[t.CallToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    tool: t.Tool
    handler: ToolHandler


def json_result(tool: str, value: Any) -> t.CallToolResult:
    """A success is text only, inside a `<policy_data>` label (hard rule 6)."""
    text = policy_tool.label(tool, value)
    return t.CallToolResult(content=[t.TextContent(type="text", text=text)], is_error=False)


def resource_uri(document: PolicyDocument) -> str:
    return f"northwind-policy://{document.kind}/{document.region}"


class PolicyServer:
    def __init__(
        self,
        book: OrderBook,
        policy: PolicyData,
        *,
        key: bytes,
        tools: Mapping[str, ToolSpec] | None = None,
        as_of: date = returns_policy.AS_OF,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.book = book
        self.policy = policy
        self.as_of = as_of  # fixed at start (D5): no argument or token sets it
        self._key = key
        self._tools = dict(tools or {})
        self._clock = clock
        self.calls = 0  # tool calls that reached a handler; the 401 tests read it

    # ---- tools ---------------------------------------------------------------------------------

    def _offered(self, claims: Claims) -> list[str]:
        allowed = allowed_tools(claims.agent, SERVER_NAME)
        return sorted(name for name in self._tools if name in allowed)

    async def list_tools(self, ctx: Any, params: Any) -> t.ListToolsResult:
        try:
            claims = claims_of(ctx)
        except ToolError as error:
            raise _rpc_error(error) from None
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

    # ---- resources -----------------------------------------------------------------------------

    def _readable(self, claims: Claims) -> dict[str, PolicyDocument]:
        """The caller's resources: allowed for its agent, and of its token's region only."""
        allowed = allowed_resources(claims.agent, SERVER_NAME)
        return {
            resource_uri(doc): doc
            for doc in self.policy.documents.values()
            if doc.region == claims.region and resource_uri(doc) in allowed
        }

    async def list_resources(self, ctx: Any, params: Any) -> t.ListResourcesResult:
        try:
            claims = claims_of(ctx)
        except ToolError as error:
            raise _rpc_error(error) from None
        return t.ListResourcesResult(
            resources=[
                t.Resource(
                    name=doc.document_id,
                    uri=uri,
                    mime_type="application/json",
                    description=f"{doc.title}: the public policy text and its facts.",
                )
                for uri, doc in sorted(self._readable(claims).items())
            ]
        )

    async def read_resource(
        self, ctx: Any, params: t.ReadResourceRequestParams
    ) -> t.ReadResourceResult:
        try:
            claims = claims_of(ctx)
        except ToolError as error:
            raise _rpc_error(error) from None
        uri = str(params.uri)
        doc = self._readable(claims).get(uri)
        if doc is None:
            log.warning("resource refused: permission")
            raise _rpc_error(resource_not_allowed())
        facts = returns_policy.FACTS[doc.region] if doc.kind == "returns" else {}
        body = {
            "document_id": doc.document_id,
            "title": doc.title,
            "region": doc.region,
            "kind": doc.kind,
            "text": doc.body,
            "facts": {key: fact.sentence for key, fact in facts.items()},
        }
        return t.ReadResourceResult(
            contents=[
                t.TextResourceContents(
                    uri=uri, mime_type="application/json", text=json.dumps(body, sort_keys=True)
                )
            ]
        )

    # ---- wiring --------------------------------------------------------------------------------

    def mcp_server(self) -> Server[Any]:
        return Server(
            SERVER_NAME,
            on_list_tools=self.list_tools,
            on_call_tool=self.call_tool,
            on_list_resources=self.list_resources,
            on_read_resource=self.read_resource,
        )

    def app(self) -> ASGIApp:
        inner = self.mcp_server().streamable_http_app(host="127.0.0.1")
        return mcp_gate.token_gate(inner, key=self._key, clock=self._clock, server=SERVER_NAME)
