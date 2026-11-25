"""The assistant's way to mcp-orders (row #27, CHK-06) and, from PR 2, to mcp-policy (CHK-07):
each endpoint and its token.

As with `CatalogEndpoint`, the CLI calls `get_order` itself, over HTTP with this token; the token,
not anything the model sends, says who is asking. It carries the signed-in shopper (`shp`) from the
server-built `Caller`, so the server answers only for that shopper's own orders. It is minted for
northwind-orders only, and mcp-rag and mcp-catalog refuse it. The CLI gets the token, never the key.

mcp-policy's is the same, minted for northwind-policy only: whether an item can be returned is
decided for that shopper's own order (decision 0024).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import AGENT
from northwind_common.mcp_token import ORDERS_SERVER, POLICY_SERVER, mint


def _signed_in_token(caller: Caller, key: bytes, server: str) -> str | None:
    """None without a signed-in shopper, so an anonymous turn never gets the server's tools."""
    if caller.shopper_id is None:
        return None
    token, _ = mint(
        audience=caller.audience,
        region=caller.region,
        agent=AGENT,
        key=key,
        server=server,
        shopper=caller.shopper_id,
    )
    return token


@dataclass(frozen=True)
class OrdersEndpoint:
    url: str
    key: bytes = field(repr=False)

    def mint(self, caller: Caller) -> str | None:
        """A fresh northwind-orders token for one turn of a signed-in caller; None without one, so
        an anonymous turn never gets the order tools."""
        return _signed_in_token(caller, self.key, ORDERS_SERVER)


@dataclass(frozen=True)
class PolicyEndpoint:
    url: str
    key: bytes = field(repr=False)

    def mint(self, caller: Caller) -> str | None:
        """A fresh northwind-policy token, as `OrdersEndpoint.mint`."""
        return _signed_in_token(caller, self.key, POLICY_SERVER)
