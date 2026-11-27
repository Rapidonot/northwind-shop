"""SA-01 PR 2: the gateway's reads for the v2 pages (hard rules 2, 6, 7, 10).

- Only the products on screen are priced: price reads are counted on the live store itself.
- `/api/departments` gives counts without reading a price.
- `/api/products?ids=` (hero, bag) keeps the order asked for.
- `/api/reviews` gives a product's public reviews and total, type-checked.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_common.mcp_token import KEY_ENV
from northwind_gateway.app import app
from northwind_gateway.shop import INVALID, NOT_FOUND, UNAVAILABLE, ShopReader, shop_reader
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog, Price
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.tools import TOOLS

pytestmark = pytest.mark.req("SA-01")

KEY = catalog_testing.KEY
FIXTURES = Path(__file__).resolve().parents[3] / "data/fixtures"
ENVELOPE = {"isError", "errorCategory", "isRetryable", "message"}
HERO = ["P0001", "P0004", "P0121"]


def _jsonl(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines() if line]


PRODUCTS = _jsonl("products.jsonl")
PUBLIC = [p for p in PRODUCTS if p["visibility"] == "public"]
INTERNAL = next(p["product_id"] for p in PRODUCTS if p["visibility"] != "public")


class CountingCatalog(FixtureCatalog):
    """Counts the products whose variants had a price read."""

    def __init__(self) -> None:
        super().__init__()
        self.priced: set[str] = set()

    def price(self, variant_id: str, region: str) -> Price | None:
        variant = self.variant(variant_id)
        if variant is not None:
            self.priced.add(variant.product_id)
        return super().price(variant_id, region)


@pytest.fixture(scope="module")
def store() -> CountingCatalog:
    return CountingCatalog()


@pytest.fixture(scope="module")
def url(store: CountingCatalog) -> Iterator[str]:
    with catalog_testing.running(CatalogServer(store, key=KEY, tools=TOOLS)) as running:
        yield running


@pytest.fixture
def photos(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def client(
    url: str, store: CountingCatalog, monkeypatch: pytest.MonkeyPatch, photos: Path
) -> Iterator[TestClient]:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    monkeypatch.setenv("DEV_SHOPPER_REGION", "US")
    monkeypatch.setenv("PORTAL_PHOTO_DIR", str(photos))
    app.dependency_overrides[shop_reader] = lambda: ShopReader(url, KEY)
    store.priced.clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _envelope(response: Any, status: int, category: str, message: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == ENVELOPE
    assert (body["errorCategory"], body["message"]) == (category, message)


# ---- only the products on screen are priced (hard rule 2) ---------------------------------------


def test_the_nav_counts_every_department_and_reads_no_price(
    client: TestClient, store: CountingCatalog
) -> None:
    body = client.get("/api/departments").json()
    expected: dict[str, int] = {}
    for p in PUBLIC:
        expected[p["department"]] = expected.get(p["department"], 0) + 1
    assert body["counts"] == expected
    assert body["departments"] == sorted(expected)
    assert body["total"] == len(PUBLIC) == 199
    assert store.priced == set()


def test_a_page_prices_only_its_own_products(client: TestClient, store: CountingCatalog) -> None:
    body = client.get("/api/products", params={"limit": "24"}).json()
    shown = [t["product_id"] for t in body["products"]]
    assert len(shown) == 24 and body["total"] == 199 and body["offset"] == 0
    assert store.priced == set(shown)


def test_the_next_page_follows_on_without_overlap(client: TestClient) -> None:
    first = client.get("/api/products", params={"department": "Jackets", "limit": "24"}).json()
    second = client.get(
        "/api/products", params={"department": "Jackets", "limit": "24", "offset": "24"}
    ).json()
    ids = [t["product_id"] for t in first["products"] + second["products"]]
    jackets = [p["product_id"] for p in PUBLIC if p["department"] == "Jackets"]
    assert first["total"] == second["total"] == 33
    assert ids == jackets  # no photos here, so catalog order
    assert second["offset"] == 24


def test_photos_come_first_across_pages(client: TestClient, photos: Path) -> None:
    jackets = [p["product_id"] for p in PUBLIC if p["department"] == "Jackets"]
    late = jackets[-3:]  # on the second page in catalog order
    for pid in late:
        (photos / f"{pid}.webp").write_bytes(b"x")
    body = client.get("/api/products", params={"department": "Jackets", "limit": "3"}).json()
    assert [t["product_id"] for t in body["products"]] == late
    assert all(t["photo"] for t in body["products"])


def test_a_page_tile_carries_its_live_low_and_high_price(
    client: TestClient, store: CountingCatalog
) -> None:
    tile = client.get("/api/products", params={"ids": "P0121"}).json()["products"][0]
    prices = [
        p
        for v in store.variants_of("P0121")
        if v.visibility == "public" and (p := store.price(v.variant_id, "US")) is not None
    ]
    low = min(prices, key=lambda p: p.minor_units)
    high = max(prices, key=lambda p: p.minor_units)
    assert tile["from_price"] == {"amount": low.amount, "currency": "USD"}
    assert tile["to_price"] == {"amount": high.amount, "currency": "USD"}


def test_ids_keep_the_order_asked_and_price_only_those(
    client: TestClient, store: CountingCatalog
) -> None:
    body = client.get("/api/products", params={"ids": ",".join(reversed(HERO))}).json()
    assert [t["product_id"] for t in body["products"]] == list(reversed(HERO))
    assert store.priced == set(HERO)


@pytest.mark.req("RAG-11")
def test_an_internal_id_is_dropped_like_an_unknown_one(
    client: TestClient, store: CountingCatalog
) -> None:
    internal = client.get("/api/products", params={"ids": f"P0001,{INTERNAL}"}).json()
    unknown = client.get("/api/products", params={"ids": "P0001,P9999"}).json()
    assert [t["product_id"] for t in internal["products"]] == ["P0001"]
    assert internal == unknown
    assert INTERNAL not in store.priced
    assert INTERNAL not in json.dumps(internal)


def test_a_page_past_the_end_is_empty_and_reads_no_price(
    client: TestClient, store: CountingCatalog
) -> None:
    body = client.get("/api/products", params={"department": "Dining", "offset": "40"}).json()
    assert body["products"] == [] and body["total"] == 12
    assert store.priced == set()


@pytest.mark.parametrize(
    "params",
    [
        {"limit": "0"},
        {"limit": "49"},
        {"limit": "-1"},
        {"limit": "ten"},
        {"offset": "1.5"},
        {"offset": "١"},  # a non-ASCII digit
        {"ids": "P0001,nope"},
        {"ids": ",".join(f"P{n:04d}" for n in range(1, 50))},
        {"ids": " , "},
    ],
)
def test_bad_paging_is_the_fixed_validation_envelope(
    client: TestClient, params: dict[str, str], store: CountingCatalog
) -> None:
    _envelope(client.get("/api/products", params=params), 422, "validation", INVALID)
    assert store.priced == set()


# ---- the product record's specification fields -------------------------------------------------


def test_the_product_carries_the_records_spec_fields(client: TestClient) -> None:
    record = next(p for p in PRODUCTS if p["product_id"] == "P0056")
    product = client.get("/api/product", params={"id": "P0056"}).json()["product"]
    assert product["subcategory"] == record["subcategory"]
    assert product["gender"] == record["gender"]
    assert product["fits"] == record["fits"]
    assert product["attributes"] == record["attributes"]


def test_a_home_good_has_no_gender_rather_than_an_empty_one(client: TestClient) -> None:
    product = client.get("/api/product", params={"id": "P0121"}).json()["product"]
    assert product["gender"] is None


# ---- reviews ------------------------------------------------------------------------------------


def _fixture_reviews(product_id: str) -> list[dict[str, Any]]:
    rows = [r for r in _jsonl("reviews.jsonl") if r["product_id"] == product_id]
    return sorted(rows, key=lambda r: (r["date"], r["review_id"]), reverse=True)


def test_reviews_are_the_three_newest_and_the_total(client: TestClient) -> None:
    body = client.get("/api/reviews", params={"id": "P0056"}).json()
    expected = _fixture_reviews("P0056")
    assert body["total"] == len(expected) == 11
    assert [r["review_id"] for r in body["reviews"]] == [r["review_id"] for r in expected[:3]]
    for review in body["reviews"]:
        assert set(review) == {"review_id", "title", "body", "reviewer", "date", "verified"}


def test_show_all_gives_every_review(client: TestClient) -> None:
    body = client.get("/api/reviews", params={"id": "P0056", "limit": "50"}).json()
    assert len(body["reviews"]) == body["total"] == 11


@pytest.mark.req("NFR-03")
def test_a_planted_instruction_in_a_review_is_passed_on_as_its_exact_text(
    client: TestClient,
) -> None:
    planted = next(r for r in _fixture_reviews("P0001") if "Ignore all previous" in r["body"])
    body = client.get("/api/reviews", params={"id": "P0001", "limit": "50"}).json()
    shown = next(r for r in body["reviews"] if r["review_id"] == planted["review_id"])
    assert shown["body"] == planted["body"]


@pytest.mark.req("RAG-11")
def test_reviews_of_an_internal_product_are_the_same_not_found_as_an_unknown_one(
    client: TestClient,
) -> None:
    internal = client.get("/api/reviews", params={"id": INTERNAL})
    unknown = client.get("/api/reviews", params={"id": "P9999"})
    _envelope(internal, 404, "validation", NOT_FOUND)
    assert internal.json() == unknown.json()


@pytest.mark.parametrize(
    "params",
    [{}, {"id": "nope"}, {"id": "P0001", "limit": "0"}, {"id": "P0001", "limit": "51"}],
)
def test_bad_review_requests_are_the_fixed_validation_envelope(
    client: TestClient, params: dict[str, str]
) -> None:
    _envelope(client.get("/api/reviews", params=params), 422, "validation", INVALID)


def test_card_data_in_a_review_request_is_refused_before_any_read(client: TestClient) -> None:
    response = client.get("/api/reviews", params={"id": "P0001", "limit": "4111111111111111"})
    assert response.status_code == 422
    assert "4111" not in response.text


def test_no_key_is_the_fixed_503_for_reviews(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    _envelope(
        TestClient(app).get("/api/reviews", params={"id": "P0001"}), 503, "transient", UNAVAILABLE
    )


def test_a_malformed_review_from_the_catalog_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def bad_call(self: ShopReader, caller: Any, tool: str, args: dict[str, Any]) -> Any:
        return {
            "product_id": "P0001",
            "total": 1,
            "reviews": [{"review_id": "R00001", "title": 1, "body": "x", "reviewer": "y"}],
        }

    monkeypatch.setattr(ShopReader, "call", bad_call)
    _envelope(client.get("/api/reviews", params={"id": "P0001"}), 503, "transient", UNAVAILABLE)
