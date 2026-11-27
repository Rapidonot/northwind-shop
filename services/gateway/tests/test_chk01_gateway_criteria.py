"""CHK-01 (row #16): the cart shown in the chat panel, as the gateway serves it.

Written from the plan's R2, R12 and R13 and decision 0019, beside `test_cart_session.py`. It covers
what that file does not: the session and the region come from the server and never from the request,
a session cookie issued once is the same session on the next request (so the panel and the
assistant see one cart), the portal's route can read a cart and cannot write one or place an order,
and the cookie helpers keep to their contract.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CartReader
from northwind_common.mcp_token import KEY_ENV
from northwind_gateway.app import app
from northwind_gateway.cart import cart_reader
from northwind_gateway.session import (
    COOKIE,
    SESSION_PATHS,
    session_key,
    set_cookie_header,
    sign,
    verified,
)
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.tools import TOOLS

pytestmark = pytest.mark.req("CHK-01")

KEY = catalog_testing.KEY
V = "P0001-BLK-REG-M"
SESSION = "ab" * 16  # fixed: never a random value in a test argument


class Spy:
    def __init__(self) -> None:
        self.callers: list[Caller] = []

    async def read(self, caller: Caller) -> Any:
        self.callers.append(caller)
        return {
            "region": caller.region,
            "lines": [],
            "item_count": 0,
            "subtotal": None,
            "currency": None,
        }


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, KEY.decode())


@pytest.fixture
def spy(keyed: None) -> Iterator[Spy]:
    reader = Spy()
    app.dependency_overrides[cart_reader] = lambda: reader
    try:
        yield reader
    finally:
        app.dependency_overrides.clear()


# ---- the panel's route cannot write and cannot place an order ----


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_the_cart_route_is_read_only(spy: Spy, method: str) -> None:
    response = TestClient(app).request(method, "/api/cart", json={"variant_id": V})
    assert response.status_code == 405
    assert spy.callers == []


# SA-01 PR 2 (the user's stop 5 yes, 2026-11-27): exactly these order, checkout and cart-write
# routes, each with exactly its one method. Any other such route fails the pin below.
ORDER_AND_CART_WRITE_ROUTES = {
    "/api/orders": {"get"},
    "/api/checkout/summary": {"get"},
    "/api/checkout/pay": {"post"},
    "/api/cart/items": {"post"},
    "/api/cart/quantity": {"post"},
    "/api/cart/remove": {"post"},
}


def test_the_gateway_has_exactly_the_approved_order_and_cart_write_routes() -> None:
    schema = app.openapi()["paths"]
    for path in schema:
        if path in ORDER_AND_CART_WRITE_ROUTES:
            assert set(schema[path]) == ORDER_AND_CART_WRITE_ROUTES[path], path
        else:
            assert not re.search(r"order|checkout|payment|purchase|cart/", path), path
    assert set(ORDER_AND_CART_WRITE_ROUTES) <= set(schema)
    assert set(schema["/api/cart"]) == {"get"}


def test_the_session_paths_are_the_chat_the_cart_the_sign_in_and_the_handoff() -> None:
    # Row #27 (CHK-06): the demo sign-in binds its shopper to the session. Row #23 (SA-06): the
    # handoff names its session or shopper. SA-01 PR 1: the shop pages' reads, so a signed-in
    # shopper's pages price in their own region. SA-01 PR 2 (the user's stop 5 yes, 2026-11-27):
    # the bag's cart writes, the order history and the demo checkout. Still exact.
    assert set(SESSION_PATHS) == {
        "/api/chat",
        "/api/cart",
        "/api/session/shopper",
        "/api/handoff",
        "/api/departments",
        "/api/products",
        "/api/product",
        "/api/cart/items",
        "/api/cart/quantity",
        "/api/cart/remove",
        "/api/orders",
        "/api/checkout/summary",
        "/api/checkout/pay",
    }


# ---- who is asking comes from the server, never from the request ----


def test_a_session_or_region_in_the_query_or_headers_is_ignored(
    spy: Spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEV_SHOPPER_REGION", "UK")
    forged = "ee" * 16
    response = TestClient(app).get(
        "/api/cart",
        params={"session": forged, "session_id": forged, "sid": forged, "region": "US"},
        headers={
            "X-Session": forged,
            "X-Session-Id": forged,
            "X-Region": "US",
            "X-Shopper-Region": "US",
            "Authorization": f"Bearer {forged}",
        },
    )
    assert response.status_code == 200
    [caller] = spy.callers
    assert caller.session is not None and caller.session != forged
    assert caller.region == "UK" and caller.audience == "shopper"


def test_the_cookie_issued_on_one_request_is_the_session_of_the_next(spy: Spy) -> None:
    client = TestClient(app)
    first = client.get("/api/cart")
    assert first.headers.get_list("set-cookie")
    second = client.get("/api/cart")
    assert second.headers.get_list("set-cookie") == []  # a kept session sets nothing again
    assert spy.callers[0].session == spy.callers[1].session
    assert spy.callers[0].session is not None


def test_a_cookie_for_a_session_cannot_be_moved_to_a_changed_session_id(spy: Spy) -> None:
    good = sign(session_key(KEY), SESSION)
    swapped = "cd" * 16 + good[32:]  # another id under the first id's mac
    response = TestClient(app, cookies={COOKIE: swapped}).get("/api/cart")
    assert response.status_code == 200
    assert spy.callers[-1].session not in (SESSION, "cd" * 16)


def test_the_session_id_is_not_in_the_cart_body(spy: Spy) -> None:
    client = TestClient(app, cookies={COOKIE: sign(session_key(KEY), SESSION)})
    assert SESSION not in client.get("/api/cart").text


# ---- the panel shows the server's cart, end to end ----


def test_the_cookie_the_gateway_issued_is_the_cart_the_assistant_writes(
    keyed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cookie issued on a first read; the assistant's token for that session adds to the cart in the
    shopper's region; the next panel read shows the line with the live price, and the read of a
    stranger without that cookie is empty. Stock is read live between the two panel reads."""
    monkeypatch.setenv("DEV_SHOPPER_REGION", "UK")
    store = FixtureCatalog()
    store.set_stock(V, "UK", 5)
    store.set_price(V, "UK", "12.34", "GBP")
    with catalog_testing.running(CatalogServer(store, key=KEY, tools=TOOLS)) as url:
        reader = CartReader(url, KEY)
        spy_reader = Spy()
        app.dependency_overrides[cart_reader] = lambda: spy_reader
        client = TestClient(app)
        try:
            client.get("/api/cart")
            session = spy_reader.callers[0].session
        finally:
            app.dependency_overrides.clear()
        assert session is not None

        async def add() -> None:
            bearer = catalog_testing.token("shopper-assistant", "UK", session=session)[0]
            async with catalog_testing.connect(url, bearer) as mcp:
                result = await mcp.call_tool(
                    "add_to_cart",
                    {"variant_id": V, "quantity": 2, "idempotency_key": "gateway-key-0001"},
                )
                assert not result.is_error

        asyncio.run(add())
        app.dependency_overrides[cart_reader] = lambda: reader
        try:
            mine = client.get("/api/cart")
            store.set_stock(V, "UK", 0)
            later = client.get("/api/cart")
            stranger = TestClient(app).get("/api/cart")
        finally:
            app.dependency_overrides.clear()
    [line] = mine.json()["cart"]["lines"]
    assert (line["variant_id"], line["quantity"], line["unit_price"]) == (V, 2, "12.34")
    assert mine.json()["cart"]["subtotal"] == "24.68"
    assert mine.json()["cart"]["region"] == "UK"
    [after] = later.json()["cart"]["lines"]
    assert (after["on_hand"], after["in_stock"]) == (0, False)
    assert stranger.json()["cart"]["lines"] == []


def test_the_panel_read_changes_nothing_in_the_cart(keyed: None) -> None:
    store = FixtureCatalog()
    store.set_stock(V, "US", 5)
    with catalog_testing.running(CatalogServer(store, key=KEY, tools=TOOLS)) as url:
        app.dependency_overrides[cart_reader] = lambda: CartReader(url, KEY)
        client = TestClient(app, cookies={COOKIE: sign(session_key(KEY), SESSION)})
        try:
            bodies = [client.get("/api/cart").json() for _ in range(3)]
        finally:
            app.dependency_overrides.clear()
    assert bodies[0] == bodies[1] == bodies[2]
    assert bodies[0]["cart"]["lines"] == []


# ---- the cookie helpers ----


def test_a_signed_cookie_verifies_to_its_session_only_under_its_own_key() -> None:
    key = session_key(KEY)
    value = sign(key, SESSION)
    assert verified(key, value) == SESSION
    assert verified(session_key(b"x" * 40), value) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v[:-1] + ("0" if v[-1] != "0" else "1"),  # last mac character
        lambda v: v[:5] + ("0" if v[5] != "0" else "1") + v[6:],  # one id character
        lambda v: v.upper(),
        lambda v: v + "\n",
        lambda v: " " + v,
        lambda v: v.replace(".", ":"),
        lambda v: "",
    ],
)
def test_any_change_to_a_signed_cookie_is_refused(mutate: Any) -> None:
    key = session_key(KEY)
    assert verified(key, mutate(sign(key, SESSION))) is None


def test_the_session_key_is_not_the_token_key_and_depends_on_it() -> None:
    assert session_key(KEY) != KEY
    assert session_key(KEY) != session_key(b"y" * 40)
    assert session_key(KEY) == session_key(KEY)


def test_the_set_cookie_header_has_the_security_attributes_and_no_secret() -> None:
    name, value = set_cookie_header(session_key(KEY), SESSION)
    text = value.decode("ascii")
    assert name == b"set-cookie"
    attributes = {part.strip().lower() for part in text.split(";")[1:]}
    assert {"httponly", "samesite=strict", "path=/api"} <= attributes
    assert KEY.decode() not in text
