"""C8 (catalog): P0009's canary, ID and title never appear in any catalog tool output (CC-10;
RAG-11, hard rule 7). Every shopper-scoped tool, every region, with arguments built from the
product's title, ID, variant IDs and canary.

Row #16 (CHK-01, R6): the cart tools too, with a real session, so each call reaches its lookup. In
this store P0009's variants are priced and in stock in every region, and every public rain shell
(P0009's own subcategory) is out of stock, so an add's alternatives scan reaches P0009 and only
visibility can keep it out."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.testing import KEY, connect, running, token
from northwind_mcp_catalog.tools import TOOLS

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("RAG-11")]

REPO = Path(__file__).resolve().parents[3]
[ENTRY] = [
    e
    for e in json.loads((REPO / "data/fixtures/_answer_key.json").read_text(encoding="utf-8"))[
        "internal_only"
    ]
    if e.get("product_id") == "P0009"
]
CANARY = ENTRY["canary"]
TITLE = ENTRY["title"]
VARIANTS = [f"P0009-BLK-REG-{s}" for s in ("S", "M", "L", "XL")]
REGIONS = ("US", "CA", "UK", "EU")
VALUES = [TITLE, TITLE.upper(), "P0009", "p0009", CANARY, f"{TITLE} {CANARY}", *VARIANTS]
FORBIDDEN = (CANARY, "P0009", TITLE, "NWCANARY")


SESSION = "c" * 32
CURRENCY = {"US": "USD", "CA": "CAD", "UK": "GBP", "EU": "EUR"}


OUT_OF_STOCK = "P0001-BLK-REG-M"  # a public rain shell, at zero in `canary_store`


def canary_store() -> FixtureCatalog:
    """P0009 priced and stocked everywhere; every public rain shell at zero."""
    store = FixtureCatalog()
    for region in REGIONS:
        for variant in store.variants_of("P0009"):
            store.set_price(variant.variant_id, region, "10.00", CURRENCY[region])
            store.set_stock(variant.variant_id, region, 50)
        for product in store.products():
            if product.subcategory == "rain_shell" and product.visibility == "public":
                for variant in store.variants_of(product.product_id):
                    store.set_stock(variant.variant_id, region, 0)
    return store


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    with running(CatalogServer(canary_store(), key=KEY, tools=TOOLS)) as live_url:
        yield live_url


def arguments() -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for value in VALUES:
        calls.append(("get_product", {"product_id": value}))
        for tool in ("get_variant", "get_price", "check_stock"):
            calls.append((tool, {"variant_id": value}))
        for key in ("text", "brand", "category", "department", "subcategory", "colour", "size"):
            calls.append(("search_products", {key: value}))
        calls.append(("search_products", {"text": value, "limit": 20}))
    calls.append(("get_product", {"product_id": "P0009", "note": CANARY}))
    # Row #16: the cart tools, with the same values, then an out-of-stock add beside P0009.
    for n, value in enumerate(VALUES):
        key = f"canary-key-{n:03d}"  # hard rule 9: every cart write takes one
        calls.append(("add_to_cart", {"variant_id": value, "idempotency_key": f"{key}-a"}))
        calls.append(
            (
                "set_cart_quantity",
                {"variant_id": value, "quantity": 2, "idempotency_key": f"{key}-s"},
            )
        )
        calls.append(("remove_from_cart", {"variant_id": value, "idempotency_key": f"{key}-r"}))
    calls.append(("add_to_cart", {"variant_id": OUT_OF_STOCK, "idempotency_key": "canary-oos-1"}))
    calls.append(
        (
            "add_to_cart",
            {"variant_id": "P0009-BLK-REG-M", "note": CANARY, "idempotency_key": "canary-note-1"},
        )
    )
    calls.append(("get_cart", {}))
    # Row #11: the live filter too (refused to this agent; `shopper-query` is checked below).
    calls.append(("eligible_products", {"price_max": 1_000_000}))
    # SA-01 PR 1: the portal's two reads too (refused to this agent; `shopper-portal` is below).
    calls.extend(portal_arguments())
    return calls


def portal_arguments() -> list[tuple[str, dict[str, Any]]]:
    """`browse_products` and `get_prices` with every canary value, P0009's ID included."""
    calls: list[tuple[str, dict[str, Any]]] = [("browse_products", {})]
    for value in VALUES:
        for key in ("text", "department"):
            calls.append(("browse_products", {key: value}))
        calls.append(("get_prices", {"product_id": value}))
    calls.append(("get_prices", {"product_id": "P0009"}))
    return calls


def test_the_canary_title_and_id_are_in_no_response_byte(url: str) -> None:
    calls = arguments()
    seen_tools = {name for name, _ in calls}
    assert seen_tools == set(TOOLS)

    async def go(region: str) -> list[str]:
        out: list[str] = []
        async with connect(url, token("shopper-assistant", region, session=SESSION)[0]) as client:
            for name, args in calls:
                result = await client.call_tool(name, args)
                out.append(json.dumps(result.model_dump(mode="json", by_alias=True)))
        return out

    for region in REGIONS:
        for (name, args), raw in zip(calls, asyncio.run(go(region)), strict=True):
            for needle in FORBIDDEN:
                assert needle not in raw, (region, name, args)
            if name == "add_to_cart" and args.get("variant_id") == OUT_OF_STOCK:
                assert "out_of_stock" in raw  # the alternatives scan really ran


def test_the_portal_reads_never_name_the_canary_or_its_product(url: str) -> None:
    """SA-01 PR 1: `browse_products` and `get_prices` as `shopper-portal`, their one agent, with
    every canary value and P0009's own ID: never the canary title or P0009, in any region."""
    calls = portal_arguments()

    async def go(region: str) -> list[str]:
        out: list[str] = []
        async with connect(url, token("shopper-portal", region, session=SESSION)[0]) as client:
            for name, args in calls:
                result = await client.call_tool(name, args)
                out.append(json.dumps(result.model_dump(mode="json", by_alias=True)))
        return out

    listed = 0
    for region in REGIONS:
        raws = asyncio.run(go(region))
        for (name, args), raw in zip(calls, raws, strict=True):
            for needle in FORBIDDEN:
                assert needle not in raw, (region, name, args)
        listed += raws[0].count("product_id")
    assert listed  # a positive control: the unfiltered browse really lists products


def test_the_live_filter_never_names_the_canary_or_its_product(url: str) -> None:
    """Row #11 (RAG-04): `eligible_products` as `shopper-query`, its one agent, with the widest
    bounds and with stock: never P0009, never the canary title, in any region."""

    async def go(region: str) -> list[str]:
        out: list[str] = []
        async with connect(url, token("shopper-query", region)[0]) as client:
            for args in ({"price_max": 1_000_000}, {"in_stock": True}, {}):
                result = await client.call_tool("eligible_products", args)
                assert not result.is_error
                out.extend(getattr(block, "text", "") for block in result.content)
        return out

    found = 0
    for region in ("US", "CA", "UK", "EU"):
        for text in asyncio.run(go(region)):
            assert CANARY not in text and "P0009" not in text
            found += len(json.loads(text)["products"])
    assert found  # a positive control: the filter does return products here
