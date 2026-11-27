"""SA-01 PR 1, adversarial tests of `browse_products` and `get_prices` (hard rules 2, 7, 8, 10).

The canary and internal product in every region; an unpriced product is null, not an error; no
region is a refusal, never an empty list; an unknown ID is not echoed; the result shapes carry no
stock or description; no token, or another agent's, gets nothing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from northwind_common.mcp_token import CATALOG_SERVER, mint
from northwind_common.tool_scopes import BROWSE_PRODUCTS, GET_PRICES
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.testing import KEY
from northwind_mcp_catalog.tools import TOOLS

pytestmark = pytest.mark.req("SA-01")

REPO = Path(__file__).resolve().parents[3]
[ENTRY] = [
    e
    for e in json.loads((REPO / "data/fixtures/_answer_key.json").read_text(encoding="utf-8"))[
        "internal_only"
    ]
    if e.get("product_id") == "P0009"
]
FORBIDDEN = (ENTRY["canary"], "P0009", ENTRY["title"], "NWCANARY")
REGIONS = ("US", "CA", "UK", "EU")
CURRENCY = {"US": "USD", "CA": "CAD", "UK": "GBP", "EU": "EUR"}
STORE = FixtureCatalog()


def bearer_for(agent: str = "shopper-portal", region: str | None = "US") -> str:
    minted, _ = mint(audience="shopper", region=region, agent=agent, key=KEY, server=CATALOG_SERVER)
    return minted


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    with catalog_testing.running(CatalogServer(STORE, key=KEY, tools=TOOLS)) as running:
        yield running


async def _call(url: str, bearer: str, tool: str, args: dict[str, Any]) -> tuple[bool, str]:
    async with catalog_testing.connect(url, bearer) as client:
        result = await client.call_tool(tool, args)
    return bool(result.is_error), "".join(getattr(b, "text", "") for b in result.content)


def call(url: str, tool: str, args: dict[str, Any], **kw: Any) -> tuple[bool, str]:
    return asyncio.run(_call(url, bearer_for(**kw), tool, args))


def ok(url: str, tool: str, args: dict[str, Any], **kw: Any) -> Any:
    is_error, text = call(url, tool, args, **kw)
    assert not is_error, text
    return json.loads(text)


def unpriced_in(region: str) -> list[str]:
    return [
        p.product_id
        for p in STORE.products()
        if p.visibility == "public"
        and not any(
            STORE.price(v.variant_id, region)
            for v in STORE.variants_of(p.product_id)
            if v.visibility == "public"
        )
    ]


# ---- hard rule 7: the canary -------------------------------------------------------------------


@pytest.mark.req("RAG-11")
@pytest.mark.parametrize("region", REGIONS)
def test_the_canary_is_in_no_portal_tool_result_in_any_region(url: str, region: str) -> None:
    results = [
        call(url, BROWSE_PRODUCTS, {}, region=region),
        call(url, BROWSE_PRODUCTS, {"department": "Jackets"}, region=region),
        call(url, GET_PRICES, {"product_id": "P0009"}, region=region),
    ]
    for text in (ENTRY["title"], "P0009", ENTRY["canary"], "Ascent"):
        results.append(call(url, BROWSE_PRODUCTS, {"text": text}, region=region))
    for variant in STORE.variants_of("P0009"):
        results.append(call(url, GET_PRICES, {"product_id": variant.variant_id}, region=region))
    for _, text in results:
        for forbidden in FORBIDDEN:
            assert forbidden not in text


@pytest.mark.req("RAG-11")
def test_searching_the_internal_titles_text_finds_nothing_and_is_not_an_error(url: str) -> None:
    body = ok(url, BROWSE_PRODUCTS, {"text": ENTRY["title"]})
    assert body["products"] == [] and body["total"] == 0


# ---- hard rule 10: unavailable is not empty; unpriced is null -----------------------------------


def test_a_shopper_token_cannot_be_minted_without_a_region() -> None:
    """So the tools' own `region_required` refusal is a second line, never an empty list."""
    with pytest.raises(ValueError, match="region"):
        bearer_for(region=None)


@pytest.mark.parametrize("region", ["UK", "EU"])
def test_a_product_unpriced_in_the_region_is_null_not_an_error(url: str, region: str) -> None:
    unpriced = unpriced_in(region)
    assert unpriced
    listed = {p["product_id"]: p for p in ok(url, BROWSE_PRODUCTS, {}, region=region)["products"]}
    for product_id in unpriced:
        assert listed[product_id]["from_price"] is None
    body = ok(url, GET_PRICES, {"product_id": unpriced[0]}, region=region)
    assert body["variants"] and all(v["price"] is None for v in body["variants"])
    # and the empty-vs-failed split: a name with no match is a valid empty result
    nothing = ok(url, BROWSE_PRODUCTS, {"text": "zzz no such product"}, region=region)
    assert nothing["products"] == [] and nothing["total"] == 0


@pytest.mark.parametrize("region", REGIONS)
def test_every_price_is_in_the_regions_own_currency(url: str, region: str) -> None:
    for product in ok(url, BROWSE_PRODUCTS, {}, region=region)["products"]:
        if product["from_price"] is not None:
            assert product["from_price"]["currency"] == CURRENCY[region]
    prices = ok(url, GET_PRICES, {"product_id": "P0001"}, region=region)["variants"]
    assert all(v["price"] is None or v["price"]["currency"] == CURRENCY[region] for v in prices)


def test_an_unknown_id_is_not_echoed_and_has_the_fixed_envelope(url: str) -> None:
    is_error, text = call(url, GET_PRICES, {"product_id": "P9999"})
    body = json.loads(text)
    assert is_error and "P9999" not in text
    assert {"isError", "errorCategory", "isRetryable", "message"} <= set(body)
    assert body["isError"] is True and body["isRetryable"] is False


# ---- hard rule 2: no stock, no text, only the live price ----------------------------------------


@pytest.mark.req("RAG-09")
def test_results_carry_no_stock_description_or_attribute_text(url: str) -> None:
    prices = ok(url, GET_PRICES, {"product_id": "P0001"})
    assert set(prices) == {"product_id", "region", "variants"}
    for variant in prices["variants"]:
        assert set(variant) == {"variant_id", "colour", "size", "fit", "price"}
        assert set(variant["price"]) == {"amount", "currency"}
    for product in ok(url, BROWSE_PRODUCTS, {})["products"]:
        assert {"description", "attributes", "stock", "on_hand", "price_band"}.isdisjoint(product)


@pytest.mark.req("RAG-09")
def test_a_stock_change_never_moves_a_price_and_a_price_change_shows_in_both_tools(
    url: str,
) -> None:
    product = "P0004"
    variants = [v for v in STORE.variants_of(product) if v.visibility == "public"]
    old = {v.variant_id: STORE.price(v.variant_id, "US") for v in variants}
    stock = {v.variant_id: STORE.stock(v.variant_id, "US") for v in variants}
    try:
        for v in variants:
            STORE.set_price(v.variant_id, "US", "77.70", "USD")
        for v in variants:
            STORE.set_stock(v.variant_id, "US", 0)
        listed = ok(url, BROWSE_PRODUCTS, {"text": STORE.product(product).name})["products"]  # type: ignore[union-attr]
        assert next(p for p in listed if p["product_id"] == product)["from_price"] == {
            "amount": "77.70",
            "currency": "USD",
        }
        shown = ok(url, GET_PRICES, {"product_id": product})["variants"]
        assert all(v["price"] == {"amount": "77.70", "currency": "USD"} for v in shown)
    finally:
        for v in variants:
            before = old[v.variant_id]
            if before is not None:
                STORE.set_price(v.variant_id, "US", before.amount, before.currency)
            STORE.set_stock(v.variant_id, "US", stock[v.variant_id] or 0)


# ---- hard rule 8: only the portal agent, and a real token ---------------------------------------


@pytest.mark.req("NFR-01")
def test_the_listing_for_the_portal_agent_holds_the_portal_tools_and_only_the_cart_writes(
    url: str,
) -> None:
    async def listed() -> set[str]:
        async with catalog_testing.connect(url, bearer_for()) as client:
            return {t.name for t in (await client.list_tools()).tools}

    names = asyncio.run(listed())
    assert {BROWSE_PRODUCTS, GET_PRICES} <= names
    # SA-01 PR 2 (the user's stop 5 yes, 2026-11-27): exactly the three cart writes, no other.
    writes = {"add_to_cart", "set_cart_quantity", "remove_from_cart"}
    assert names & writes == writes
    reads = {"get_cart", "get_product", BROWSE_PRODUCTS, GET_PRICES, "list_reviews"}
    assert names - writes == reads


@pytest.mark.req("NFR-01")
@pytest.mark.parametrize(
    "tool_args", [(BROWSE_PRODUCTS, {}), (GET_PRICES, {"product_id": "P0001"})]
)
def test_a_token_for_another_server_or_a_bad_signature_gets_nothing(
    url: str, tool_args: tuple[str, dict[str, Any]]
) -> None:
    tool, args = tool_args
    other_server, _ = mint(
        audience="shopper", region="US", agent="shopper-portal", key=KEY, server="northwind-orders"
    )
    wrong_key, _ = mint(
        audience="shopper",
        region="US",
        agent="shopper-portal",
        key=b"z" * 40,
        server=CATALOG_SERVER,
    )
    for bad in (other_server, wrong_key, "not-a-token"):
        try:
            is_error, text = asyncio.run(_call(url, bad, tool, args))
        except Exception:  # noqa: BLE001, S112 - refused at the door: a transport error is a refusal
            continue
        assert is_error and "products" not in text and "variants" not in text
