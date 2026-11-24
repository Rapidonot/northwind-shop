"""RAG-04 (row #11), hard rule 2: price and availability filters are resolved against live data.

`eligible_products` returns product IDs only, never a price or a count, from the live store in the
token's region. Only the server's own `shopper-query` agent may call it: the assistant's model never
sees it. A price or stock change is reflected on the next call, with no re-ingest.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from northwind_common.mcp_token import CATALOG_SERVER, mint
from northwind_common.tool_scopes import allowed_tools
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog, eligible_products
from northwind_mcp_catalog.testing import KEY

pytestmark = pytest.mark.req("RAG-04")

TOOL = "eligible_products"


def token(agent: str = "shopper-query", region: str = "US") -> str:
    minted, _ = mint(audience="shopper", region=region, agent=agent, key=KEY, server=CATALOG_SERVER)
    return minted


async def call(url: str, bearer: str, args: dict[str, Any]) -> tuple[bool, Any]:
    async with catalog_testing.connect(url, bearer) as client:
        result = await client.call_tool(TOOL, args)
    text = "".join(getattr(b, "text", "") for b in result.content)
    return bool(result.is_error), text


def test_only_the_query_agent_has_it() -> None:
    assert TOOL in allowed_tools("shopper-query", CATALOG_SERVER)
    assert allowed_tools("shopper-query", CATALOG_SERVER) == frozenset({TOOL})
    assert TOOL not in allowed_tools("shopper-assistant", CATALOG_SERVER)
    assert TOOL not in allowed_tools("shopper-portal", CATALOG_SERVER)


def test_ids_only_from_live_prices() -> None:
    store = FixtureCatalog()
    under = eligible_products(store, "US", currency="USD", price_min=None, price_max=5000)
    assert under and all(p.startswith("P") and len(p) == 5 for p in under)
    for product_id in under:
        prices = [store.price(v.variant_id, "US") for v in store.variants_of(product_id)]
        assert any(p is not None and p.minor_units <= 5000 for p in prices)
    assert "P0009" not in eligible_products(
        store, "US", currency="USD", price_min=None, price_max=None
    )


def test_a_price_change_is_seen_at_once() -> None:
    store = FixtureCatalog()
    [variant, *_] = store.variants_of("P0011")
    assert "P0011" not in eligible_products(
        store, "US", currency="USD", price_min=None, price_max=100
    )
    store.set_price(variant.variant_id, "US", "0.99", "USD")
    assert "P0011" in eligible_products(store, "US", currency="USD", price_min=None, price_max=100)


def test_stock_changes_are_seen_at_once() -> None:
    store = FixtureCatalog()
    variants = store.variants_of("P0011")
    for v in variants:
        store.set_stock(v.variant_id, "US", 0)
    assert "P0011" not in eligible_products(store, "US", currency="USD", in_stock=True)
    store.set_stock(variants[0].variant_id, "US", 3)
    assert "P0011" in eligible_products(store, "US", currency="USD", in_stock=True)


def test_price_and_stock_must_hold_for_the_same_variant() -> None:
    store = FixtureCatalog()
    cheap, dear, *_ = store.variants_of("P0011")
    for v in store.variants_of("P0011"):
        store.set_price(v.variant_id, "US", "500.00", "USD")
        store.set_stock(v.variant_id, "US", 0)
    store.set_price(cheap.variant_id, "US", "10.00", "USD")  # cheap, but none in stock
    store.set_stock(dear.variant_id, "US", 5)  # in stock, but dear
    found = eligible_products(store, "US", currency="USD", price_max=2000, in_stock=True)
    assert "P0011" not in found


def test_another_currency_matches_nothing() -> None:
    store = FixtureCatalog()
    assert eligible_products(store, "US", currency="GBP", price_max=100000) == []


def test_over_mcp_for_the_query_agent() -> None:
    server = catalog_testing.server(key=KEY)
    with catalog_testing.running(server) as url:
        is_error, text = asyncio.run(call(url, token(), {"price_max": 5000}))
    assert not is_error
    body = json.loads(text) if text.startswith("{") else text
    assert "products" in text and "amount" not in text and "on_hand" not in text, body


@pytest.mark.parametrize("agent", ["shopper-assistant", "shopper-portal"])
def test_refused_for_any_other_agent(agent: str) -> None:
    server = catalog_testing.server(key=KEY)
    with catalog_testing.running(server) as url:
        is_error, text = asyncio.run(call(url, token(agent), {"price_max": 5000}))
    assert is_error and "permission" in text


@pytest.mark.parametrize(
    "args",
    [
        {"price_max": -1},
        {"price_max": 0},
        {"price_max": 1_000_001},
        {"price_max": "50"},
        {"price_max": True},
        {"price_min": 9000, "price_max": 100},
        {"in_stock": "yes"},
        {"region": "UK"},
        {"price_max": 5000, "visibility": "internal"},
    ],
)
def test_bad_arguments_are_refused(args: dict[str, Any]) -> None:
    server = catalog_testing.server(key=KEY)
    with catalog_testing.running(server) as url:
        is_error, _ = asyncio.run(call(url, token(), args))
    assert is_error
