"""Who is asking. Built by the server, never from anything the shopper or the model sends.

`session` (row #16, CHK-01, decision 0019) is the gateway's session id, which the cart is keyed on.
It goes into the MCP token only: never into a prompt, a tool argument or a tool result, so it never
reaches the model's input.

`shopper_id` (row #27, CHK-06, decision 0023) is the signed-in shopper, set only by the gateway's
demo sign-in. Like `session`, it goes into one MCP token only, northwind-orders' (`shp`), never into
a prompt, a tool argument or a tool result. With it, the turn gets the order tool and the prompt's
order section; without it, the turn is the anonymous one, byte for byte."""

from __future__ import annotations

from dataclasses import dataclass

from northwind_rag.retrieval import Audience, ShopperRegion


@dataclass(frozen=True)
class Caller:
    region: ShopperRegion
    audience: Audience = "shopper"
    session: str | None = None
    shopper_id: str | None = None
