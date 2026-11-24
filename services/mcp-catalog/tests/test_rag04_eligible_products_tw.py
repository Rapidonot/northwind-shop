"""RAG-04 (row #11, decision 0022 section 6), hard rule 2: price and availability filters are
resolved only from the live store, through `eligible_products`.

From the decision: the tool returns product IDs only (no amount, currency or stock count), reads the
live store in the token's region on every call, is offered only to the `shopper-query` agent, and
retrieval never reads a price. Written against the MCP surface and `tool_scopes`, over the verified
fixtures. No database, no model.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest

from northwind_common.mcp_token import CATALOG_SERVER, RAG_SERVER, mint
from northwind_common.tool_scopes import (
    AGENT_TOOLS,
    FORBIDDEN_FRAGMENTS,
    REGION_CURRENCY,
    agents,
    allowed_tools,
)
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog, eligible_products
from northwind_mcp_catalog.testing import KEY

pytestmark = pytest.mark.req("RAG-04")

TOOL = "eligible_products"
PRODUCT_ID = re.compile(r"P\d{4}")


def token(agent: str, region: str | None) -> str:
    audience: Any = "staff" if agent == "catalog-subagent" else "shopper"
    minted, _ = mint(audience=audience, region=region, agent=agent, key=KEY, server=CATALOG_SERVER)
    return minted


async def call(url: str, bearer: str, args: dict[str, Any]) -> tuple[bool, str]:
    async with catalog_testing.connect(url, bearer) as client:
        result = await client.call_tool(TOOL, args)
    return bool(result.is_error), "".join(getattr(b, "text", "") for b in result.content)


async def offered(url: str, bearer: str) -> set[str]:
    async with catalog_testing.connect(url, bearer) as client:
        return {tool.name for tool in (await client.list_tools()).tools}


def ask(store: FixtureCatalog, agent: str, region: str | None, args: dict[str, Any]) -> Any:
    with catalog_testing.running(catalog_testing.server(store, key=KEY)) as url:
        return asyncio.run(call(url, token(agent, region), args))


def ids(text: str) -> list[str]:
    body = json.loads(text)
    products: list[str] = body["products"]
    return products


# ---- scoping: only `shopper-query` --------------------------------------------------------------


def test_only_shopper_query_is_given_the_tool_in_the_scope_table() -> None:
    holders = {
        agent
        for agent in agents()
        if TOOL in allowed_tools(agent, CATALOG_SERVER) or TOOL in allowed_tools(agent, RAG_SERVER)
    }
    assert holders == {"shopper-query"}
    assert allowed_tools("shopper-query", CATALOG_SERVER) == frozenset({TOOL})
    assert allowed_tools("shopper-query", RAG_SERVER) == frozenset()


def test_the_query_agent_has_no_forbidden_tool() -> None:
    names = {name for tools in AGENT_TOOLS["shopper-query"].values() for name in tools}
    for fragment in FORBIDDEN_FRAGMENTS["shopper-query"]:
        assert not any(fragment in name for name in names)


def test_the_tool_is_listed_only_to_the_query_agent() -> None:
    with catalog_testing.running(catalog_testing.server(key=KEY)) as url:
        seen = {
            agent: asyncio.run(
                offered(url, token(agent, None if agent == "catalog-subagent" else "US"))
            )
            for agent in sorted(agents())
        }
    assert TOOL in seen["shopper-query"]
    assert {a for a, tools in seen.items() if TOOL in tools} == {"shopper-query"}


@pytest.mark.parametrize("agent", ["shopper-assistant", "shopper-portal", "catalog-subagent"])
def test_any_other_agent_is_refused_and_learns_nothing(agent: str) -> None:
    region = None if agent == "catalog-subagent" else "US"
    is_error, text = ask(FixtureCatalog(), agent, region, {"price_max": 5000})
    assert is_error and "permission" in text
    assert not PRODUCT_ID.search(text)  # not one product ID leaks in the refusal


def test_a_token_for_the_other_server_cannot_call_it() -> None:
    other, _ = mint(
        audience="shopper", region="US", agent="shopper-query", key=KEY, server=RAG_SERVER
    )
    with catalog_testing.running(catalog_testing.server(key=KEY)) as url:
        with pytest.raises(Exception):  # noqa: B017, PT011 - a bare 401 surfaces as any client error
            asyncio.run(call(url, other, {"price_max": 5000}))


# ---- the output: IDs only -----------------------------------------------------------------------


@pytest.mark.parametrize("region", sorted(REGION_CURRENCY))
def test_the_output_is_ids_and_the_region_and_nothing_else(region: str) -> None:
    is_error, text = ask(FixtureCatalog(), "shopper-query", region, {"price_max": 20000})
    assert not is_error
    body = json.loads(text)
    assert set(body) == {"region", "products"}
    assert body["region"] == region
    assert body["products"] and all(PRODUCT_ID.fullmatch(p) for p in body["products"])
    assert body["products"] == sorted(body["products"])
    assert len(set(body["products"])) == len(body["products"])


@pytest.mark.parametrize("region", sorted(REGION_CURRENCY))
def test_no_amount_currency_or_count_appears_in_the_output(region: str) -> None:
    for args in (
        {},
        {"price_max": 20000},
        {"in_stock": True},
        {"price_min": 1000, "in_stock": True},
    ):
        is_error, text = ask(FixtureCatalog(), "shopper-query", region, args)
        assert not is_error
        body = json.loads(text)
        stripped = text.replace(f'"{region}"', "")  # the region code is allowed, echoed
        for word in ("amount", "price", "currency", "stock", "on_hand", "count", "minor", "total"):
            assert word not in stripped.replace('"products"', "").lower(), (args, word)
        for code in set(REGION_CURRENCY.values()):
            assert code not in text
        assert not re.search(r"[$£€]", text)
        # Every value but the region is a product ID: a bare number could only be a price or count.
        assert all(PRODUCT_ID.fullmatch(p) for p in body["products"])
        assert not re.search(r"[:,\[]\s*-?\d", text)


def test_the_output_never_changes_shape_with_the_bounds() -> None:
    """A tighter bound only removes IDs; no field appears or disappears."""
    with catalog_testing.running(catalog_testing.server(key=KEY)) as url:
        wide = json.loads(asyncio.run(call(url, token("shopper-query", "US"), {}))[1])
        narrow = json.loads(
            asyncio.run(call(url, token("shopper-query", "US"), {"price_max": 3000}))[1]
        )
    assert set(wide) == set(narrow)
    assert set(narrow["products"]) < set(wide["products"])


def test_only_public_products_are_ever_returned() -> None:
    store = FixtureCatalog()
    internal = {p.product_id for p in store.products() if p.visibility != "public"}
    assert internal, "the fixtures must hold an internal product for this test to bite"
    _, text = ask(store, "shopper-query", "US", {})
    assert internal.isdisjoint(ids(text))
    _, text = ask(store, "shopper-query", "US", {"in_stock": True, "price_max": 1_000_000})
    assert internal.isdisjoint(ids(text))


def test_no_match_is_an_empty_list_not_an_error() -> None:
    """Hard rule 10: a valid empty result and an access failure are told apart."""
    is_error, text = ask(FixtureCatalog(), "shopper-query", "US", {"price_max": 1})
    assert not is_error
    assert json.loads(text) == {"region": "US", "products": []}


# ---- live data, token region --------------------------------------------------------------------


def test_the_regions_come_from_the_token_never_from_arguments() -> None:
    """A US token asking for UK in any argument gets a refusal, not the UK answer."""
    for extra in (
        {"region": "UK"},
        {"currency": "GBP"},
        {"audience": "staff"},
        {"visibility": "internal"},
    ):
        is_error, text = ask(FixtureCatalog(), "shopper-query", "US", {"price_max": 5000, **extra})
        assert is_error, extra
        assert "products" not in text, extra


def test_each_region_reads_its_own_live_price() -> None:
    store = FixtureCatalog()
    for variant in store.variants_of("P0011"):
        store.set_price(variant.variant_id, "UK", "0.99", "GBP")
    with catalog_testing.running(catalog_testing.server(store, key=KEY)) as url:
        _, uk = asyncio.run(call(url, token("shopper-query", "UK"), {"price_max": 100}))
        _, us = asyncio.run(call(url, token("shopper-query", "US"), {"price_max": 100}))
    assert "P0011" in ids(uk)
    assert "P0011" not in ids(us)  # a UK price change does not move a US filter


def test_each_region_reads_its_own_live_stock() -> None:
    store = FixtureCatalog()
    variants = store.variants_of("P0011")
    for v in variants:
        store.set_stock(v.variant_id, "US", 4)
        store.set_stock(v.variant_id, "UK", 0)
    with catalog_testing.running(catalog_testing.server(store, key=KEY)) as url:
        _, us = asyncio.run(call(url, token("shopper-query", "US"), {"in_stock": True}))
        _, uk = asyncio.run(call(url, token("shopper-query", "UK"), {"in_stock": True}))
    assert "P0011" in ids(us)
    assert "P0011" not in ids(uk)


def test_a_change_after_the_server_started_is_seen_by_the_next_call_with_no_reingest() -> None:
    store = FixtureCatalog()
    [first, *_] = store.variants_of("P0011")
    tok = token("shopper-query", "US")
    with catalog_testing.running(catalog_testing.server(store, key=KEY)) as url:
        _, before = asyncio.run(call(url, tok, {"price_max": 100}))
        store.set_price(first.variant_id, "US", "0.99", "USD")
        _, after = asyncio.run(call(url, tok, {"price_max": 100}))
        for v in store.variants_of("P0011"):
            store.set_stock(v.variant_id, "US", 0)
        _, gone = asyncio.run(call(url, tok, {"price_max": 100, "in_stock": True}))
        store.set_stock(first.variant_id, "US", 2)
        _, back = asyncio.run(call(url, tok, {"price_max": 100, "in_stock": True}))
    assert "P0011" not in ids(before)
    assert "P0011" in ids(after)
    assert "P0011" not in ids(gone)
    assert "P0011" in ids(back)


def test_the_tool_agrees_with_the_live_function_in_every_region() -> None:
    store = FixtureCatalog()
    for region, currency in sorted(REGION_CURRENCY.items()):
        want = eligible_products(store, region, currency=currency, price_min=2000, price_max=9000)
        _, text = ask(store, "shopper-query", region, {"price_min": 2000, "price_max": 9000})
        assert ids(text) == want, region


# ---- bad arguments ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        {"price_max": 0},
        {"price_max": -50},
        {"price_min": 0},
        {"price_max": 1_000_001},
        {"price_max": 49.99},
        {"price_max": "5000"},
        {"price_max": True},
        {"price_min": 9000, "price_max": 1000},
        {"in_stock": "yes"},
        {"in_stock": 1},
        {"product_id": "P0011"},
        {"query": "pants"},
    ],
)
def test_a_bad_argument_is_a_validation_refusal_with_no_ids(args: dict[str, Any]) -> None:
    is_error, text = ask(FixtureCatalog(), "shopper-query", "US", args)
    assert is_error
    assert "validation" in text
    assert not PRODUCT_ID.search(text)


def test_card_text_in_an_argument_is_refused() -> None:
    is_error, text = ask(
        FixtureCatalog(), "shopper-query", "US", {"price_max": 5000, "note": "4111 1111 1111 1111"}
    )
    assert is_error and "products" not in text
