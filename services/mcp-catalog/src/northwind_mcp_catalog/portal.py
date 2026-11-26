"""The portal's own catalog reads for the shop pages (SA-01 PR 1; hard rules 2, 7, 10).

Scoped to `shopper-portal` only, the gateway's own agent (`tool_scopes.PORTAL_ONLY_TOOLS`): no model
holds a token for them, so they never join the assistant's tool list or any recorded request.

- `browse_products` lists every public product that matches an optional department and name text,
  each with its live lowest price in the token's region. `search_products` stays as the model
  sees it: its limit of 20 is below a department's size (Jackets has 33), and changing it would
  change the assistant's tool schema.
- `get_prices` gives one public product's live price per public variant in the token's region.

Prices come from the live store on every call, never from the index or a fixture text (hard
rule 2). A shopper sees only `public` records, and an internal product gets exactly the `not_found`
an unknown ID gets (hard rule 7). The result is plain JSON, not a `<catalog_data>` label: that
format is for the model's own tools.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import mcp.types as t

from northwind_common.mcp_token import Claims
from northwind_common.tool_scopes import BROWSE_PRODUCTS, GET_PRICES, REGION_CURRENCY
from northwind_mcp_catalog.live import Price, Product
from northwind_mcp_catalog.server import CatalogServer, ToolSpec
from northwind_mcp_catalog.tools import (
    MAX_FILTER_CHARS,
    PRODUCT_ID,
    _checked,
    _fold,
    _id,
    _matches,
    _schema,
    _sees,
    _string,
    _summary,
    _visible_product,
    invalid_argument,
    invalid_product_id,
    product_not_found,
    region_required,
)

BROWSE_FILTERS = ("department", "text")


def _plain(body: Mapping[str, Any]) -> t.CallToolResult:
    text = json.dumps(body, separators=(",", ":"), sort_keys=True)
    return t.CallToolResult(content=[t.TextContent(type="text", text=text)], is_error=False)


def _region(claims: Claims) -> str:
    if claims.region is None or claims.region not in REGION_CURRENCY:
        raise region_required()
    return claims.region


def _money(price: Price) -> dict[str, str]:
    return {"amount": price.amount, "currency": price.currency}


def _variant_prices(
    server: CatalogServer, claims: Claims, product: Product, region: str
) -> list[tuple[Any, Price | None]]:
    """Each visible variant with its live price in the region's own currency, or None."""
    currency = REGION_CURRENCY[region]
    out: list[tuple[Any, Price | None]] = []
    for variant in server.catalog.variants_of(product.product_id):
        if not _sees(claims, variant.visibility):
            continue
        price = server.catalog.price(variant.variant_id, region)  # live, on every call
        out.append((variant, price if price is not None and price.currency == currency else None))
    return out


async def browse_products(
    server: CatalogServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    _checked(args, frozenset(BROWSE_FILTERS))
    filters: dict[str, str] = {}
    for key in BROWSE_FILTERS:
        if key not in args:
            continue
        value = args[key]
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_FILTER_CHARS:
            raise invalid_argument()
        filters[key] = _fold(value)
    region = _region(claims)
    products = []
    for product in server.catalog.products():
        if not _sees(claims, product.visibility) or not _matches(product, filters):
            continue
        priced = [p for _, p in _variant_prices(server, claims, product, region) if p is not None]
        lowest = min(priced, key=lambda p: p.minor_units) if priced else None
        products.append(
            {**_summary(product), "from_price": None if lowest is None else _money(lowest)}
        )
    return _plain({"region": region, "products": products, "total": len(products)})


async def get_prices(
    server: CatalogServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    _checked(args, frozenset({"product_id"}))
    product_id = _id(args, "product_id", PRODUCT_ID, invalid_product_id())
    product = _visible_product(server, claims, product_id)
    if product is None:
        raise product_not_found()
    region = _region(claims)
    variants = [
        {
            "variant_id": variant.variant_id,
            "colour": variant.colour,
            "size": variant.size,
            "fit": variant.fit,
            "price": None if price is None else _money(price),
        }
        for variant, price in _variant_prices(server, claims, product, region)
    ]
    return _plain({"product_id": product.product_id, "region": region, "variants": variants})


_READ_ONLY = t.ToolAnnotations(read_only_hint=True, open_world_hint=False)

PORTAL_SPECS: Mapping[str, ToolSpec] = {
    BROWSE_PRODUCTS: ToolSpec(
        tool=t.Tool(
            name=BROWSE_PRODUCTS,
            description=(
                "The portal's own product list: every public product matching an optional "
                "department and name text, each with its live lowest price in the caller's region. "
                "Do not use it to answer a shopper: it is the shop pages' own read, never offered "
                'to a model. Example: {"department": "Jackets"}.'
            ),
            input_schema=_schema(
                {
                    "department": _string("e.g. 'Jackets'."),
                    "text": _string("Part of the product name."),
                },
                [],
            ),
            annotations=_READ_ONLY,
        ),
        handler=browse_products,
    ),
    GET_PRICES: ToolSpec(
        tool=t.Tool(
            name=GET_PRICES,
            description=(
                "The portal's own price read: one public product's live price per variant in the "
                "caller's region. Do not use it to answer a shopper: it is the product page's own "
                'read, never offered to a model. Example: {"product_id": "P0001"}.'
            ),
            input_schema=_schema(
                {"product_id": _string("A product ID, e.g. P0001.", 16)}, ["product_id"]
            ),
            annotations=_READ_ONLY,
        ),
        handler=get_prices,
    ),
}
