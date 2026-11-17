"""The assistant's way to mcp-catalog (row #12, RAG-09; CC-10 Q1): the endpoint and its token.

The CLI calls the five read-only catalog tools itself, over HTTP with this token; the assistant
process never sends a catalog request. As with `RagEndpoint`, the token, not anything the model
sends, says who is asking: audience and region come from the server-built `Caller`, so a price or
stock level is always the shopper's own region's (hard rules 2 and 7). The token is minted for
northwind-catalog only, and mcp-rag refuses it (#10b, L4). The CLI gets the token, never the key.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import AGENT
from northwind_common.mcp_token import CATALOG_SERVER, mint


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
        )
        return token
