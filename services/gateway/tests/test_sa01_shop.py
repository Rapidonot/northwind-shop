"""SA-01 PR 1: the shop pages' reads, as the gateway serves them (hard rules 2, 7, 10).

A real mcp-catalog over the verified fixtures, with the gateway's own `ShopReader` pointed at it.
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
from northwind_gateway.shop import (
    INVALID,
    NOT_FOUND,
    UNAVAILABLE,
    ShopReader,
    photo_ids,
    shop_reader,
)
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.tools import TOOLS

pytestmark = pytest.mark.req("SA-01")

KEY = catalog_testing.KEY
FIXTURES = Path(__file__).resolve().parents[3] / "data/fixtures"
ENVELOPE = {"isError", "errorCategory", "isRetryable", "message"}


def _jsonl(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines() if line]


PRODUCTS = _jsonl("products.jsonl")
PUBLIC = [p for p in PRODUCTS if p["visibility"] == "public"]
INTERNAL = next(p["product_id"] for p in PRODUCTS if p["visibility"] != "public")


@pytest.fixture(scope="module")
def store() -> FixtureCatalog:
    return FixtureCatalog()


@pytest.fixture(scope="module")
def url(store: FixtureCatalog) -> Iterator[str]:
    with catalog_testing.running(CatalogServer(store, key=KEY, tools=TOOLS)) as running:
        yield running


@pytest.fixture
def client(url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    monkeypatch.setenv("DEV_SHOPPER_REGION", "US")
    monkeypatch.setenv("PORTAL_PHOTO_DIR", str(tmp_path))
    app.dependency_overrides[shop_reader] = lambda: ShopReader(url, KEY)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _lowest_us_price(store: FixtureCatalog, product_id: str) -> dict[str, str] | None:
    prices = [
        p
        for v in store.variants_of(product_id)
        if v.visibility == "public" and (p := store.price(v.variant_id, "US")) is not None
    ]
    if not prices:
        return None
    low = min(prices, key=lambda p: p.minor_units)
    return {"amount": low.amount, "currency": low.currency}


def _assert_envelope(response: Any, status: int, category: str, message: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == ENVELOPE
    assert (body["errorCategory"], body["message"]) == (category, message)


# ---- departments and lists ---------------------------------------------------------------------


def test_the_nav_lists_all_ten_departments(client: TestClient) -> None:
    response = client.get("/api/departments")
    assert response.status_code == 200
    # SA-01 PR 2 (the user's look round 1): catalog order, the order each first appears.
    assert response.json()["departments"] == list(dict.fromkeys(p["department"] for p in PUBLIC))
    assert len(response.json()["departments"]) == 10


def test_a_department_lists_every_public_product_with_its_live_lowest_price(
    client: TestClient, store: FixtureCatalog
) -> None:
    body = client.get("/api/products", params={"department": "Jackets"}).json()
    expected = [p["product_id"] for p in PUBLIC if p["department"] == "Jackets"]
    assert body["total"] == len(expected) == 33  # above search_products' limit of 20
    assert [t["product_id"] for t in body["products"]] == expected
    for tile in body["products"]:
        assert tile["from_price"] == _lowest_us_price(store, tile["product_id"])
    assert INTERNAL not in [t["product_id"] for t in body["products"]]


def test_search_matches_the_name_text(client: TestClient) -> None:
    body = client.get("/api/products", params={"q": "rain jacket"}).json()
    expected = [p["product_id"] for p in PUBLIC if "rain jacket" in p["name"].casefold()]
    assert expected and [t["product_id"] for t in body["products"]] == expected


def test_products_with_a_photo_come_first_in_catalog_order(
    client: TestClient, tmp_path: Path
) -> None:
    jackets = [p["product_id"] for p in PUBLIC if p["department"] == "Jackets"]
    with_photo = [jackets[5], jackets[2]]
    for pid in with_photo:
        (tmp_path / f"{pid}.webp").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("not a photo")
    (tmp_path / f"{jackets[7]}.webp.bak").write_bytes(b"x")
    tiles = client.get("/api/products", params={"department": "Jackets"}).json()["products"]
    ids = [t["product_id"] for t in tiles]
    assert ids[:2] == [jackets[2], jackets[5]]
    assert ids[2:] == [pid for pid in jackets if pid not in with_photo]
    assert [t["photo"] for t in tiles] == [True, True] + [False] * (len(jackets) - 2)


def test_a_directory_or_a_dangling_link_named_like_a_photo_is_not_one(tmp_path: Path) -> None:
    """test-writer (SA-01 PR 1): only a regular file counts, so no tile shows a broken image."""
    (tmp_path / "P0001.webp").write_bytes(b"x")
    (tmp_path / "P0002.webp").mkdir()
    (tmp_path / "P0003.webp").symlink_to(tmp_path / "missing.webp")
    assert photo_ids(tmp_path) == {"P0001"}


def test_photo_ids_take_only_exact_product_file_names(tmp_path: Path) -> None:
    for name in ("P0001.webp", "P0002.WEBP", "p0003.webp", "P0004.webp.tmp", "../P0005.webp"):
        if "/" not in name:
            (tmp_path / name).write_bytes(b"x")
    assert photo_ids(tmp_path) == {"P0001"}
    assert photo_ids(tmp_path / "missing") == frozenset()


# ---- hard rule 2: the live price, every request -------------------------------------------------


def test_a_price_changed_while_running_is_the_one_shown(
    client: TestClient, store: FixtureCatalog
) -> None:
    product_id = PUBLIC[0]["product_id"]
    variant = next(v for v in store.variants_of(product_id) if v.visibility == "public")
    before = store.price(variant.variant_id, "US")
    assert before is not None
    try:
        store.set_price(variant.variant_id, "US", "1.23", before.currency)
        page = client.get("/api/product", params={"id": product_id}).json()["product"]
        shown = next(v for v in page["variants"] if v["variant_id"] == variant.variant_id)
        assert shown["price"] == {"amount": "1.23", "currency": before.currency}
        tile = next(
            t
            for t in client.get("/api/products", params={"q": PUBLIC[0]["name"]}).json()["products"]
            if t["product_id"] == product_id
        )
        assert tile["from_price"] == {"amount": "1.23", "currency": before.currency}
    finally:
        store.set_price(variant.variant_id, "US", before.amount, before.currency)


def test_the_product_page_has_the_record_and_every_public_variants_price(
    client: TestClient, store: FixtureCatalog
) -> None:
    record = PUBLIC[0]
    page = client.get("/api/product", params={"id": record["product_id"]}).json()["product"]
    assert (page["name"], page["brand"], page["description"]) == (
        record["name"],
        record["brand"],
        record["description"],
    )
    assert page["region"] == "US"
    public_variants = [
        v.variant_id for v in store.variants_of(record["product_id"]) if v.visibility == "public"
    ]
    assert [v["variant_id"] for v in page["variants"]] == public_variants
    for shown in page["variants"]:
        live = store.price(shown["variant_id"], "US")
        assert shown["price"] == (
            None if live is None else {"amount": live.amount, "currency": live.currency}
        )


def test_a_region_in_the_query_is_ignored(client: TestClient) -> None:
    body = client.get("/api/products", params={"department": "Bath", "region": "UK"}).json()
    assert body["region"] == "US"
    assert all(
        t["from_price"] is None or t["from_price"]["currency"] == "USD" for t in body["products"]
    )


# ---- hard rule 7: an internal product is an unknown one ----------------------------------------


def test_an_internal_product_is_the_same_not_found_as_an_unknown_one(client: TestClient) -> None:
    internal = client.get("/api/product", params={"id": INTERNAL})
    unknown = client.get("/api/product", params={"id": "P9999"})
    _assert_envelope(internal, 404, "validation", NOT_FOUND)
    assert internal.json() == unknown.json() and unknown.status_code == 404


# ---- hard rule 10 and input checks --------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/product", {}),
        ("/api/product", {"id": "P1"}),
        ("/api/product", {"id": "P0001; drop"}),
        ("/api/products", {"q": "x" * 65}),
    ],
)
def test_a_malformed_request_is_the_fixed_validation_envelope(
    client: TestClient, path: str, params: dict[str, str]
) -> None:
    _assert_envelope(client.get(path, params=params), 422, "validation", INVALID)


@pytest.mark.req("NFR-04")
@pytest.mark.parametrize("field", ["q", "department", "id"])
def test_card_data_in_a_query_is_refused(client: TestClient, field: str) -> None:
    path = "/api/product" if field == "id" else "/api/products"
    response = client.get(path, params={field: "4242 4242 4242 4242"})
    assert response.status_code == 422
    assert "4242" not in response.text


@pytest.mark.parametrize(
    ("path", "params"),
    [("/api/departments", {}), ("/api/products", {}), ("/api/product", {"id": "P0001"})],
)
def test_no_key_or_a_catalog_down_is_the_fixed_503(
    monkeypatch: pytest.MonkeyPatch, path: str, params: dict[str, str]
) -> None:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    for reader in (None, ShopReader("http://127.0.0.1:9/mcp", KEY)):
        app.dependency_overrides[shop_reader] = lambda r=reader: r
        try:
            _assert_envelope(
                TestClient(app).get(path, params=params), 503, "transient", UNAVAILABLE
            )
        finally:
            app.dependency_overrides.clear()


def test_the_shop_routes_are_read_only(client: TestClient) -> None:
    for path in ("/api/departments", "/api/products", "/api/product"):
        for method in ("post", "put", "patch", "delete"):
            assert client.request(method, path).status_code == 405, (method, path)


# ---- security review F2: an ID from mcp-catalog is checked before it reaches a URL --------------


class Forged:
    """A reader that returns well-shaped results with an ID the browser would put in a URL."""

    def __init__(self, product_id: str, variant_id: str = "P0001-BLK-REG-M") -> None:
        self.product_id, self.variant_id = product_id, variant_id

    async def call(self, caller: Any, tool: str, args: dict[str, Any]) -> Any:
        if tool == "browse_products":
            tile = {"product_id": self.product_id, "name": "n", "brand": "b", "department": "d"}
            return {"region": caller.region, "products": [{**tile, "from_price": None}]}
        if tool == "get_product":
            return {"product_id": args["product_id"], "name": "n", "brand": "b"}
        variant = {"variant_id": self.variant_id, "colour": "c", "size": "s", "price": None}
        return {"product_id": args["product_id"], "region": caller.region, "variants": [variant]}


@pytest.mark.parametrize("bad", ["../../api/cart?x=", "P0001/../x", "javascript:x", "P00011"])
def test_a_malformed_product_id_from_the_catalog_fails_closed(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    app.dependency_overrides[shop_reader] = lambda: Forged(bad)
    try:
        response = TestClient(app).get("/api/products")
    finally:
        app.dependency_overrides.clear()
    _assert_envelope(response, 503, "transient", UNAVAILABLE)
    assert bad not in response.text


@pytest.mark.parametrize("bad", ["../x", "P0001-blk", "P0001-BLK-REG-M?x=1"])
def test_a_malformed_variant_id_from_the_catalog_fails_closed(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    app.dependency_overrides[shop_reader] = lambda: Forged("P0001", bad)
    try:
        response = TestClient(app).get("/api/product", params={"id": "P0001"})
    finally:
        app.dependency_overrides.clear()
    _assert_envelope(response, 503, "transient", UNAVAILABLE)
