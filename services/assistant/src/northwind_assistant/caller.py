"""Who is asking. Built by the server, never from anything the shopper or the model sends.

`session` (row #16, CHK-01, decision 0019) is the gateway's session id, which the cart is keyed on.
It goes into the MCP token only: never into a prompt, a tool argument or a tool result, so it never
reaches the model's input."""

from __future__ import annotations

from dataclasses import dataclass

from northwind_rag.retrieval import Audience, ShopperRegion


@dataclass(frozen=True)
class Caller:
    region: ShopperRegion
    audience: Audience = "shopper"
    session: str | None = None
