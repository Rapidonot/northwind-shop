"""The five catalog tools (CC-10, row #10b; PRD §7; hard rules 1, 2, 7, 10), and the registry that
also holds the four cart tools (row #16, CHK-01: `northwind_mcp_catalog.cart`).

All five catalog tools are read-only. The caller comes only from the token: the audience decides
what is visible, and the region decides price and stock. No schema takes a caller key.

**Visibility (hard rule 7, RAG-11).** A shopper sees only `public` records. A variant is visible
only when its product is too. For a shopper, an internal product or variant gets exactly the result
an unknown ID gets, so a refusal reveals nothing (P0009 and its canary never appear). Search leaves
internal products out. A staff caller sees them; no staff agent is scoped to these tools yet.

**Live values (hard rule 2).** `get_price` and `check_stock` read the live store on every call,
never the index.

**Arguments (decision D3).**
- Card data anywhere in them is refused first, before any lookup or log.
- An unknown key is refused, unlike mcp-rag, where extra keys stay ignored.
- Every value is type- and length-checked.
- No error text echoes an argument.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import mcp.types as t

from northwind_common.cards import holds_card_data
from northwind_common.errors import ToolError
from northwind_common.mcp_token import Claims
from northwind_mcp_catalog.live import Product, Variant
from northwind_mcp_catalog.server import CatalogServer, ToolSpec, json_result

SEARCH_PRODUCTS = "search_products"
GET_PRODUCT = "get_product"
GET_VARIANT = "get_variant"
CHECK_STOCK = "check_stock"
GET_PRICE = "get_price"

PRODUCT_ID = re.compile(r"P\d{4}", re.ASCII)
VARIANT_ID = re.compile(r"P\d{4}(?:-[A-Z0-9]{1,16}){1,4}", re.ASCII)
MAX_FILTER_CHARS = 64
MAX_LIMIT = 20
DEFAULT_LIMIT = 10
SEARCH_FILTERS = (
    "text",
    "category",
    "department",
    "subcategory",
    "brand",
    "gender",
    "colour",
    "size",
    "fit",
)


def _error(category: Any, code: str, message: str) -> ToolError:
    return ToolError(category, False, code, message)


def card_data_refused() -> ToolError:
    return _error(
        "validation",
        "card_data_refused",
        "Card details cannot be sent to a catalog tool. Payment happens only in the secure "
        "payment form.",
    )


def unknown_argument() -> ToolError:
    return _error(
        "validation", "unknown_argument", "The request has an argument this tool does not take."
    )


def invalid_argument() -> ToolError:
    return _error("validation", "invalid_argument", "An argument has the wrong type or length.")


def invalid_product_id() -> ToolError:
    return _error("validation", "invalid_product_id", "The product ID is malformed.")


def invalid_variant_id() -> ToolError:
    return _error("validation", "invalid_variant_id", "The variant ID is malformed.")


def product_not_found() -> ToolError:
    return _error("validation", "not_found", "No product with that ID.")


def variant_not_found() -> ToolError:
    return _error("validation", "not_found", "No variant with that ID.")


def not_sold_in_region() -> ToolError:
    return _error("business", "not_sold_in_region", "This variant is not sold in your region.")


def region_required() -> ToolError:
    return _error(
        "validation", "region_required", "This caller has no region to price or stock in."
    )


# ---- argument checks ---------------------------------------------------------------------------


def _checked(args: Mapping[str, Any], allowed: frozenset[str]) -> Mapping[str, Any]:
    if holds_card_data(args):
        raise card_data_refused()
    if any(not isinstance(key, str) or key not in allowed for key in args):
        raise unknown_argument()
    return args


def _id(args: Mapping[str, Any], key: str, pattern: re.Pattern[str], bad: ToolError) -> str:
    value = args.get(key)
    if not isinstance(value, str) or len(value) > 80 or not pattern.fullmatch(value):
        raise bad
    return value


def _sees(claims: Claims, visibility: str) -> bool:
    """Only `public` passes for a shopper; any other value, known or not, is hidden."""
    return visibility == "public" or claims.aud == "staff"


def _visible_product(server: CatalogServer, claims: Claims, product_id: str) -> Product | None:
    product = server.catalog.product(product_id)
    return product if product is not None and _sees(claims, product.visibility) else None


def _visible_variant(server: CatalogServer, claims: Claims, variant_id: str) -> Variant | None:
    variant = server.catalog.variant(variant_id)
    if variant is None or not _sees(claims, variant.visibility):
        return None
    if _visible_product(server, claims, variant.product_id) is None:
        return None
    return variant


# ---- views -------------------------------------------------------------------------------------


def _summary(product: Product) -> dict[str, Any]:
    return {
        "product_id": product.product_id,
        "name": product.name,
        "brand": product.brand,
        "category": product.category,
        "department": product.department,
        "subcategory": product.subcategory,
    }


def _variant_view(variant: Variant) -> dict[str, Any]:
    return {
        "variant_id": variant.variant_id,
        "product_id": variant.product_id,
        "sku": variant.sku,
        "colour": variant.colour,
        "colour_family": variant.colour_family,
        "fit": variant.fit,
        "size": variant.size,
    }


def _fold(value: str) -> str:
    return value.strip().casefold()


def _matches(product: Product, filters: Mapping[str, str]) -> bool:
    """Every filter must match; a field the product does not have (null, as `gender` is for home
    goods) matches no filter value."""
    fields: dict[str, str | None] = {
        "category": product.category,
        "department": product.department,
        "subcategory": product.subcategory,
        "brand": product.brand,
        "gender": product.gender,
    }
    lists = {"colour": product.colours, "size": product.sizes, "fit": product.fits}
    for key, wanted in filters.items():
        if key == "text":
            if wanted not in _fold(product.name):
                return False
        elif key in fields:
            value = fields[key]
            if value is None or _fold(value) != wanted:
                return False
        elif wanted not in {_fold(v) for v in lists[key]}:
            return False
    return True


# ---- handlers ----------------------------------------------------------------------------------


async def search_products(
    server: CatalogServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    _checked(args, frozenset({*SEARCH_FILTERS, "limit"}))
    filters: dict[str, str] = {}
    for key in SEARCH_FILTERS:
        if key not in args:
            continue
        value = args[key]
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_FILTER_CHARS:
            raise invalid_argument()
        filters[key] = _fold(value)
    limit = args.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise invalid_argument()
    found = [
        p for p in server.catalog.products() if _sees(claims, p.visibility) and _matches(p, filters)
    ]
    return json_result(
        SEARCH_PRODUCTS,
        {
            "products": [_summary(p) for p in found[:limit]],
            "returned": min(len(found), limit),
            "more": len(found) > limit,
        },
    )


async def get_product(
    server: CatalogServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    _checked(args, frozenset({"product_id"}))
    product_id = _id(args, "product_id", PRODUCT_ID, invalid_product_id())
    product = _visible_product(server, claims, product_id)
    if product is None:
        raise product_not_found()
    variants = [
        v.variant_id
        for v in server.catalog.variants_of(product.product_id)
        if _sees(claims, v.visibility)
    ]
    return json_result(
        GET_PRODUCT,
        {
            **_summary(product),
            "gender": product.gender,
            "description": product.description,
            "attributes": dict(product.attributes),
            "colours": list(product.colours),
            "sizes": list(product.sizes),
            "fits": list(product.fits),
            "regions": list(product.regions),
            "care_guide_id": product.care_guide_id,
            "size_guide_id": product.size_guide_id,
            "variant_ids": variants,
        },
    )


async def get_variant(
    server: CatalogServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    _checked(args, frozenset({"variant_id"}))
    variant_id = _id(args, "variant_id", VARIANT_ID, invalid_variant_id())
    variant = _visible_variant(server, claims, variant_id)
    if variant is None:
        raise variant_not_found()
    return json_result(GET_VARIANT, _variant_view(variant))


def _priced_variant(server: CatalogServer, claims: Claims, args: Mapping[str, Any]) -> str:
    """The variant ID, once it is visible to the caller and the caller has a region."""
    _checked(args, frozenset({"variant_id"}))
    variant_id = _id(args, "variant_id", VARIANT_ID, invalid_variant_id())
    if _visible_variant(server, claims, variant_id) is None:
        raise variant_not_found()
    if claims.region is None:
        raise region_required()
    return variant_id


async def get_price(
    server: CatalogServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    variant_id = _priced_variant(server, claims, args)
    region = claims.region or ""
    price = server.catalog.price(variant_id, region)  # live, on every call
    if price is None:
        raise not_sold_in_region()
    return json_result(
        GET_PRICE,
        {
            "variant_id": variant_id,
            "region": region,
            "amount": price.amount,
            "currency": price.currency,
        },
    )


async def check_stock(
    server: CatalogServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    variant_id = _priced_variant(server, claims, args)
    region = claims.region or ""
    # Sold in a region means priced there; stock with no row there is zero.
    if server.catalog.price(variant_id, region) is None:
        raise not_sold_in_region()
    on_hand = server.catalog.stock(variant_id, region) or 0  # live, on every call
    return json_result(
        CHECK_STOCK,
        {"variant_id": variant_id, "region": region, "on_hand": on_hand, "in_stock": on_hand > 0},
    )


# ---- registry ----------------------------------------------------------------------------------


def _string(description: str, max_length: int = MAX_FILTER_CHARS) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": max_length, "description": description}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


VARIANT_SCHEMA = _schema(
    {"variant_id": _string("A variant ID, e.g. P0001-BLK-REG-M.", 80)}, ["variant_id"]
)

_READ_ONLY = t.ToolAnnotations(read_only_hint=True, open_world_hint=False)

DESCRIPTIONS = {
    SEARCH_PRODUCTS: (
        "Find products in the Northwind catalog by structured filters: text (part of the product "
        "name), category, department, subcategory, brand, gender, colour, size, fit; limit 1-20 "
        "(default 10). Filters match exactly, ignoring case. Returns product summaries with IDs, "
        "no prices. Use it to find candidates before get_product. Do not use it for prices or "
        "stock (use get_price and check_stock) or for questions about care, fit or policies (use "
        'retrieve_context). Example: {"department": "Jackets", "colour": "Black", '
        '"size": "M"}.'
    ),
    GET_PRODUCT: (
        "Get one product's catalog record by product ID: name, brand, description, attributes, "
        "colours, sizes, fits, regions sold in, guide IDs and variant IDs. Use it after "
        "search_products. Do not use it for prices or stock, which it never returns. Example: "
        '{"product_id": "P0001"}.'
    ),
    GET_VARIANT: (
        "Get one variant (a product in one colour, fit and size) by variant ID: SKU, colour, fit "
        "and size. Use it to confirm which variant a shopper means. Do not use it for prices or "
        'stock. Example: {"variant_id": "P0001-BLK-REG-M"}.'
    ),
    CHECK_STOCK: (
        "Check live stock for one variant in the shopper's own region: units on hand and whether "
        "it is in stock. Always use this for availability; never answer stock from memory or "
        "retrieved text. Not for other regions: the region comes from the session. Example: "
        '{"variant_id": "P0001-BLK-REG-M"}.'
    ),
    GET_PRICE: (
        "Get the live price of one variant in the shopper's own region: amount and currency. "
        "Always use this for prices; never quote a price from memory or retrieved text. Not for "
        "other regions: the region comes from the session. Example: "
        '{"variant_id": "P0001-BLK-REG-M"}.'
    ),
}

SCHEMAS = {
    SEARCH_PRODUCTS: _schema(
        {
            "text": _string("Part of the product name, e.g. 'rain jacket'."),
            "category": _string("e.g. 'apparel' or 'home'."),
            "department": _string("e.g. 'Jackets'."),
            "subcategory": _string("e.g. 'rain_shell'."),
            "brand": _string("e.g. 'Tidewick'."),
            "gender": _string("e.g. 'unisex'."),
            "colour": _string("e.g. 'Black'."),
            "size": _string("e.g. 'M'."),
            "fit": _string("e.g. 'regular'."),
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
        },
        [],
    ),
    GET_PRODUCT: _schema({"product_id": _string("A product ID, e.g. P0001.", 16)}, ["product_id"]),
    GET_VARIANT: VARIANT_SCHEMA,
    CHECK_STOCK: VARIANT_SCHEMA,
    GET_PRICE: VARIANT_SCHEMA,
}

_HANDLERS = {
    SEARCH_PRODUCTS: search_products,
    GET_PRODUCT: get_product,
    GET_VARIANT: get_variant,
    CHECK_STOCK: check_stock,
    GET_PRICE: get_price,
}

READ_TOOLS: Mapping[str, ToolSpec] = {
    name: ToolSpec(
        tool=t.Tool(
            name=name,
            description=DESCRIPTIONS[name],
            input_schema=SCHEMAS[name],
            annotations=_READ_ONLY,
        ),
        handler=handler,
    )
    for name, handler in _HANDLERS.items()
}

# Row #16 (CHK-01): the cart tools, on this server beside the live store whose stock they check
# (decision 0019). Imported last: `cart` uses the checks above.
from northwind_mcp_catalog.cart import CART_SPECS  # noqa: E402

TOOLS: Mapping[str, ToolSpec] = {**READ_TOOLS, **CART_SPECS}
