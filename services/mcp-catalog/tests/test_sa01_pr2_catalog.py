"""SA-01 PR 2: the portal's catalog reads for the v2 pages (hard rules 2, 6, 7, 10).

- `browse_products` takes `product_ids` and `priced: false`, so the gateway can order and count
  every match without reading a price, then price only the page on screen. Price reads are counted
  on the store itself.
- `list_reviews` gives a public product's public reviews, newest first, with the total.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest

from northwind_common.mcp_token import CATALOG_SERVER, mint
from northwind_common.tool_scopes import BROWSE_PRODUCTS, LIST_REVIEWS
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog, Price, Review
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.testing import KEY
from northwind_mcp_catalog.tools import TOOLS

pytestmark = pytest.mark.req("SA-01")

INTERNAL = "P0009"  # the one internal product (the canary)


class CountingCatalog(FixtureCatalog):
    """The fixture store, counting every price read and the variants it was read for."""

    def __init__(self) -> None:
        super().__init__()
        self.priced: list[str] = []

    def price(self, variant_id: str, region: str) -> Price | None:
        self.priced.append(variant_id)
        return super().price(variant_id, region)


class NoReviews:
    """A store with products but no review source at all."""

    def __init__(self, inner: FixtureCatalog) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        if name == "reviews_of":
            raise AttributeError(name)
        return getattr(self._inner, name)


STORE = CountingCatalog()


def token(agent: str = "shopper-portal", region: str = "US") -> str:
    minted, _ = mint(audience="shopper", region=region, agent=agent, key=KEY, server=CATALOG_SERVER)
    return minted


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    with catalog_testing.running(CatalogServer(STORE, key=KEY, tools=TOOLS)) as running:
        yield running


async def _call(url: str, tool: str, args: dict[str, Any]) -> tuple[bool, str]:
    async with catalog_testing.connect(url, token()) as client:
        result = await client.call_tool(tool, args)
    return bool(result.is_error), "".join(getattr(b, "text", "") for b in result.content)


def call(url: str, tool: str, args: dict[str, Any]) -> tuple[bool, str]:
    return asyncio.run(_call(url, tool, args))


def ok(url: str, tool: str, args: dict[str, Any]) -> Any:
    is_error, text = call(url, tool, args)
    assert not is_error, text
    return json.loads(text)


def public_variants(product_id: str) -> set[str]:
    return {v.variant_id for v in STORE.variants_of(product_id) if v.visibility == "public"}


# ---- browse_products: only the page on screen is priced (hard rule 2) --------------------------


def test_unpriced_browse_lists_every_public_product_and_reads_no_price(url: str) -> None:
    STORE.priced.clear()
    body = ok(url, BROWSE_PRODUCTS, {"priced": False})
    public = [p.product_id for p in STORE.products() if p.visibility == "public"]
    assert [p["product_id"] for p in body["products"]] == public
    assert body["total"] == len(public) == 199
    assert STORE.priced == []
    for product in body["products"]:
        assert set(product) == {
            "product_id",
            "name",
            "brand",
            "category",
            "department",
            "subcategory",
        }


def test_product_ids_prices_only_those_products(url: str) -> None:
    STORE.priced.clear()
    body = ok(url, BROWSE_PRODUCTS, {"product_ids": ["P0121", "P0001", "P0004"]})
    # Catalog order, whatever order the IDs came in; the gateway puts them back in page order.
    assert [p["product_id"] for p in body["products"]] == ["P0001", "P0004", "P0121"]
    assert set(STORE.priced) == public_variants("P0001") | public_variants(
        "P0004"
    ) | public_variants("P0121")
    for product in body["products"]:
        assert product["from_price"] is not None and product["to_price"] is not None


def test_a_range_is_lowest_to_highest_and_one_price_is_both(url: str) -> None:
    sheets = ok(url, BROWSE_PRODUCTS, {"product_ids": ["P0121"]})["products"][0]
    assert float(sheets["from_price"]["amount"]) < float(sheets["to_price"]["amount"])
    jacket = ok(url, BROWSE_PRODUCTS, {"product_ids": ["P0004"]})["products"][0]
    assert jacket["from_price"] == jacket["to_price"]


@pytest.mark.req("RAG-11")
def test_an_internal_id_is_left_out_exactly_like_an_unknown_one(url: str) -> None:
    STORE.priced.clear()
    internal = ok(url, BROWSE_PRODUCTS, {"product_ids": [INTERNAL]})
    unknown = ok(url, BROWSE_PRODUCTS, {"product_ids": ["P9999"]})
    assert internal == unknown == {"products": [], "region": "US", "total": 0}
    assert STORE.priced == []


def test_filters_and_ids_combine(url: str) -> None:
    body = ok(url, BROWSE_PRODUCTS, {"department": "Bedding", "product_ids": ["P0001", "P0121"]})
    assert [p["product_id"] for p in body["products"]] == ["P0121"]


@pytest.mark.parametrize(
    "args",
    [
        {"product_ids": []},
        {"product_ids": "P0001"},
        {"product_ids": ["P0001"] * 49},
        {"product_ids": ["p0001"]},
        {"product_ids": ["P0001", 7]},
        {"priced": "false"},
        {"priced": 0},
        {"product_ids": ["P0001"], "limit": 3},
    ],
)
def test_bad_browse_arguments_are_refused(url: str, args: dict[str, Any]) -> None:
    is_error, text = call(url, BROWSE_PRODUCTS, args)
    assert is_error
    assert json.loads(text)["errorCategory"] == "validation"


# ---- list_reviews -------------------------------------------------------------------------------


def fixture_reviews(product_id: str) -> list[Review]:
    return [r for r in STORE.reviews_of(product_id) if r.visibility == "public"]


def test_three_newest_reviews_by_default_with_the_total(url: str) -> None:
    body = ok(url, LIST_REVIEWS, {"product_id": "P0056"})
    expected = fixture_reviews("P0056")
    assert body["product_id"] == "P0056"
    assert body["total"] == len(expected) > 3
    assert [r["review_id"] for r in body["reviews"]] == [r.review_id for r in expected[:3]]
    dates = [r["date"] for r in body["reviews"]]
    assert dates == sorted(dates, reverse=True)
    for review in body["reviews"]:
        assert set(review) == {"review_id", "title", "body", "reviewer", "date", "verified"}


def test_show_all_returns_every_review(url: str) -> None:
    body = ok(url, LIST_REVIEWS, {"product_id": "P0056", "limit": 50})
    assert len(body["reviews"]) == body["total"]


@pytest.mark.req("NFR-03")
def test_a_review_that_holds_instructions_comes_back_as_its_exact_text(url: str) -> None:
    """Hard rule 6: the page shows shopper text as data; the tool neither drops nor rewrites it."""
    body = ok(url, LIST_REVIEWS, {"product_id": "P0001", "limit": 50})
    planted = next(r for r in fixture_reviews("P0001") if "Ignore all previous" in r.body)
    shown = next(r for r in body["reviews"] if r["review_id"] == planted.review_id)
    assert shown["body"] == planted.body


def test_a_product_with_no_reviews_is_an_empty_result_not_an_error(url: str) -> None:
    reviewed = {r.product_id for p in STORE.products() for r in STORE.reviews_of(p.product_id)}
    quiet = next(
        p.product_id
        for p in STORE.products()
        if p.visibility == "public" and p.product_id not in reviewed
    )
    assert ok(url, LIST_REVIEWS, {"product_id": quiet}) == {
        "product_id": quiet,
        "total": 0,
        "reviews": [],
    }


@pytest.mark.req("RAG-11")
def test_an_internal_product_is_the_same_not_found_as_an_unknown_one(url: str) -> None:
    internal = call(url, LIST_REVIEWS, {"product_id": INTERNAL})
    unknown = call(url, LIST_REVIEWS, {"product_id": "P9999"})
    assert internal[0] and unknown[0]
    assert internal[1] == unknown[1]
    assert INTERNAL not in internal[1]


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"product_id": "nope"},
        {"product_id": "P0001", "limit": 0},
        {"product_id": "P0001", "limit": 51},
        {"product_id": "P0001", "limit": True},
        {"product_id": "P0001", "limit": "3"},
        {"product_id": "P0001", "offset": 3},
    ],
)
def test_bad_review_arguments_are_refused(url: str, args: dict[str, Any]) -> None:
    is_error, text = call(url, LIST_REVIEWS, args)
    assert is_error
    assert json.loads(text)["errorCategory"] == "validation"


def test_a_store_without_reviews_is_a_transient_error_not_an_empty_list() -> None:
    """Hard rule 10: "access failed" is never shown as "no reviews"."""
    server = CatalogServer(NoReviews(FixtureCatalog()), key=KEY, tools=TOOLS)
    with catalog_testing.running(server) as running:
        is_error, text = call(running, LIST_REVIEWS, {"product_id": "P0001"})
    body = json.loads(text)
    assert is_error and body["errorCategory"] == "transient" and body["isRetryable"] is True
