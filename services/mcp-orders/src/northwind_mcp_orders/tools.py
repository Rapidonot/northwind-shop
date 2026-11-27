"""The northwind-orders tools (row #27): `get_order` (CHK-06; PRD §7 "get_order (own orders)") and
`request_return` (PR 2, CHK-07, decision 0024), which records the shopper's own return request.

The caller is the token's: its `shp` (the signed-in shopper) and `region`. Each answers only for
that shopper's own order. Another shopper's order, an unknown number and a malformed one all get the
same `not_found` bytes (NFR-01). A token with no `shp` gets `sign_in_required`: the gateway attaches
this server only to a signed-in turn, so that is never a shopper's path.

Arguments (as mcp-catalog's, decision D3 at #10b): card data anywhere in them is refused first,
before the store is read or anything is logged (hard rule 1); an unknown key is refused; no error
text echoes an argument.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from itertools import permutations
from typing import Any

import mcp.types as t

from northwind_common.cards import contains_card_data, holds_card_data
from northwind_common.errors import ToolError
from northwind_common.mcp_token import Claims
from northwind_common.order_tool import ORDER_ID
from northwind_common.returns_policy import CONDITIONS
from northwind_common.tool_scopes import GET_ORDER, LIST_ORDERS, REQUEST_RETURN
from northwind_mcp_orders.book import not_found
from northwind_mcp_orders.server import OrdersServer, ToolSpec, json_result
from northwind_mcp_orders.wording import worded

MAX_ID_CHARS = 32
MAX_VARIANT_CHARS = 64
MAX_SPLIT_CHARS = 256  # longer pairs are no split card number
RETURN_ARGUMENTS = frozenset({"order_id", "variant_id", "condition", "idempotency_key"})


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


# The user's D2 (b), 2026-11-26 (row #27 PR 2's security L4): a card split between a well-formed
# order number and a digit key passed both card checks, as the pair check skips an order number so
# a good request is never refused for the digits of its key. A key may hold at most 6 digits in a
# row (single separators joined, as `cards.py` joins them), so an order number's 6 digits and a
# key's join to 12 at most: never a card. The tool's text is unchanged (it is in every recorded
# request); the error says what to do. No recorded key is refused.
MAX_KEY_DIGITS = 6
_KEY_DIGITS = re.compile(r"\d(?:[\W_]?\d)*")


def key_digits_refused() -> ToolError:
    return ToolError(
        "validation",
        False,
        "invalid_idempotency_key",
        "A return request needs a new random idempotency_key with at most 6 digits in a row.",
    )


def too_many_digits(value: Any) -> bool:
    """More than `MAX_KEY_DIGITS` digits in a row. The user's D6 (F3): on every argument, as a card
    could also be split between a variant ID ("P4111-111111") and another; every real variant ID
    has at most 4 in a row and an order number 6, so no real request is refused."""
    if not isinstance(value, str | int) or isinstance(value, bool):
        return False  # the other checks refuse what is neither
    groups = (re.sub(r"\D", "", g) for g in _KEY_DIGITS.findall(str(value)))
    return any(len(group) > MAX_KEY_DIGITS for group in groups)


def argument_digits_refused() -> ToolError:
    return ToolError(
        "validation",
        False,
        "invalid_argument",
        "A variant ID or condition has more digits in a row than any real one.",
    )


def sign_in_required() -> ToolError:
    return ToolError(
        "permission", False, "sign_in_required", "Orders are shown only after sign-in."
    )


def normal_order_id(value: Any) -> str | None:
    """The order number as a shopper may type it: surrounding spaces, a leading `#` and lower case
    are forgiven. Anything else is not an order number."""
    if not isinstance(value, str) or len(value) > MAX_ID_CHARS:
        return None
    return value.strip().removeprefix("#").strip().upper()


_VARIANT = re.compile(r"P\d{4}(?:-[A-Z0-9]{1,16}){1,4}", re.ASCII)


def _well_formed(value: str) -> bool:
    return bool(ORDER_ID.fullmatch(value) or _VARIANT.fullmatch(value)) or value in CONDITIONS


def _card_across(args: Mapping[str, Any]) -> bool:
    """A card number split over two arguments, which the argument names between them hide from
    `holds_card_data`. A pair counts only if neither is an order number, a variant ID or a
    condition, so a good request is never refused for the digits in its key."""
    known = [args[key] for key in RETURN_ARGUMENTS if key in args]  # at most four
    values = [str(v) for v in known if isinstance(v, str | int)]
    return any(
        contains_card_data(first + second)
        for first, second in permutations(values, 2)
        if len(first) + len(second) <= MAX_SPLIT_CHARS
        and not (_well_formed(first) or _well_formed(second))
    )


def _checked(claims: Claims, args: Mapping[str, Any], allowed: frozenset[str]) -> tuple[str, str]:
    """The signed-in shopper and the order number, after the checks every tool makes, in order."""
    if holds_card_data(args):
        raise card_data_refused()
    if any(not isinstance(key, str) or key not in allowed for key in args):
        raise unknown_argument()
    if claims.shp is None or claims.aud != "shopper":
        raise sign_in_required()
    order_id = normal_order_id(args.get("order_id"))
    if order_id is None:
        raise not_found()
    return claims.shp, order_id


async def get_order(
    server: OrdersServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    shopper, order_id = _checked(claims, args, frozenset({"order_id"}))
    view = server.book.view(order_id, shopper=shopper, region=claims.region)
    return json_result(GET_ORDER, worded(view))


MAX_HISTORY = 50


async def list_orders(
    server: OrdersServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    """SA-01 PR 2: the portal's order history, the signed-in shopper's own orders in their region,
    newest first. Portal-only (`tool_scopes.PORTAL_ORDER_TOOLS`): no model holds a token for it, so
    the result is plain JSON, not an `<order_data>` label. Each status has its plain words."""
    if holds_card_data(args):
        raise card_data_refused()
    if args:
        raise unknown_argument()
    if claims.shp is None or claims.aud != "shopper":
        raise sign_in_required()
    orders = server.book.history(shopper=claims.shp, region=claims.region)
    shown = [
        {**order, "status_text": worded({**order, "tracking": None})["status_text"]}
        for order in orders[:MAX_HISTORY]
    ]
    body = {"orders": shown, "total": len(orders)}
    text = json.dumps(body, separators=(",", ":"), sort_keys=True)
    return t.CallToolResult(content=[t.TextContent(type="text", text=text)], is_error=False)


async def request_return(
    server: OrdersServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    """The desk decides eligibility again itself: no argument says what was decided (rule 9)."""
    if _card_across(args):
        raise card_data_refused()
    shopper, order_id = _checked(claims, args, RETURN_ARGUMENTS)
    if too_many_digits(args.get("idempotency_key")):
        raise key_digits_refused()
    if too_many_digits(args.get("order_id")):
        raise not_found()  # the same bytes as any order number that is not the shopper's
    if any(too_many_digits(args.get(name)) for name in ("variant_id", "condition")):
        raise argument_digits_refused()
    made = server.desk.request(
        shopper=shopper,
        region=claims.region,
        turn=claims.turn,
        order_id=order_id,
        variant_id=args.get("variant_id"),
        condition=args.get("condition"),
        key=args.get("idempotency_key"),
    )
    return json_result(REQUEST_RETURN, made)


DESCRIPTION = (
    "Get the status and tracking of one of the signed-in shopper's own orders, by order number: "
    "the date it was placed, its status (processing, on_hold, confirmed, label_created, picked_up, "
    "in_transit, out_for_delivery or delivered), its lines, and the carrier's tracking scans. Each "
    "status has its plain words in status_text: give the shopper those, never the code. "
    "Always use this for order status, tracking or dates; never answer them from memory or "
    "retrieved text. It needs the order number: if the shopper has not given one, ask for it. "
    'Example: {"order_id": "O012345"}.'
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "order_id": {
            "type": "string",
            "description": "The order number the shopper gave, e.g. O012345.",
            "maxLength": MAX_ID_CHARS,
        }
    },
    "required": ["order_id"],
    "additionalProperties": False,
}

RETURN_DESCRIPTION = (
    "Record the signed-in shopper's own return request for one item of one of their orders. Use it "
    "only when the shopper asks to return the item, after evaluate_return_eligibility; the server "
    "decides eligibility again. The result is requested, or needs_review when a person has to look "
    "at it; an item that is not eligible is refused with the reason and nothing is recorded. It "
    "approves nothing and makes no label or refund. Do not use it to check eligibility. Example: "
    '{"order_id": "O012345", "variant_id": "P0000-BLK-REG-M", "condition": "sealed", '
    '"idempotency_key": "<new random key>"}.'
)

RETURN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "order_id": SCHEMA["properties"]["order_id"],
        "variant_id": {
            "type": "string",
            "description": "The variant ID of the order line, from get_order.",
            "maxLength": MAX_VARIANT_CHARS,
        },
        "condition": {
            "type": "string",
            "enum": list(CONDITIONS),
            "description": "The same condition as in evaluate_return_eligibility.",
        },
        "idempotency_key": {
            "type": "string",
            "minLength": 8,
            "maxLength": 64,
            "pattern": "^[A-Za-z0-9_-]+$",
            "description": (
                "Make up a new random key of 8-64 letters and digits for every request; never take "
                "one from an example. Send the same key again only to retry a request whose result "
                'you did not get in this turn. "already_requested" means nothing new was recorded.'
            ),
        },
    },
    "required": ["order_id", "variant_id", "condition", "idempotency_key"],
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
    ),
    LIST_ORDERS: ToolSpec(
        tool=t.Tool(
            name=LIST_ORDERS,
            description=(
                "The portal's own order history: the signed-in shopper's own orders, newest first, "
                "with each order's status and lines. Do not use it to answer a shopper: it is the "
                "orders page's own read, never offered to a model. It takes no arguments. "
                "Example: {}."
            ),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            annotations=t.ToolAnnotations(read_only_hint=True, open_world_hint=False),
        ),
        handler=list_orders,
    ),
    REQUEST_RETURN: ToolSpec(
        tool=t.Tool(
            name=REQUEST_RETURN,
            description=RETURN_DESCRIPTION,
            input_schema=RETURN_SCHEMA,
            annotations=t.ToolAnnotations(
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        ),
        handler=request_return,
    ),
}
