"""The cart tools (row #16, CHK-01; decision 0019; PRD §7; hard rules 1, 2, 6, 7, 8 and 10).

`get_cart`, `add_to_cart`, `remove_from_cart` and `set_cart_quantity` write only the caller's own
cart (`cart_store`). None of them places, confirms or pays for an order, and none returns an order
ID or order state: placement is CHK-02 and CHK-05, behind a portal confirmation.

**Whose cart.** The session and region come only from the verified token (`Claims.sid`, `.region`).
No schema takes a cart, session or shopper ID, and an unknown argument is refused, so the model
can't name another shopper's cart. A token without a session gets `session_required`.

**Live stock (hard rule 2).** An add, and a quantity increase, read the live store's stock for the
caller's region at call time, less the units BE-04 holds live there (`holds.py`); a cart shows each
line's price, total and stock as read when it is shown. The cart never stores a price. Stock is
checked, not reserved.

**Out of stock.** An add the stock can't cover is refused: `business`, `out_of_stock` (none on hand)
or `insufficient_stock` (some, not enough). The envelope comes alone in the first content block, and
a second block holds one `<catalog_data>` label with the alternatives *the server* chose. They are
BE-04's substitutes (`northwind_backend.inventory.substitutes.find`, row #25; row #18, step 0 (b)),
so the chat offers what BE-04's rules choose: public only, the same department and subcategory, a
live price in the region within ±20% of the requested variant's, at most one variant per product,
ranked same product other colour, other product same size, same product other size, then other
product, and at most `MAX_ALTERNATIVES`. Prices come from this server's live store, and stock is
this server's region total, less live holds (`holds.py`), covering the quantity asked. BE-04's
checkout reserves from one node, so an alternative whose units are split across nodes can be offered
here and still be short at checkout (step 0's security review L1, the user's option 1 (a): owned by
row #21). The model never chooses or invents one.

**Visibility (hard rule 7).** An unknown or internal variant gets exactly `get_variant`'s
`not_found`, and alternatives come only from what the caller may see, so P0009 never appears.

**Arguments.** These use the same checks as the catalog tools: card data is refused first, then
unknown keys, then each value's type and range. No error echoes an argument.

**Idempotency (hard rule 9; the user, 2026-11-17).** Every write requires an `idempotency_key`, 8-64
characters of `[A-Za-z0-9_-]`. A key is remembered within the turn it was sent in (the `turn` of the
verified token), never across turns: a model has no memory between turns, so it could not keep a
key unique for a whole cart (re-review N1; the user's option A). Within one turn, the same key with
the same arguments returns the stored outcome and the cart changes once; the same key with other
arguments, or for another tool, is an `idempotency_conflict`. In a new turn the same key is a new
change. The last `MAX_KEYS` (turn, key) pairs per cart are kept. There is no dry run: a reversible
cart write doesn't need one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any
from weakref import WeakKeyDictionary

import mcp.types as t

from northwind_backend.inventory import substitutes
from northwind_backend.ports import PriceQuote
from northwind_common import catalog_tool
from northwind_common.errors import ToolError
from northwind_common.mcp_token import Claims
from northwind_mcp_catalog.cart_store import MAX_KEYS, MAX_LINES, MAX_QUANTITY, Cart
from northwind_mcp_catalog.live import Product, Variant
from northwind_mcp_catalog.server import CatalogServer, ToolSpec, json_result

GET_CART = "get_cart"
ADD_TO_CART = "add_to_cart"
REMOVE_FROM_CART = "remove_from_cart"
SET_CART_QUANTITY = "set_cart_quantity"

IDEMPOTENCY_KEY = "idempotency_key"
KEY = re.compile(r"[A-Za-z0-9_-]{8,64}", re.ASCII)
MAX_ALTERNATIVES = substitutes.MAX_SUBSTITUTES


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


def invalid_idempotency_key() -> ToolError:
    return _error(
        "validation",
        "invalid_idempotency_key",
        "Each cart change needs an idempotency_key of 8-64 letters, digits, '-' or '_'.",
    )


def idempotency_conflict() -> ToolError:
    return _error(
        "validation",
        "idempotency_conflict",
        "That idempotency_key was already used for a different cart change in this turn.",
    )


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


def _on_hand(
    server: CatalogServer, variant_id: str, region: str, held: Mapping[str, int] | None = None
) -> int:
    return server.on_hand(variant_id, region, held)  # live, less live holds, on every call


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
    server: CatalogServer,
    region: str,
    product: Product,
    variant: Variant,
    held: Mapping[str, int],
) -> dict[str, Any]:
    on_hand = _on_hand(server, variant.variant_id, region, held)
    return {**_item(product, variant), "on_hand": on_hand, "in_stock": on_hand > 0}


_SUBSTITUTE_CATALOGS: WeakKeyDictionary[CatalogServer, substitutes.SubstituteCatalog] = (
    WeakKeyDictionary()
)


def _substitute_catalog(server: CatalogServer) -> substitutes.SubstituteCatalog:
    """BE-04's matching shape, built once per server from this server's verified store (products
    and variants never change while it runs; only price and stock do)."""
    found = _SUBSTITUTE_CATALOGS.get(server)
    if found is None:
        products = server.catalog.products()
        found = substitutes.SubstituteCatalog.from_rows(
            (
                {
                    "product_id": p.product_id,
                    "department": p.department,
                    "subcategory": p.subcategory,
                    "attributes": dict(p.attributes),
                    "visibility": p.visibility,
                }
                for p in products
            ),
            (
                {
                    "variant_id": v.variant_id,
                    "product_id": v.product_id,
                    "colour": v.colour,
                    "size": v.size,
                    "fit": v.fit,
                    "visibility": v.visibility,
                }
                for p in products
                for v in server.catalog.variants_of(p.product_id)
            ),
        )
        _SUBSTITUTE_CATALOGS[server] = found
    return found


def alternatives(
    server: CatalogServer, claims: Claims, region: str, refused: Variant, wanted: int
) -> list[dict[str, Any]]:
    """The server's alternatives to `refused`: BE-04's substitutes on this server's live price
    and stock (see the module docstring), each still checked against what the caller may see."""
    if _catalog._visible_product(server, claims, refused.product_id) is None:
        return []
    held = server.held(region)  # one reading for every candidate (the call's, in a write)

    def price(variant_id: str, at: str) -> PriceQuote | None:
        found = server.catalog.price(variant_id, at)
        if found is None:
            return None
        return PriceQuote(variant_id, at, found.minor_units, found.currency)

    chosen = substitutes.find(
        refused.variant_id,
        region,
        wanted,
        lambda variant_id: _on_hand(server, variant_id, region, held),
        catalog=_substitute_catalog(server),
        price=price,
    )
    out: list[dict[str, Any]] = []
    for substitute in chosen:
        variant = _catalog._visible_variant(server, claims, substitute.variant_id)
        product = None if variant is None else server.catalog.product(variant.product_id)
        if variant is None or product is None or not _catalog._sees(claims, product.visibility):
            continue
        out.append(_alternative(server, region, product, variant, held))
    return out


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
    # A read never creates a cart, so reads cannot evict real carts (security review F4).
    # One holds reading for the whole view, so its lines agree (re-review N3).
    with server.reading(region), server.carts.open(session, region, create=False) as cart:
        value = view(server, claims, region, cart)
    return json_result(GET_CART, value)


def _add(
    server: CatalogServer, claims: Claims, region: str, cart: Cart, args: Mapping[str, Any]
) -> t.CallToolResult | dict[str, Any]:
    quantity = _quantity(args, default=1)
    variant = _variant(server, claims, args)
    if server.catalog.price(variant.variant_id, region) is None:
        raise _catalog.not_sold_in_region()
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
    return {"result": "added", "variant_id": variant.variant_id, "quantity": total}


def _remove(
    server: CatalogServer, claims: Claims, region: str, cart: Cart, args: Mapping[str, Any]
) -> t.CallToolResult | dict[str, Any]:
    variant = _variant(server, claims, args)
    if variant.variant_id not in cart.lines:
        raise not_in_cart()
    del cart.lines[variant.variant_id]
    return {"result": "removed", "variant_id": variant.variant_id}


def _set_quantity(
    server: CatalogServer, claims: Claims, region: str, cart: Cart, args: Mapping[str, Any]
) -> t.CallToolResult | dict[str, Any]:
    quantity = _quantity(args, default=None)
    variant = _variant(server, claims, args)
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
    return {"result": "updated", "variant_id": variant.variant_id, "quantity": quantity}


OUTCOME_KEYS = ("result", "variant_id", "quantity")


Write = Callable[
    [CatalogServer, Claims, str, Cart, Mapping[str, Any]], t.CallToolResult | dict[str, Any]
]


def _idempotent(tool: str, arguments: frozenset[str], write: Write) -> Any:
    """A cart write behind its idempotency key (hard rule 9; plan R1, R8).

    Card data and unknown arguments are refused first. Then the key is checked (`KEY`). Keys are
    remembered per (turn, key), the turn taken from the verified token (the user's option A for
    re-review N1). Under the cart's lock, a pair this cart has seen returns its stored outcome when
    the tool and the other arguments are the same, and `idempotency_conflict` when they are not; a
    new pair runs the write.
    Only a write that changed the cart is stored (the last `MAX_KEYS` per cart): a refusal or an
    error is not, so retrying it with the same key checks again.

    What is stored is the outcome only (`result`, `variant_id`, `quantity`), taken from the write's
    own value, never a re-parse (re-review N2), and never a cart view. A replay says so at the top
    level, `"result": "already_applied"` with the first outcome under `"original"`, plus the cart as
    it is now, read live: it can never hand back an old price, stock level or line, nor read as a
    fresh change (security review F1, F5; re-review N1)."""

    async def handler(
        server: CatalogServer, claims: Claims, args: Mapping[str, Any]
    ) -> t.CallToolResult:
        _catalog._checked(args, arguments | {IDEMPOTENCY_KEY})
        session, region = _caller(claims)
        key = args.get(IDEMPOTENCY_KEY)
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise invalid_idempotency_key()
        rest = {k: v for k, v in args.items() if k != IDEMPOTENCY_KEY}
        fingerprint = json.dumps([tool, rest], sort_keys=True, separators=(",", ":"), default=str)
        # Holds are read once, before the cart can change (security review M1).
        with server.reading(region), server.carts.open(session, region) as cart:
            remembered = (claims.turn, key)
            seen = cart.keys.get(remembered)
            if seen is not None:
                if seen[0] != fingerprint:
                    raise idempotency_conflict()
                cart.keys.move_to_end(remembered)
                replay = {
                    "result": "already_applied",
                    "original": dict(seen[1]),
                    "cart": view(server, claims, region, cart),
                }
                return json_result(tool, replay)
            written = write(server, claims, region, cart, rest)
            if isinstance(written, t.CallToolResult):  # a stock refusal: never stored
                return written
            outcome = {k: written[k] for k in OUTCOME_KEYS if k in written}
            cart.keys[remembered] = (fingerprint, outcome)
            while len(cart.keys) > MAX_KEYS:
                cart.keys.popitem(last=False)
            return json_result(tool, {**outcome, "cart": view(server, claims, region, cart)})

    return handler


add_to_cart = _idempotent(ADD_TO_CART, frozenset({"variant_id", "quantity"}), _add)
remove_from_cart = _idempotent(REMOVE_FROM_CART, frozenset({"variant_id"}), _remove)
set_cart_quantity = _idempotent(
    SET_CART_QUANTITY, frozenset({"variant_id", "quantity"}), _set_quantity
)


# ---- registry ----------------------------------------------------------------------------------

_VARIANT = {
    "type": "string",
    "minLength": 1,
    "maxLength": 80,
    "description": "A variant ID, e.g. P0001-BLK-REG-M.",
}
_QUANTITY = {"type": "integer", "minimum": 1, "maximum": MAX_QUANTITY}
_KEY = {
    "type": "string",
    "minLength": 8,
    "maxLength": 64,
    "pattern": "^[A-Za-z0-9_-]+$",
    "description": (
        "Make up a new random key of 8-64 letters and digits for every cart change; never take one "
        "from an example. A key is remembered only within this turn: send the same key again only "
        "to retry a change whose result you did not get in this turn. A result of "
        '"already_applied" means that key was already used in this turn: nothing changed now.'
    ),
}


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
            IDEMPOTENCY_KEY: _KEY,
        },
        ["variant_id", IDEMPOTENCY_KEY],
    ),
    REMOVE_FROM_CART: _schema(
        {"variant_id": _VARIANT, IDEMPOTENCY_KEY: _KEY}, ["variant_id", IDEMPOTENCY_KEY]
    ),
    SET_CART_QUANTITY: _schema(
        {
            "variant_id": _VARIANT,
            "quantity": {**_QUANTITY, "description": "The new number of units, 1-10."},
            IDEMPOTENCY_KEY: _KEY,
        },
        ["variant_id", "quantity", IDEMPOTENCY_KEY],
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
        '{"variant_id": "P0001-BLK-REG-M", "quantity": 1, "idempotency_key": "<new random key>"}.'
    ),
    REMOVE_FROM_CART: (
        "Remove one variant from the shopper's own cart. Use it only when the shopper asks to "
        "remove that item; never to make room or tidy up on your own. Example: "
        '{"variant_id": "P0001-BLK-REG-M", "idempotency_key": "<new random key>"}.'
    ),
    SET_CART_QUANTITY: (
        "Change how many units of one variant are in the shopper's own cart (1-10; use "
        "remove_from_cart for none). A larger quantity is checked against live stock first. Use it "
        "only when the shopper asks to change the quantity; do not use it to add a new item (use "
        "add_to_cart). Example: "
        '{"variant_id": "P0001-BLK-REG-M", "quantity": 2, "idempotency_key": "<new random key>"}.'
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
        reads_stock=True,
    )
    for name, handler in _HANDLERS.items()
}

# Last, on purpose: `tools` imports this module at its end for `CART_SPECS`, and the handlers above
# use its checks only when called. Either module can then be imported first.
from northwind_mcp_catalog import tools as _catalog  # noqa: E402
