"""The assistant's way to mcp-rag (CC-10 C1'): the endpoint, the per-turn token, one search.

`RagEndpoint` holds mcp-rag's URL and the token signing key (from the environment, hard rule 4),
and mints one token per turn from the server-built `Caller`: the token, not anything the model
sends, says who is asking. The CLI is given the token, never the key.

The MCP client library logs every request it sends, arguments included, at DEBUG. A shopper's
message is the first search's query, so its loggers are held at WARNING here: no shopper message
reaches a log at any level (NFR-04; hard rule 1 for a card-shaped string, which is refused locally
before it gets this far anyway).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from northwind_assistant.caller import Caller
from northwind_common.mcp_token import bearer, mint
from northwind_common.rag_tool import TOOL_NAME

AGENT = "shopper-assistant"
# Every logger under these names stays at WARNING or above, whatever the root level.
QUIET_LOGGERS = ("mcp.client", "mcp.shared", "httpx", "httpcore")


def quiet_client_logs() -> None:
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


quiet_client_logs()


@dataclass(frozen=True)
class RagEndpoint:
    url: str
    key: bytes = field(repr=False)

    def mint(self, caller: Caller) -> str:
        """A fresh token for one turn of this caller."""
        token, _ = mint(audience=caller.audience, region=caller.region, agent=AGENT, key=self.key)
        return token


@dataclass(frozen=True)
class SearchResult:
    is_error: bool
    text: str


class McpRagSearcher:
    """One turn's searches through mcp-rag, with that turn's token."""

    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._token = token

    def __repr__(self) -> str:
        return "McpRagSearcher(…)"  # the token is a credential

    async def search(self, query: str) -> SearchResult:
        """Raises on any transport failure (unreachable, 401, timeout); the caller maps it."""
        quiet_client_logs()  # again, in case logging was configured after import
        http = create_mcp_http_client(headers=bearer(self._token))
        async with Client(streamable_http_client(self._url, http_client=http)) as client:
            result = await client.call_tool(TOOL_NAME, {"query": query})
        text = "".join(getattr(block, "text", "") for block in result.content)
        return SearchResult(is_error=bool(result.is_error), text=text)
