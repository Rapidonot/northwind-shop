"""The northwind-rag MCP server (CC-10; PRD §7).

Streamable HTTP on 127.0.0.1, with the SDK's DNS-rebinding protection. A token gate runs before
MCP: a request without a valid per-turn token (`northwind_common.mcp_token`) gets a bare 401 and
never reaches a handler. Handlers take the caller (audience, region, agent, turn) only from the
verified token, never from arguments.

What a caller sees is what its agent is given in `northwind_common.tool_scopes`: other tools are
not listed and are refused with a `permission` error, which is the same for a tool that does not
exist, so a refusal does not reveal what exists. Errors are the structured envelope of
`northwind_common.errors`, with fixed text. The server has no `instructions` (S0 item 3) and no SQL
of its own: it calls `northwind_rag`. Logs name categories and codes, never a query, a token or an
exception's text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import mcp.types as t
import psycopg
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError

from northwind_common import mcp_gate
from northwind_common.errors import ToolError
from northwind_common.mcp_gate import ASGIApp
from northwind_common.mcp_token import RAG_SERVER, Claims
from northwind_common.tool_scopes import allowed_resources, allowed_tools
from northwind_data.dataset import FIXTURES_DIR
from northwind_data.ingest_guard import verify_dataset
from northwind_mcp_rag.ledger import SearchLedger
from northwind_rag import store
from northwind_rag.embedding import Embedder
from northwind_rag.retrieval import (
    Audience,
    RetrievedChunk,
    ShopperRegion,
    retrieve_context,
    retriever_id,
)

log = logging.getLogger("northwind_mcp_rag")

SERVER_NAME = RAG_SERVER
MANIFEST_URI = "northwind-rag://index/manifest"
CLAIMS_KEY = mcp_gate.CLAIMS_KEY


# Fixed text only. Each call builds a fresh error, so no raised object is shared between requests.
def not_allowed() -> ToolError:
    return ToolError(
        "permission", False, "not_allowed", "That tool is not available to this caller."
    )


def tool_failed() -> ToolError:
    return ToolError("transient", True, "tool_failed", "The tool failed. Try again later.")


def resource_not_allowed() -> ToolError:
    return ToolError(
        "permission", False, "not_allowed", "That resource is not available to this caller."
    )


def index_unavailable() -> ToolError:
    return ToolError("transient", True, "index_unavailable", "The index is unavailable right now.")


# JSON-RPC codes for a refused resource read: the envelope rides in the error's data.
_RPC_CODES = {"permission": -32001, "transient": -32002, "validation": -32602, "business": -32003}


@dataclass(frozen=True)
class Backend:
    """What the handlers read through, injected so tests can supply their own."""

    connect: Callable[[], psycopg.Connection]
    embedder: Embedder
    # Q4 (#10b): the fixtures the manifest's `dataset_sha256` is checked against.
    fixtures_dir: Path = FIXTURES_DIR


class Searcher(Protocol):
    """What a search runs through. Production searches the index; tests inject their own."""

    def search(
        self, query: str, *, audience: str, region: str | None
    ) -> Sequence[RetrievedChunk]: ...


class IndexSearcher:
    """The production `Searcher`: hybrid retrieval over the index for the token's caller."""

    def __init__(self, backend: Backend) -> None:
        self.backend = backend

    def search(self, query: str, *, audience: str, region: str | None) -> Sequence[RetrievedChunk]:
        with self.backend.connect() as conn:
            return retrieve_context(
                conn,
                self.backend.embedder,
                query,
                region=cast(ShopperRegion | None, region),
                audience=cast(Audience, audience),
            )


ToolHandler = Callable[["RagServer", Claims, Mapping[str, Any]], Awaitable[t.CallToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    tool: t.Tool
    handler: ToolHandler


def error_result(error: ToolError) -> t.CallToolResult:
    """The envelope as JSON text, which the model sees, and as `structuredContent` for MCP
    clients (S0 item 5: on an error the CLI shows the model the text)."""
    envelope = error.to_envelope()
    return t.CallToolResult(
        content=[t.TextContent(type="text", text=json.dumps(envelope))],
        structured_content=envelope,
        is_error=True,
    )


def text_result(text: str) -> t.CallToolResult:
    """A success is text only: with `structuredContent` the CLI would show the model that JSON
    instead of the text (S0 item 5)."""
    return t.CallToolResult(content=[t.TextContent(type="text", text=text)], is_error=False)


def _rpc_error(error: ToolError) -> MCPError:
    return MCPError(_RPC_CODES[error.category], error.message, error.to_envelope())


def claims_of(ctx: Any) -> Claims:
    """The verified caller, set by the token gate. A request that reached a handler without one
    is refused (it cannot happen through `RagServer.app`)."""
    request = getattr(ctx, "request", None)
    state = getattr(request, "scope", {}).get("state", {}) if request is not None else {}
    claims = state.get(CLAIMS_KEY) if isinstance(state, dict) else None
    if not isinstance(claims, Claims):
        raise not_allowed()
    return claims


class RagServer:
    def __init__(
        self,
        backend: Backend,
        *,
        key: bytes,
        tools: Mapping[str, ToolSpec] | None = None,
        ledger: SearchLedger | None = None,
        searcher: Searcher | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.backend = backend
        self._searcher = searcher
        self.ledger = ledger or SearchLedger(clock=clock)
        self._key = key
        self._tools = dict(tools or {})
        self._clock = clock

    def search(self, query: str, claims: Claims) -> Sequence[RetrievedChunk]:
        """One search for the token's caller (hard rule 7): audience and region from the token."""
        searcher = self._searcher or IndexSearcher(self.backend)
        return searcher.search(query, audience=claims.aud, region=claims.region)

    # ---- MCP handlers --------------------------------------------------------------------------

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
            return await self._tools[params.name].handler(self, claims, params.arguments or {})
        except ToolError as error:
            return error_result(error)
        except Exception as error:  # noqa: BLE001 - its text must not reach the caller
            log.error("tool failed: %s", type(error).__name__)
            return error_result(tool_failed())

    async def list_resources(self, ctx: Any, params: Any) -> t.ListResourcesResult:
        try:
            claims = claims_of(ctx)
        except ToolError as error:
            raise _rpc_error(error) from None
        resources = []
        if MANIFEST_URI in allowed_resources(claims.agent, SERVER_NAME):
            resources.append(
                t.Resource(
                    name="index-manifest",
                    uri=MANIFEST_URI,
                    mime_type="application/json",
                    description=(
                        "The live index's version, embedder, retriever and chunk counts by "
                        "source type, for the caller's audience only."
                    ),
                )
            )
        return t.ListResourcesResult(resources=resources)

    async def read_resource(
        self, ctx: Any, params: t.ReadResourceRequestParams
    ) -> t.ReadResourceResult:
        try:
            claims = claims_of(ctx)
        except ToolError as error:
            raise _rpc_error(error) from None
        uri = str(params.uri)
        if uri != MANIFEST_URI or uri not in allowed_resources(claims.agent, SERVER_NAME):
            log.warning("resource refused: permission")
            raise _rpc_error(resource_not_allowed())
        try:
            manifest = await asyncio.to_thread(self._manifest, claims)
        except Exception as error:  # noqa: BLE001 - its text must not reach the caller
            log.error("manifest failed: %s", type(error).__name__)
            raise _rpc_error(index_unavailable()) from None
        if manifest is None:
            raise _rpc_error(index_unavailable())
        return t.ReadResourceResult(
            contents=[
                t.TextResourceContents(
                    uri=MANIFEST_URI,
                    mime_type="application/json",
                    text=json.dumps(manifest, sort_keys=True),
                )
            ]
        )

    def _manifest(self, claims: Claims) -> dict[str, Any] | None:
        with self.backend.connect() as conn:
            manifest = store.index_manifest(conn, claims.aud)
        if manifest is None:
            return None
        return {
            **manifest,
            "retriever_id": retriever_id(self.backend.embedder),
            "dataset_verified": self._dataset_verified(manifest.get("dataset_sha256")),
        }

    def _dataset_verified(self, stored: object) -> bool:
        """Q4 (#10b, decision D4): true only when the index's stored `dataset_sha256` is the hash of
        the fixtures on disk, recomputed from their bytes now. Any mismatch or failure is false,
        never an error, so the manifest stays readable."""
        try:
            verified = verify_dataset(self.backend.fixtures_dir)
        except Exception as error:  # noqa: BLE001 - unverifiable is false; its text is not logged
            log.warning("dataset not verified: %s", type(error).__name__)
            return False
        return isinstance(stored, str) and stored == verified

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
        return token_gate(inner, self._key, self._clock)


def token_gate(app: ASGIApp, key: bytes, clock: Callable[[], float]) -> ASGIApp:
    """The shared gate (`northwind_common.mcp_gate`, moved there at #10b), bound to this server:
    a token minted for another server is a bare 401 here (security review L4)."""
    return mcp_gate.token_gate(app, key=key, clock=clock, server=SERVER_NAME)
