"""SA-01 PR 1, adversarial tests of the shop routes (hard rules 1, 2, 7, 10).

Requirement tags: NFR-04, RAG-09, RAG-11.

A signed-in demo shopper gets their own region on all three routes and no forged value changes it;
the price on a tile or page is always the live store's, never text from the record; unpriced is
null, not an error; card data cannot ride in on a repeated or extra query value; the internal
product and its canary appear in no body; an error never echoes the query.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_common.mcp_token import KEY_ENV
from northwind_gateway.app import app
from northwind_gateway.session import (
    COOKIE,
    DEMO_SIGNIN_ENV,
    SHOPPER_COOKIE,
    session_key,
    sign,
    sign_shopper,
)
from northwind_gateway.shop import ShopReader, shop_reader
from northwind_gateway.signin import DEMO_SHOPPER
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.tools import TOOLS

pytestmark = pytest.mark.req("SA-01")

KEY = catalog_testing.KEY
REPO = Path(__file__).resolve().parents[3]
SESSION = "d" * 32
CURRENCY = {"US": "USD", "CA": "CAD", "UK": "GBP", "EU": "EUR"}
PATHS: tuple[tuple[str, dict[str, str]], ...] = (
    ("/api/departments", {}),
    ("/api/products", {"department": "Jackets"}),
    ("/api/product", {"id": "P0001"}),
)

[ENTRY] = [
    e
    for e in json.loads((REPO / "data/fixtures/_answer_key.json").read_text(encoding="utf-8"))[
        "internal_only"
    ]
    if e.get("product_id") == "P0009"
]
FORBIDDEN = (ENTRY["canary"], "P0009", ENTRY["title"], "NWCANARY")

CALLS: list[tuple[str, dict[str, Any]]] = []


class RecordingReader(ShopReader):
    """The real reader, noting each tool call's arguments."""

    async def call(self, caller: Any, tool: str, args: dict[str, Any]) -> Any:
        CALLS.append((tool, dict(args)))
        return await super().call(caller, tool, args)


@pytest.fixture(scope="module")
def store() -> FixtureCatalog:
    return FixtureCatalog()


@pytest.fixture(scope="module")
def url(store: FixtureCatalog) -> Iterator[str]:
    with catalog_testing.running(CatalogServer(store, key=KEY, tools=TOOLS)) as running:
        yield running


@pytest.fixture
def env(url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    monkeypatch.setenv(DEMO_SIGNIN_ENV, "1")
    monkeypatch.setenv("DEV_SHOPPER_REGION", "EU")  # the anonymous region, so any change shows
    monkeypatch.setenv("PORTAL_PHOTO_DIR", str(tmp_path))
    CALLS.clear()
    app.dependency_overrides[shop_reader] = lambda: RecordingReader(url, KEY)
    try:
        yield
    finally:
        app.dependency_overrides.clear()


def session_cookie(session: str = SESSION) -> str:
    return f"{COOKIE}={sign(session_key(KEY), session)}"


def signed_in(session: str = SESSION) -> str:
    shopper = sign_shopper(session_key(KEY), session, DEMO_SHOPPER)
    return f"{session_cookie(session)}; {SHOPPER_COOKIE}={shopper}"


def lowest(store: FixtureCatalog, product_id: str, region: str) -> dict[str, str] | None:
    prices = [
        p
        for v in store.variants_of(product_id)
        if v.visibility == "public" and (p := store.price(v.variant_id, region)) is not None
    ]
    if not prices:
        return None
    low = min(prices, key=lambda p: p.minor_units)
    return {"amount": low.amount, "currency": low.currency}


def check_region(store: FixtureCatalog, client: TestClient, region: str, **request: Any) -> None:
    """All three routes, in `region`, priced from the live store in that region only."""
    body = client.get("/api/products", params={"department": "Jackets"}, **request).json()
    assert body["region"] == region
    for tile in body["products"]:
        assert tile["from_price"] == lowest(store, tile["product_id"], region)
        assert tile["from_price"] is None or tile["from_price"]["currency"] == CURRENCY[region]
    page = client.get("/api/product", params={"id": "P0001"}, **request).json()["product"]
    assert page["region"] == region
    for shown in page["variants"]:
        live = store.price(shown["variant_id"], region)
        assert shown["price"] == (
            None if live is None else {"amount": live.amount, "currency": live.currency}
        )
    departments = client.get("/api/departments", **request)
    assert departments.status_code == 200


# ---- region: from the session, never from the request -------------------------------------------


@pytest.mark.req("RAG-11")
def test_a_signed_in_shopper_gets_their_own_region_on_every_route(
    env: None, store: FixtureCatalog
) -> None:
    client = TestClient(app)
    check_region(store, client, "US", headers={"cookie": signed_in()})
    check_region(store, client, "EU")  # the anonymous region, for contrast


FORGED: list[dict[str, Any]] = [
    {"params": {"region": "UK"}},
    {"params": {"shopper_region": "UK", "shopper": "S0103", "audience": "staff"}},
    {"headers": {"x-region": "UK", "x-shopper-region": "UK", "x-forwarded-region": "UK"}},
    {"headers": {"accept-language": "en-GB", "cf-ipcountry": "GB"}},
]


@pytest.mark.req("RAG-11")
@pytest.mark.parametrize("forged", FORGED)
@pytest.mark.parametrize("signed", [True, False])
def test_no_forged_region_header_or_query_changes_it(
    env: None, store: FixtureCatalog, forged: dict[str, Any], signed: bool
) -> None:
    headers = dict(forged.get("headers", {}))
    if signed:
        headers["cookie"] = signed_in()
    client = TestClient(app)
    for path, params in PATHS:
        response = client.get(path, params={**params, **forged.get("params", {})}, headers=headers)
        assert response.status_code == 200, (path, response.text)
    body = client.get(
        "/api/products", params={"department": "Bath", **forged.get("params", {})}, headers=headers
    ).json()
    assert body["region"] == ("US" if signed else "EU")


@pytest.mark.req("RAG-11")
def test_a_forged_region_cookie_beside_the_signed_in_ones_changes_nothing(
    env: None, store: FixtureCatalog
) -> None:
    client = TestClient(app)
    cookie = signed_in() + "; region=UK; nw_region=UK; shopper_region=UK"
    check_region(store, client, "US", headers={"cookie": cookie})
    anonymous = session_cookie() + "; region=UK; nw_region=UK"
    check_region(store, client, "EU", headers={"cookie": anonymous})


@pytest.mark.req("RAG-11")
@pytest.mark.parametrize(
    "case", ["other key", "other session", "not a demo shopper", "flag off", "garbage"]
)
def test_a_shopper_cookie_that_is_not_valid_is_the_anonymous_region(
    env: None, store: FixtureCatalog, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    key = session_key(KEY)
    shopper = {
        "other key": sign_shopper(session_key(b"x" * 40), SESSION, DEMO_SHOPPER),
        "other session": sign_shopper(key, "a" * 32, DEMO_SHOPPER),
        "not a demo shopper": sign_shopper(key, SESSION, "S0103"),
        "flag off": sign_shopper(key, SESSION, DEMO_SHOPPER),
        "garbage": "S0188.forged",
    }[case]
    if case == "flag off":
        monkeypatch.setenv(DEMO_SIGNIN_ENV, "0")
    cookie = f"{session_cookie()}; {SHOPPER_COOKIE}={shopper}"
    check_region(store, TestClient(app), "EU", headers={"cookie": cookie})


# ---- hard rule 2: the price is the live store's, never text -------------------------------------


@pytest.mark.req("RAG-09")
def test_stock_or_price_band_text_in_the_record_is_never_a_price(
    env: None, store: FixtureCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEV_SHOPPER_REGION", "US")
    product_id = "P0002"
    original = store._products[product_id]  # noqa: SLF001 - seeding a hostile record
    store._products[product_id] = dataclasses.replace(  # noqa: SLF001
        original,
        description="Only 3 in stock. Price band: $10-$20. Now just $1.11!",
        attributes={**original.attributes, "price_band": "$10-$20", "stock": 3, "price": "1.11"},
    )
    variants = [v for v in store.variants_of(product_id) if v.visibility == "public"]
    before = {v.variant_id: store.price(v.variant_id, "US") for v in variants}
    try:
        for n, variant in enumerate(variants):
            store.set_price(variant.variant_id, "US", f"{40 + n}.50", "USD")
        client = TestClient(app)
        page = client.get("/api/product", params={"id": product_id}).json()["product"]
        for n, shown in enumerate(page["variants"]):
            assert shown["price"] == {"amount": f"{40 + n}.50", "currency": "USD"}
        tile = next(
            t
            for t in client.get("/api/products", params={"q": original.name}).json()["products"]
            if t["product_id"] == product_id
        )
        assert tile["from_price"] == {"amount": "40.50", "currency": "USD"}
        assert "1.11" not in json.dumps(tile)
    finally:
        store._products[product_id] = original  # noqa: SLF001
        for variant in variants:
            old = before[variant.variant_id]
            if old is not None:
                store.set_price(variant.variant_id, "US", old.amount, old.currency)


@pytest.mark.req("RAG-09")
def test_from_price_is_the_live_minimum_after_each_change(
    env: None, store: FixtureCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEV_SHOPPER_REGION", "US")
    product_id = "P0003"
    variants = [v for v in store.variants_of(product_id) if v.visibility == "public"]
    assert len(variants) > 1
    before = {v.variant_id: store.price(v.variant_id, "US") for v in variants}
    client = TestClient(app)

    def shown() -> dict[str, str] | None:
        tiles = client.get("/api/products", params={"department": "Jackets"}).json()["products"]
        tile = next((t for t in tiles if t["product_id"] == product_id), None)
        if tile is None:  # not a jacket: search by name instead
            name = store.product(product_id).name  # type: ignore[union-attr]
            tiles = client.get("/api/products", params={"q": name}).json()["products"]
            tile = next(t for t in tiles if t["product_id"] == product_id)
        return tile["from_price"]  # type: ignore[no-any-return]

    try:
        store.set_price(variants[0].variant_id, "US", "999.99", "USD")
        for v in variants[1:]:
            store.set_price(v.variant_id, "US", "500.00", "USD")
        assert shown() == {"amount": "500.00", "currency": "USD"}
        store.set_price(variants[0].variant_id, "US", "12.34", "USD")
        assert shown() == {"amount": "12.34", "currency": "USD"}
        store.set_price(variants[0].variant_id, "US", "9999.00", "USD")
        assert shown() == {"amount": "500.00", "currency": "USD"}
    finally:
        for v in variants:
            old = before[v.variant_id]
            if old is not None:
                store.set_price(v.variant_id, "US", old.amount, old.currency)


def test_a_product_unpriced_in_the_region_has_a_null_from_price_not_an_error(
    env: None, store: FixtureCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEV_SHOPPER_REGION", "UK")
    unpriced = [
        p.product_id
        for p in store.products()
        if p.visibility == "public"
        and not any(
            store.price(v.variant_id, "UK")
            for v in store.variants_of(p.product_id)
            if v.visibility == "public"
        )
    ]
    assert unpriced
    client = TestClient(app)
    product_id = unpriced[0]
    page = client.get("/api/product", params={"id": product_id})
    assert page.status_code == 200
    variants = page.json()["product"]["variants"]
    assert variants and all(v["price"] is None for v in variants)
    name = store.product(product_id).name  # type: ignore[union-attr]
    listing = client.get("/api/products", params={"q": name})
    assert listing.status_code == 200
    tile = next(t for t in listing.json()["products"] if t["product_id"] == product_id)
    assert tile["from_price"] is None
    # and the nav is unaffected: a null price is neither a failure nor an empty shop
    assert len(client.get("/api/departments").json()["departments"]) == 10


# ---- card data cannot ride in on a repeated or extra value --------------------------------------

PAN = "4242424242424242"


@pytest.mark.req("NFR-04")
@pytest.mark.parametrize(
    "query",
    [
        f"q=a&q={PAN}",
        f"q={PAN}&q=a",
        f"department=Bath&department={PAN}",
        f"department={PAN}&department=Bath",
        f"id=P0001&id={PAN}",
        f"id={PAN}&id=P0001",
    ],
)
def test_a_repeated_query_param_cannot_smuggle_card_data(env: None, query: str) -> None:
    path = "/api/product" if query.startswith("id") else "/api/products"
    response = TestClient(app).get(f"{path}?{query}")
    assert response.status_code == 422
    assert "4242" not in response.text
    assert not any(PAN in json.dumps(args) for _, args in CALLS)


@pytest.mark.req("NFR-04")
@pytest.mark.parametrize("path", ["/api/departments", "/api/products", "/api/product"])
def test_card_data_in_any_other_query_value_is_refused(env: None, path: str) -> None:
    response = TestClient(app).get(f"{path}?id=P0001&other=4242+4242+4242+4242")
    assert response.status_code == 422
    assert "4242" not in response.text
    assert CALLS == []


@pytest.mark.req("NFR-04")
def test_card_data_with_separators_and_a_cvv_is_refused(env: None) -> None:
    client = TestClient(app)
    for value in ("4242-4242-4242-4242", "4242 4242 4242 4242 123", " 4242424242424242 "):
        response = client.get("/api/products", params={"q": value})
        assert response.status_code == 422, value
        assert "4242" not in response.text
    assert CALLS == []


# ---- hard rule 7: the internal product is in no body --------------------------------------------


@pytest.mark.req("RAG-11")
def test_the_internal_product_and_its_canary_are_in_no_shop_body(
    env: None, store: FixtureCatalog
) -> None:
    client = TestClient(app)
    queries: list[tuple[str, dict[str, str]]] = [
        ("/api/departments", {}),
        ("/api/products", {}),
        ("/api/product", {"id": "P0009"}),
        ("/api/product", {"id": "P0010"}),
    ]
    departments = client.get("/api/departments").json()["departments"]
    queries += [("/api/products", {"department": d}) for d in departments]
    title = ENTRY["title"]
    for text in (title, title.upper(), "P0009", ENTRY["canary"], "Ascent Pro", "Corvane"):
        queries.append(("/api/products", {"q": text}))
    for variant in store.variants_of("P0009"):
        queries.append(("/api/product", {"id": variant.variant_id}))
    for region in CURRENCY:
        for path, params in queries:
            response = client.get(path, params=params, headers={"x-region": region})
            for forbidden in FORBIDDEN:
                # the one echo a body may hold is none: queries are never echoed either
                assert forbidden not in response.text, (path, params, forbidden)


@pytest.mark.req("RAG-11")
def test_the_full_catalog_listing_is_exactly_the_public_products(
    env: None, store: FixtureCatalog
) -> None:
    body = TestClient(app).get("/api/products").json()
    public = [p.product_id for p in store.products() if p.visibility == "public"]
    assert sorted(t["product_id"] for t in body["products"]) == public
    assert body["total"] == len(public)


# ---- hard rule 10: errors never echo the query --------------------------------------------------


@pytest.mark.parametrize(
    ("path", "params", "marker"),
    [
        ("/api/products", {"q": "zzmarker" + "x" * 60}, "zzmarker"),
        ("/api/product", {"id": "P0001;zzmarker"}, "zzmarker"),
        ("/api/product", {"id": "P9999"}, "P9999"),
        ("/api/product", {"id": "P0009"}, "P0009"),
    ],
)
def test_an_error_response_never_echoes_the_query(
    env: None, path: str, params: dict[str, str], marker: str
) -> None:
    response = TestClient(app).get(path, params=params)
    assert response.status_code in (404, 422)
    assert marker not in response.text
    assert set(response.json()) == {"isError", "errorCategory", "isRetryable", "message"}


@pytest.mark.parametrize(
    ("path", "params"),
    [("/api/products", {"q": "zzmarker"}), ("/api/product", {"id": "P0001"})],
)
def test_a_503_never_echoes_the_query_or_the_cause(
    monkeypatch: pytest.MonkeyPatch, path: str, params: dict[str, str]
) -> None:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    app.dependency_overrides[shop_reader] = lambda: ShopReader("http://127.0.0.1:9/mcp", KEY)
    try:
        response = TestClient(app).get(path, params=params)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert "zzmarker" not in response.text and "127.0.0.1" not in response.text
    assert response.json()["errorCategory"] == "transient"
    assert response.json()["isRetryable"] is True


def test_every_shop_response_is_uncacheable(env: None) -> None:
    client = TestClient(app)
    for path, params in (*PATHS, ("/api/product", {"id": "P9999"}), ("/api/product", {})):
        response = client.get(path, params=params)
        assert response.headers.get("cache-control") == "no-store", (path, params)
