"""SA-01 PR 1: the portal's own catalog reads, `browse_products` and `get_prices` (hard rules
2, 7, 8).

Also the plan's answer (a): the assistant's tools are unchanged. Its catalog listing (names,
descriptions, schemas and annotations) is pinned by digest to what it was at 361d396, and so are the
tool names the assistant's options allow. The two portal tools reach no other agent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import pytest

from northwind_common import catalog_tool, order_tool, policy_tool
from northwind_common.mcp_token import CATALOG_SERVER, mint
from northwind_common.tool_scopes import (
    AGENT_TOOLS,
    BROWSE_PRODUCTS,
    GET_PRICES,
    LIST_REVIEWS,
    PORTAL_ONLY_TOOLS,
    agents,
    allowed_tools,
)
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.testing import KEY
from northwind_mcp_catalog.tools import TOOLS

pytestmark = pytest.mark.req("SA-01")

# The assistant's catalog `tools/list` at 361d396 (origin/main when SA-01 started), as a digest of
# each tool's name, description, input schema and annotations, sorted by name.
ASSISTANT_CATALOG_LISTING_AT_BASE = (
    "cb4260066347fae0e2b996f1566629de431b4cdfe5386ed634d8d89c59088a2a"
)
ASSISTANT_ALLOWED_AT_BASE = (
    "mcp__northwind-catalog__add_to_cart",
    "mcp__northwind-catalog__check_stock",
    "mcp__northwind-catalog__get_cart",
    "mcp__northwind-catalog__get_price",
    "mcp__northwind-catalog__get_product",
    "mcp__northwind-catalog__get_variant",
    "mcp__northwind-catalog__remove_from_cart",
    "mcp__northwind-catalog__search_products",
    "mcp__northwind-catalog__set_cart_quantity",
    "mcp__northwind-orders__get_order",
    "mcp__northwind-orders__request_return",
    "mcp__northwind-policy__evaluate_return_eligibility",
)
STORE = FixtureCatalog()


def token(agent: str = "shopper-portal", region: str | None = "US") -> str:
    audience: Any = "staff" if agent == "catalog-subagent" else "shopper"
    minted, _ = mint(audience=audience, region=region, agent=agent, key=KEY, server=CATALOG_SERVER)
    return minted


@pytest.fixture(scope="module")
def url() -> Any:
    with catalog_testing.running(CatalogServer(STORE, key=KEY, tools=TOOLS)) as running:
        yield running


async def _call(url: str, bearer: str, tool: str, args: dict[str, Any]) -> tuple[bool, str]:
    async with catalog_testing.connect(url, bearer) as client:
        result = await client.call_tool(tool, args)
    return bool(result.is_error), "".join(getattr(b, "text", "") for b in result.content)


def call(url: str, tool: str, args: dict[str, Any], **kw: Any) -> tuple[bool, str]:
    return asyncio.run(_call(url, token(**kw), tool, args))


def ok(url: str, tool: str, args: dict[str, Any], **kw: Any) -> Any:
    is_error, text = call(url, tool, args, **kw)
    assert not is_error, text
    return json.loads(text)


def listing_digest(tools: list[Any]) -> str:
    rows = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "annotations": tool.annotations.model_dump(mode="json") if tool.annotations else None,
        }
        for tool in sorted(tools, key=lambda t: t.name)
    ]
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


# ---- answer (a): the assistant is unchanged -----------------------------------------------------


def test_the_assistants_catalog_listing_is_unchanged_since_the_base(url: str) -> None:
    async def listed() -> list[Any]:
        async with catalog_testing.connect(url, token("shopper-assistant", "UK")) as client:
            return list((await client.list_tools()).tools)

    tools = asyncio.run(listed())
    assert {t.name for t in tools} & PORTAL_ONLY_TOOLS == set()
    assert listing_digest(tools) == ASSISTANT_CATALOG_LISTING_AT_BASE


def test_the_assistants_allowed_tools_are_unchanged_since_the_base() -> None:
    allowed = catalog_tool.ALLOWED_TOOLS + order_tool.ALLOWED_TOOLS + policy_tool.ALLOWED_TOOLS
    assert tuple(sorted(allowed)) == ASSISTANT_ALLOWED_AT_BASE


@pytest.mark.req("NFR-01")
def test_only_the_portal_agent_holds_the_portal_tools() -> None:
    assert PORTAL_ONLY_TOOLS == {BROWSE_PRODUCTS, GET_PRICES, LIST_REVIEWS}  # PR 2: reviews
    for agent in agents():
        for server, tools in AGENT_TOOLS[agent].items():
            held = tools & PORTAL_ONLY_TOOLS
            expected = (
                PORTAL_ONLY_TOOLS
                if (agent, server) == ("shopper-portal", CATALOG_SERVER)
                else set()
            )
            assert held == expected, (agent, server)
    assert not PORTAL_ONLY_TOOLS & set(catalog_tool.TOOL_NAMES)


@pytest.mark.req("NFR-01")
@pytest.mark.parametrize("agent", ["shopper-assistant", "shopper-query", "catalog-subagent"])
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (BROWSE_PRODUCTS, {}),
        (GET_PRICES, {"product_id": "P0001"}),
        (LIST_REVIEWS, {"product_id": "P0001"}),  # SA-01 PR 2
    ],
)
def test_every_other_agent_is_refused(
    url: str, agent: str, tool: str, args: dict[str, Any]
) -> None:
    region = None if agent == "catalog-subagent" else "US"
    is_error, text = call(url, tool, args, agent=agent, region=region)
    assert is_error and "permission" in text
    assert tool not in allowed_tools(agent, CATALOG_SERVER)


# ---- browse_products ----------------------------------------------------------------------------


def _prices(product_id: str, region: str) -> list[Any]:
    return [
        p
        for v in STORE.variants_of(product_id)
        if v.visibility == "public" and (p := STORE.price(v.variant_id, region)) is not None
    ]


def _lowest(product_id: str, region: str) -> dict[str, str] | None:
    prices = _prices(product_id, region)
    low = min(prices, key=lambda p: p.minor_units) if prices else None
    return None if low is None else {"amount": low.amount, "currency": low.currency}


def _highest(product_id: str, region: str) -> dict[str, str] | None:
    prices = _prices(product_id, region)
    high = max(prices, key=lambda p: p.minor_units) if prices else None
    return None if high is None else {"amount": high.amount, "currency": high.currency}


@pytest.mark.req("RAG-11")
def test_browse_lists_every_public_product_with_its_live_lowest_price(url: str) -> None:
    body = ok(url, BROWSE_PRODUCTS, {}, region="UK")
    public = [p.product_id for p in STORE.products() if p.visibility == "public"]
    assert [p["product_id"] for p in body["products"]] == public
    assert body["total"] == len(public) and body["region"] == "UK"
    for product in body["products"]:
        assert product["from_price"] == _lowest(product["product_id"], "UK")
        assert product["to_price"] == _highest(product["product_id"], "UK")  # SA-01 PR 2
        assert set(product) == {
            "product_id",
            "name",
            "brand",
            "category",
            "department",
            "subcategory",
            "from_price",
            "to_price",
        }


def test_browse_filters_by_department_and_name_text(url: str) -> None:
    jackets = ok(url, BROWSE_PRODUCTS, {"department": "jackets"})["products"]
    assert jackets and {p["department"] for p in jackets} == {"Jackets"}
    found = ok(url, BROWSE_PRODUCTS, {"text": "Rain Jacket"})["products"]
    assert found and all("rain jacket" in p["name"].casefold() for p in found)


@pytest.mark.req("RAG-09")
def test_a_price_change_shows_on_the_next_call(url: str) -> None:
    product = next(p for p in STORE.products() if p.visibility == "public")
    variant = next(v for v in STORE.variants_of(product.product_id) if v.visibility == "public")
    before = STORE.price(variant.variant_id, "US")
    assert before is not None
    try:
        STORE.set_price(variant.variant_id, "US", "0.50", before.currency)
        listed = ok(url, BROWSE_PRODUCTS, {"text": product.name})["products"]
        assert next(p for p in listed if p["product_id"] == product.product_id)["from_price"] == {
            "amount": "0.50",
            "currency": before.currency,
        }
        prices = ok(url, GET_PRICES, {"product_id": product.product_id})["variants"]
        assert next(v for v in prices if v["variant_id"] == variant.variant_id)["price"] == {
            "amount": "0.50",
            "currency": before.currency,
        }
    finally:
        STORE.set_price(variant.variant_id, "US", before.amount, before.currency)


@pytest.mark.parametrize(
    "args",
    [
        {"department": ""},
        {"department": 3},
        {"text": "x" * 65},
        {"region": "UK"},
        {"visibility": "internal"},
        {"limit": 5},
    ],
)
def test_browse_refuses_bad_arguments(url: str, args: dict[str, Any]) -> None:
    is_error, text = call(url, BROWSE_PRODUCTS, args)
    assert is_error and "validation" in text


@pytest.mark.req("NFR-04")
@pytest.mark.parametrize("tool", [BROWSE_PRODUCTS, GET_PRICES])
def test_card_data_is_refused_first(url: str, tool: str) -> None:
    key = "text" if tool == BROWSE_PRODUCTS else "product_id"
    is_error, text = call(url, tool, {key: "4242 4242 4242 4242"})
    assert is_error and "card_data_refused" in text and "4242" not in text


# ---- get_prices ---------------------------------------------------------------------------------


def test_get_prices_gives_each_public_variants_live_price(url: str) -> None:
    product = next(p for p in STORE.products() if p.visibility == "public")
    body = ok(url, GET_PRICES, {"product_id": product.product_id}, region="EU")
    public = [v for v in STORE.variants_of(product.product_id) if v.visibility == "public"]
    assert [v["variant_id"] for v in body["variants"]] == [v.variant_id for v in public]
    for shown, variant in zip(body["variants"], public, strict=True):
        live = STORE.price(variant.variant_id, "EU")
        assert shown["price"] == (
            None if live is None else {"amount": live.amount, "currency": live.currency}
        )
        assert (shown["colour"], shown["size"], shown["fit"]) == (
            variant.colour,
            variant.size,
            variant.fit,
        )


@pytest.mark.req("RAG-11")
def test_an_internal_product_is_the_same_not_found_as_an_unknown_one(url: str) -> None:
    internal = next(p.product_id for p in STORE.products() if p.visibility != "public")
    assert call(url, GET_PRICES, {"product_id": internal}) == call(
        url, GET_PRICES, {"product_id": "P9999"}
    )
    is_error, text = call(url, GET_PRICES, {"product_id": internal})
    assert is_error and "not_found" in text
    listed = ok(url, BROWSE_PRODUCTS, {})["products"]
    assert internal not in [p["product_id"] for p in listed]


@pytest.mark.parametrize(
    "args", [{}, {"product_id": "P1"}, {"product_id": "P0001", "region": "UK"}]
)
def test_get_prices_refuses_bad_arguments(url: str, args: dict[str, Any]) -> None:
    is_error, text = call(url, GET_PRICES, args)
    assert is_error and "validation" in text
