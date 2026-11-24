"""`get_order`, the one northwind-orders tool (row #27, CHK-06; PRD §7 "get_order (own orders)").

The caller is the token's: its `shp` (the signed-in shopper) and `region`. The tool answers only for
that shopper's own order. Another shopper's order, an unknown number and a malformed one all get the
same `not_found` bytes (NFR-01). A token with no `shp` gets `sign_in_required`: the gateway attaches
this server only to a signed-in turn, so that is never a shopper's path.

Arguments (as mcp-catalog's, decision D3 at #10b): card data anywhere in them is refused first,
before the store is read or anything is logged (hard rule 1); an unknown key is refused; no error
text echoes an argument.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import mcp.types as t

from northwind_common.cards import holds_card_data
from northwind_common.errors import ToolError
from northwind_common.mcp_token import Claims
from northwind_common.tool_scopes import GET_ORDER
from northwind_mcp_orders.book import not_found
from northwind_mcp_orders.server import OrdersServer, ToolSpec, json_result

MAX_ID_CHARS = 32


def card_data_refused() -> ToolError:
    return ToolError(
        "validation",
        False,
        "card_data_refused",
        "Card details cannot be sent to an order tool. Payment happens only in the secure "
        "payment form.",
    )


def unknown_argument() -> ToolError:
    return ToolError(
        "validation",
        False,
        "unknown_argument",
        "The request has an argument this tool does not take.",
    )


def sign_in_required() -> ToolError:
    return ToolError(
        "permission", False, "sign_in_required", "Orders are shown only after sign-in."
    )


def _order_id(value: Any) -> str | None:
    """The order number as a shopper may type it: surrounding spaces, a leading `#` and lower case
    are forgiven. Anything else is not an order number."""
    if not isinstance(value, str) or len(value) > MAX_ID_CHARS:
        return None
    return value.strip().removeprefix("#").strip().upper()


async def get_order(
    server: OrdersServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    if holds_card_data(args):
        raise card_data_refused()
    if any(not isinstance(key, str) or key != "order_id" for key in args):
        raise unknown_argument()
    if claims.shp is None or claims.aud != "shopper":
        raise sign_in_required()
    order_id = _order_id(args.get("order_id"))
    if order_id is None:
        raise not_found()
    return json_result(
        GET_ORDER, server.book.view(order_id, shopper=claims.shp, region=claims.region)
    )


DESCRIPTION = (
    "Get the status and tracking of one of the signed-in shopper's own orders, by order number: "
    "the date it was placed, its status (processing, on_hold, confirmed, label_created, picked_up, "
    "in_transit, out_for_delivery or delivered), its lines, and the carrier's tracking scans. "
    "Always use this for order status, tracking or dates; never answer them from memory or "
    "retrieved text. It needs the order number: if the shopper has not given one, ask for it. "
    'Example: {"order_id": "O000800"}.'
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "order_id": {
            "type": "string",
            "description": "The order number the shopper gave, e.g. O000800.",
            "maxLength": MAX_ID_CHARS,
        }
    },
    "required": ["order_id"],
    "additionalProperties": False,
}

TOOLS: Mapping[str, ToolSpec] = {
    GET_ORDER: ToolSpec(
        tool=t.Tool(
            name=GET_ORDER,
            description=DESCRIPTION,
            input_schema=SCHEMA,
            annotations=t.ToolAnnotations(read_only_hint=True, open_world_hint=False),
        ),
        handler=get_order,
    )
}
