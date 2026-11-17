"""The cart tools (row #16, CHK-01; decision 0019; PRD §7; hard rules 1, 2, 6, 7, 8 and 10).

`get_cart`, `add_to_cart`, `remove_from_cart` and `set_cart_quantity` write only the caller's own
cart (`cart_store`). None of them places, confirms or pays for an order, and none returns an order
ID or order state: placement is CHK-02 and CHK-05, behind a portal confirmation.

**Whose cart.** The session and region come only from the verified token (`Claims.sid`, `.region`).
No schema takes a cart, session or shopper ID, and an unknown argument is refused, so the model
can't name another shopper's cart. A token without a session gets `session_required`.

**Live stock (hard rule 2).** An add, and a quantity increase, read the live store's stock for the
caller's region at call time; a cart shows each line's price, total and stock as read when it is
shown. The cart never stores a price. Stock is checked, not reserved.

**Out of stock.** An add the stock can't cover is refused: `business`, `out_of_stock` (none on hand)
or `insufficient_stock` (some, not enough). The envelope comes alone in the first content block, and
a second block holds one `<catalog_data>` label with the alternatives *the server* chose:
1. up to `MAX_SAME_PRODUCT` other variants of the same product, same colour and fit first, then by
   variant ID;
2. then similar products, by product ID: same department and subcategory (`search_products`'s own
   matching), each with its first variant of the same size, then any size, by variant ID.

Up to `MAX_ALTERNATIVES` in all. Every alternative is visible to the caller, sold in the region and
has stock for the quantity asked. The model never chooses or invents one.

**Visibility (hard rule 7).** An unknown or internal variant gets exactly `get_variant`'s
`not_found`, and alternatives come only from what the caller may see, so P0009 never appears.

**Arguments.** These use the same checks as the catalog tools: card data is refused first, then
unknown keys, then each value's type and range. No error echoes an argument.

Idempotency keys and dry runs are not here (the user's scope cut, 2026-11-17): an open item for
CHK-05 and hardening.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import mcp.types as t

from northwind_common import catalog_tool
from northwind_common.errors import ToolError
from northwind_common.mcp_token import Claims
from northwind_mcp_catalog.cart_store import MAX_LINES, MAX_QUANTITY, Cart
from northwind_mcp_catalog.live import Product, Variant
from northwind_mcp_catalog.server import CatalogServer, ToolSpec, json_result

GET_CART = "get_cart"
ADD_TO_CART = "add_to_cart"
REMOVE_FROM_CART = "remove_from_cart"
SET_CART_QUANTITY = "set_cart_quantity"

MAX_SAME_PRODUCT = 3
MAX_ALTERNATIVES = 5


def _error(category: Any, code: str, message: str) -> ToolError:
    return ToolError(category, False, code, message)


def session_required() -> ToolError:
    return _error("permission", "session_required", "The cart needs a shopper session.")


def invalid_quantity() -> ToolError:
    return _error(
        "validation",
        "invalid_quantity",
        f"The quantity must be a whole number from 1 to {MAX_QUANTITY}.",
    )


def cart_full() -> ToolError:
    return _error("business", "cart_full", f"The cart holds at most {MAX_LINES} different items.")


def line_limit() -> ToolError:
    return _error("business", "line_limit", f"A cart line holds at most {MAX_QUANTITY} units.")


def not_in_cart() -> ToolError:
    return _error("business", "not_in_cart", "That item is not in the cart.")


def out_of_stock() -> ToolError:
    return _error("business", "out_of_stock", "That item is out of stock in your region.")


def insufficient_stock() -> ToolError:
    return _error(
        "business",
        "insufficient_stock",
        "There is not enough stock in your region for that quantity.",
    )


# ---- checks ------------------------------------------------------------------------------------


def _caller(claims: Claims) -> tuple[str, str]:
    """The cart's key, from the token only."""
    if claims.sid is None or claims.aud != "shopper":
        raise session_required()
    if claims.region is None:
        raise _catalog.region_required()
    return claims.sid, claims.region


def _quantity(args: Mapping[str, Any], *, default: int | None) -> int:
    value = args.get("quantity", default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_QUANTITY:
        raise invalid_quantity()
    return value


def _variant(server: CatalogServer, claims: Claims, args: Mapping[str, Any]) -> Variant:
    """The variant, if the caller may see it, with `get_variant`'s own errors otherwise."""
    variant_id = _catalog._id(
        args, "variant_id", _catalog.VARIANT_ID, _catalog.invalid_variant_id()
    )
    variant = _catalog._visible_variant(server, claims, variant_id)
    if variant is None:
        raise _catalog.variant_not_found()
    return variant


# ---- views -------------------------------------------------------------------------------------


def _amount(minor: int) -> str:
    units, cents = divmod(minor, 100)
    return f"{units}.{cents:02d}"


def _on_hand(server: CatalogServer, variant_id: str, region: str) -> int:
    return server.catalog.stock(variant_id, region) or 0  # live, on every call


def _item(product: Product, variant: Variant) -> dict[str, Any]:
    return {
        "variant_id": variant.variant_id,
        "product_id": variant.product_id,
        "name": product.name,
        "colour": variant.colour,
        "size": variant.size,
        "fit": variant.fit,
    }


def view(server: CatalogServer, claims: Claims, region: str, cart: Cart) -> dict[str, Any]:
    """The cart as the caller sees it now: each line's live unit price, line total and stock, and
    the subtotal of the lines sold in the region, in exact minor units. No session ID."""
    lines: list[dict[str, Any]] = []
    subtotal = 0
    currencies: set[str] = set()
    for variant_id, quantity in cart.lines.items():
        variant = _catalog._visible_variant(server, claims, variant_id)
        product = None if variant is None else server.catalog.product(variant.product_id)
        if variant is None or product is None:
            continue  # never shown, even if it stopped being visible after it was added
        price = server.catalog.price(variant_id, region)
        on_hand = _on_hand(server, variant_id, region)
        line = {
            **_item(product, variant),
            "quantity": quantity,
            "on_hand": on_hand,
            "in_stock": on_hand > 0,
            "unit_price": None if price is None else price.amount,
            "line_total": None if price is None else _amount(price.minor_units * quantity),
            "currency": None if price is None else price.currency,
        }
        if price is not None:
            subtotal += price.minor_units * quantity
            currencies.add(price.currency)
        lines.append(line)
    single = len(currencies) == 1
    return {
        "region": region,
        "lines": lines,
        "item_count": sum(line["quantity"] for line in lines),
        "subtotal": _amount(subtotal) if single else None,
        "currency": next(iter(currencies)) if single else None,
    }


def _alternative(
    server: CatalogServer, region: str, product: Product, variant: Variant
) -> dict[str, Any]:
    on_hand = _on_hand(server, variant.variant_id, region)
    return {**_item(product, variant), "on_hand": on_hand, "in_stock": on_hand > 0}


def alternatives(
    server: CatalogServer, claims: Claims, region: str, refused: Variant, wanted: int
) -> list[dict[str, Any]]:
    """The server's alternatives to `refused`, deterministic for one store state (see the module
    docstring)."""

    def offerable(variant: Variant) -> bool:
        return (
            variant.variant_id != refused.variant_id
            and _catalog._sees(claims, variant.visibility)
            and server.catalog.price(variant.variant_id, region) is not None
            and _on_hand(server, variant.variant_id, region) >= wanted
        )

    product = _catalog._visible_product(server, claims, refused.product_id)
    if product is None:
        return []
    same = sorted(
        (v for v in server.catalog.variants_of(product.product_id) if offerable(v)),
        key=lambda v: (not (v.colour == refused.colour and v.fit == refused.fit), v.variant_id),
    )
    found = [_alternative(server, region, product, v) for v in same[:MAX_SAME_PRODUCT]]
    like = {
        "department": _catalog._fold(product.department),
        "subcategory": _catalog._fold(product.subcategory),
    }
    for other in server.catalog.products():
        if len(found) >= MAX_ALTERNATIVES:
            break
        if other.product_id == product.product_id or not _catalog._sees(claims, other.visibility):
            continue
        if not _catalog._matches(other, like):
            continue
        choices = sorted(
            (v for v in server.catalog.variants_of(other.product_id) if offerable(v)),
            key=lambda v: (v.size != refused.size, v.variant_id),
        )
        if choices:
            found.append(_alternative(server, region, other, choices[0]))
    return found


def refusal(
    tool: str,
    error: ToolError,
    server: CatalogServer,
    claims: Claims,
    region: str,
    refused: Variant,
    wanted: int,
) -> t.CallToolResult:
    """An out-of-stock refusal: the fixed envelope alone in the first block, then one labelled block
    with the requested variant's live stock and the server's alternatives."""
    on_hand = _on_hand(server, refused.variant_id, region)
    data = {
        "region": region,
        "requested": {
            "variant_id": refused.variant_id,
            "on_hand": on_hand,
            "in_stock": on_hand > 0,
        },
        "alternatives": alternatives(server, claims, region, refused, wanted),
    }
    return t.CallToolResult(
        content=[
            t.TextContent(type="text", text=json.dumps(error.to_envelope())),
            t.TextContent(type="text", text=catalog_tool.label(tool, data)),
        ],
        is_error=True,
    )


def _stock_refusal(on_hand: int) -> ToolError:
    return out_of_stock() if on_hand <= 0 else insufficient_stock()


# ---- handlers ----------------------------------------------------------------------------------


async def get_cart(
    server: CatalogServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    _catalog._checked(args, frozenset())
    session, region = _caller(claims)
    with server.carts.open(session, region) as cart:
        value = view(server, claims, region, cart)
    return json_result(GET_CART, value)


async def add_to_cart(
    server: CatalogServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    _catalog._checked(args, frozenset({"variant_id", "quantity"}))
    session, region = _caller(claims)
    quantity = _quantity(args, default=1)
    variant = _variant(server, claims, args)
    if server.catalog.price(variant.variant_id, region) is None:
        raise _catalog.not_sold_in_region()
    with server.carts.open(session, region) as cart:
        current = cart.lines.get(variant.variant_id, 0)
        if current == 0 and len(cart.lines) >= MAX_LINES:
            raise cart_full()
        total = current + quantity
        if total > MAX_QUANTITY:
            raise line_limit()
        on_hand = _on_hand(server, variant.variant_id, region)
        if on_hand < total:
            return refusal(
                ADD_TO_CART, _stock_refusal(on_hand), server, claims, region, variant, quantity
            )
        cart.lines[variant.variant_id] = total
        value = {
            "result": "added",
            "variant_id": variant.variant_id,
            "quantity": total,
            "cart": view(server, claims, region, cart),
        }
    return json_result(ADD_TO_CART, value)


async def remove_from_cart(
    server: CatalogServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    _catalog._checked(args, frozenset({"variant_id"}))
    session, region = _caller(claims)
    variant = _variant(server, claims, args)
    with server.carts.open(session, region) as cart:
        if variant.variant_id not in cart.lines:
            raise not_in_cart()
        del cart.lines[variant.variant_id]
        value = {
            "result": "removed",
            "variant_id": variant.variant_id,
            "cart": view(server, claims, region, cart),
        }
    return json_result(REMOVE_FROM_CART, value)


async def set_cart_quantity(
    server: CatalogServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    _catalog._checked(args, frozenset({"variant_id", "quantity"}))
    session, region = _caller(claims)
    quantity = _quantity(args, default=None)
    variant = _variant(server, claims, args)
    with server.carts.open(session, region) as cart:
        current = cart.lines.get(variant.variant_id)
        if current is None:
            raise not_in_cart()
        if quantity > current:  # an increase is stock-checked; a decrease never needs to be
            on_hand = _on_hand(server, variant.variant_id, region)
            if on_hand < quantity:
                return refusal(
                    SET_CART_QUANTITY,
                    _stock_refusal(on_hand),
                    server,
                    claims,
                    region,
                    variant,
                    quantity,
                )
        cart.lines[variant.variant_id] = quantity
        value = {
            "result": "updated",
            "variant_id": variant.variant_id,
            "quantity": quantity,
            "cart": view(server, claims, region, cart),
        }
    return json_result(SET_CART_QUANTITY, value)


# ---- registry ----------------------------------------------------------------------------------

_VARIANT = {
    "type": "string",
    "minLength": 1,
    "maxLength": 80,
    "description": "A variant ID, e.g. P0001-BLK-REG-M.",
}
_QUANTITY = {"type": "integer", "minimum": 1, "maximum": MAX_QUANTITY}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


SCHEMAS = {
    GET_CART: _schema({}, []),
    ADD_TO_CART: _schema(
        {
            "variant_id": _VARIANT,
            "quantity": {**_QUANTITY, "description": "Units to add, 1-10 (default 1)."},
        },
        ["variant_id"],
    ),
    REMOVE_FROM_CART: _schema({"variant_id": _VARIANT}, ["variant_id"]),
    SET_CART_QUANTITY: _schema(
        {
            "variant_id": _VARIANT,
            "quantity": {**_QUANTITY, "description": "The new number of units, 1-10."},
        },
        ["variant_id", "quantity"],
    ),
}

DESCRIPTIONS = {
    GET_CART: (
        "Show the shopper's own cart: each item's variant, quantity, live unit price, line total "
        "and stock, and the subtotal. Use it when the shopper asks what is in their cart. Do not "
        "use it for a price or stock check on an item not in the cart (use get_price and "
        "check_stock). It takes no arguments: the cart is always the shopper's own. Example: {}."
    ),
    ADD_TO_CART: (
        "Add units of one variant to the shopper's own cart, after a live stock check in their "
        "region. Use it only when the shopper asks to add an item and has said which colour, size "
        "and fit; otherwise ask first. If stock can't cover it, the add is refused and the result "
        "lists alternatives in stock: offer only those. It never places an order. Example: "
        '{"variant_id": "P0001-BLK-REG-M", "quantity": 1}.'
    ),
    REMOVE_FROM_CART: (
        "Remove one variant from the shopper's own cart. Use it only when the shopper asks to "
        "remove that item; never to make room or tidy up on your own. Example: "
        '{"variant_id": "P0001-BLK-REG-M"}.'
    ),
    SET_CART_QUANTITY: (
        "Change how many units of one variant are in the shopper's own cart (1-10; use "
        "remove_from_cart for none). A larger quantity is checked against live stock first. Use it "
        "only when the shopper asks to change the quantity; do not use it to add a new item (use "
        "add_to_cart). Example: "
        '{"variant_id": "P0001-BLK-REG-M", "quantity": 2}.'
    ),
}

_READ = t.ToolAnnotations(read_only_hint=True, open_world_hint=False)
# A cart write can be undone by the shopper; it is not destructive and touches nothing outside.
_WRITE = t.ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

_HANDLERS = {
    GET_CART: get_cart,
    ADD_TO_CART: add_to_cart,
    REMOVE_FROM_CART: remove_from_cart,
    SET_CART_QUANTITY: set_cart_quantity,
}

CART_SPECS: Mapping[str, ToolSpec] = {
    name: ToolSpec(
        tool=t.Tool(
            name=name,
            description=DESCRIPTIONS[name],
            input_schema=SCHEMAS[name],
            annotations=_READ if name == GET_CART else _WRITE,
        ),
        handler=handler,
    )
    for name, handler in _HANDLERS.items()
}

# Last, on purpose: `tools` imports this module at its end for `CART_SPECS`, and the handlers above
# use its checks only when called. Either module can then be imported first.
from northwind_mcp_catalog import tools as _catalog  # noqa: E402
