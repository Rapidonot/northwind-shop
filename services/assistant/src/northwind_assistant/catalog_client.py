"""The assistant's way to mcp-catalog (row #12, RAG-09; CC-10 Q1): the endpoint and its token.

The CLI calls the five read-only catalog tools itself, over HTTP with this token; the assistant
process never sends a catalog request. As with `RagEndpoint`, the token, not anything the model
sends, says who is asking: audience and region come from the server-built `Caller`, so a price or
stock level is always the shopper's own region's (hard rules 2 and 7). The token is minted for
northwind-catalog only, and mcp-rag refuses it (#10b, L4). The CLI gets the token, never the key.
Row #16: the caller's session rides in the token too, so the cart tools act on the shopper's own
cart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import AGENT, quiet_client_logs
from northwind_common import catalog_tool
from northwind_common.mcp_token import CATALOG_SERVER, bearer, mint
from northwind_common.tool_scopes import GET_CART

# Row #16 (CHK-01): the gateway's own agent for reading a cart into the chat panel; never a model.
PORTAL_AGENT = "shopper-portal"


@dataclass(frozen=True)
class CatalogEndpoint:
    url: str
    key: bytes = field(repr=False)

    def mint(self, caller: Caller) -> str:
        """A fresh northwind-catalog token for one turn of this caller."""
        token, _ = mint(
            audience=caller.audience,
            region=caller.region,
            agent=AGENT,
            key=self.key,
            server=CATALOG_SERVER,
            session=caller.session,
        )
        return token


@dataclass(frozen=True)
class CartReader:
    url: str
    key: bytes = field(repr=False)

    """The gateway's read of a shopper's cart (row #16): `get_cart` as `shopper-portal`, whose only
    tool it is, with the caller's session and region in the token."""

    async def read(self, caller: Caller) -> dict[str, Any] | None:
        """The caller's cart as `get_cart` returned it, or None for anything else. Raises on a
        transport failure; the route maps every failure the same way."""
        if caller.session is None:
            return None
        token, _ = mint(
            audience="shopper",
            region=caller.region,
            agent=PORTAL_AGENT,
            key=self.key,
            server=CATALOG_SERVER,
            session=caller.session,
        )
        quiet_client_logs()
        # Closed here: `streamable_http_client` leaves a client it was given open, so each read
        # leaked a connection pool (SA-01 PR 1 security review F4, fixed in PR 2).
        async with (
            httpx2.AsyncClient(
                headers=bearer(token),
                timeout=httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
                trust_env=False,  # an HTTP_PROXY in the environment must never carry the token
            ) as http,
            Client(streamable_http_client(self.url, http_client=http)) as client,
        ):
            result = await client.call_tool(GET_CART, {})
        if result.is_error or len(result.content) != 1:
            return None
        labelled = catalog_tool.unlabel(getattr(result.content[0], "text", ""))
        if labelled is None or labelled[0] != GET_CART or not isinstance(labelled[1], dict):
            return None
        body: dict[str, Any] = labelled[1]
        return body if body.get("region") == caller.region else None
