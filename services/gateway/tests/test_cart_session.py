"""The shopper's session and GET /api/cart (row #16, CHK-01; plan R2, R13; decision 0019)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import PORTAL_AGENT, CartReader, CatalogEndpoint
from northwind_common.mcp_token import CATALOG_SERVER, KEY_ENV, verify_for
from northwind_gateway.app import app
from northwind_gateway.cart import UNAVAILABLE, cart_reader
from northwind_gateway.chat import chat
from northwind_gateway.session import COOKIE, session_key, sign
from northwind_gateway.settings import shopper_caller
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog

pytestmark = pytest.mark.req("CHK-01")

KEY = catalog_testing.KEY
COOKIE_SHAPE = re.compile(
    rf"^{COOKIE}=([0-9a-f]{{32}})\.[0-9a-f]{{64}}; HttpOnly; SameSite=Strict; Path=/api$"
)


class Spy:
    """A cart reader that records who asked and returns an empty cart."""

    def __init__(self, result: Any = "empty") -> None:
        self.callers: list[Caller] = []
        self.result = result

    async def read(self, caller: Caller) -> Any:
        self.callers.append(caller)
        if isinstance(self.result, Exception):
            raise self.result
        if self.result == "empty":
            return {
                "region": caller.region,
                "lines": [],
                "item_count": 0,
                "subtotal": None,
                "currency": None,
            }
        return self.result


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


def issued(response: Any) -> str:
    [header] = response.headers.get_list("set-cookie")
    match = COOKIE_SHAPE.match(header)
    assert match, header
    return match.group(1)


def test_a_request_without_a_cookie_gets_a_signed_strict_httponly_session(spy: Spy) -> None:
    response = TestClient(app).get("/api/cart")
    assert response.status_code == 200
    session = issued(response)
    assert spy.callers[-1].session == session
    assert session not in response.text  # never in a body
    assert response.headers["cache-control"] == "no-store"


def test_a_valid_cookie_keeps_its_session_and_sets_nothing(spy: Spy) -> None:
    session = "a" * 32
    client = TestClient(app, cookies={COOKIE: sign(session_key(KEY), session)})
    response = client.get("/api/cart")
    assert response.headers.get_list("set-cookie") == []
    assert spy.callers[-1].session == session


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a" * 32,
        "a" * 32 + "." + "0" * 64,  # a forged mac
        sign(session_key(b"x" * 40), "a" * 32),  # signed with another key
        sign(KEY, "a" * 32),  # signed with the token key itself, not the derived one
        "A" * 32 + "." + "0" * 64,
        sign(session_key(KEY), "a" * 32) + "x",
        "../" * 20,
    ],
)
def test_a_bad_cookie_gets_a_new_session_never_the_claimed_one(spy: Spy, value: str) -> None:
    response = TestClient(app, cookies={COOKIE: value}).get("/api/cart")
    session = issued(response)
    assert session != "a" * 32 and spy.callers[-1].session == session


def test_two_new_visitors_get_two_sessions(spy: Spy) -> None:
    first = issued(TestClient(app).get("/api/cart"))
    second = issued(TestClient(app).get("/api/cart"))
    assert first != second


def test_the_health_check_gets_no_session(keyed: None) -> None:
    response = TestClient(app).get("/api/healthz")
    assert response.headers.get_list("set-cookie") == []


def test_without_the_key_there_is_no_session_and_the_cart_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    reader = CartReader("http://127.0.0.1:1/mcp", KEY)
    app.dependency_overrides[cart_reader] = lambda: reader
    try:
        response = TestClient(app).get("/api/cart")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.headers.get_list("set-cookie") == []


@pytest.mark.parametrize("result", [None, RuntimeError("upstream said: secret detail")])
def test_any_cart_failure_is_one_fixed_503(keyed: None, result: Any) -> None:
    reader = Spy(result)
    app.dependency_overrides[cart_reader] = lambda: reader
    try:
        response = TestClient(app).get("/api/cart")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json() == {
        "isError": True,
        "errorCategory": "transient",
        "isRetryable": True,
        "message": UNAVAILABLE,
    }
    assert "secret" not in response.text


def test_the_chat_route_takes_its_caller_with_the_session() -> None:
    from typing import get_type_hints

    hint = get_type_hints(chat, include_extras=True)["caller"]
    [depends] = hint.__metadata__
    assert depends.dependency is shopper_caller


def test_the_catalog_token_carries_the_callers_session() -> None:
    caller = Caller(region="UK", session="b" * 32)
    token = CatalogEndpoint("http://unused", KEY).mint(caller)
    assert verify_for(token, key=KEY, server=CATALOG_SERVER).sid == "b" * 32
    assert (
        verify_for(
            CatalogEndpoint("http://unused", KEY).mint(Caller(region="UK")),
            key=KEY,
            server=CATALOG_SERVER,
        ).sid
        is None
    )


def test_the_panel_reads_the_real_cart_as_the_portal_agent(keyed: None) -> None:
    """End to end over HTTP: the assistant's token adds to the cart for session S; the gateway,
    with the cookie for S, shows that cart through the real reader."""
    from northwind_mcp_catalog.server import CatalogServer
    from northwind_mcp_catalog.tools import TOOLS

    store = FixtureCatalog()
    store.set_stock("P0001-BLK-REG-M", "US", 5)
    session = "c" * 32
    with catalog_testing.running(CatalogServer(store, key=KEY, tools=TOOLS)) as url:
        assistant = catalog_testing.token("shopper-assistant", "US", session=session)[0]

        async def add() -> None:
            async with catalog_testing.connect(url, assistant) as client:
                result = await client.call_tool(
                    "add_to_cart",
                    {
                        "variant_id": "P0001-BLK-REG-M",
                        "quantity": 2,
                        "idempotency_key": "gateway-e2e-01",
                    },
                )
                assert not result.is_error

        asyncio.run(add())
        app.dependency_overrides[cart_reader] = lambda: CartReader(url, KEY)
        try:
            mine = TestClient(app, cookies={COOKIE: sign(session_key(KEY), session)}).get(
                "/api/cart"
            )
            stranger = TestClient(app).get("/api/cart")
        finally:
            app.dependency_overrides.clear()
    assert mine.status_code == 200
    [line] = mine.json()["cart"]["lines"]
    assert (line["variant_id"], line["quantity"]) == ("P0001-BLK-REG-M", 2)
    assert line["unit_price"] == store.price("P0001-BLK-REG-M", "US").amount  # type: ignore[union-attr]
    assert stranger.json()["cart"]["lines"] == []
    assert PORTAL_AGENT == "shopper-portal"


def test_the_portal_agent_cannot_write_even_with_a_valid_session(keyed: None) -> None:
    store = FixtureCatalog()
    from northwind_mcp_catalog.server import CatalogServer
    from northwind_mcp_catalog.tools import TOOLS

    with catalog_testing.running(CatalogServer(store, key=KEY, tools=TOOLS)) as url:
        portal = catalog_testing.token(PORTAL_AGENT, "US", session="d" * 32)[0]

        async def names() -> set[str]:
            async with catalog_testing.connect(url, portal) as client:
                return {t.name for t in (await client.list_tools()).tools}

        assert asyncio.run(names()) == {"get_cart"}


def test_without_the_key_the_real_reader_gives_the_fixed_503_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Security review F6: no override, so the real `cart_reader` dependency runs without a key."""
    monkeypatch.delenv(KEY_ENV, raising=False)
    app.dependency_overrides.clear()
    response = TestClient(app, raise_server_exceptions=False).get("/api/cart")
    assert response.status_code == 503
    assert response.json()["message"] == UNAVAILABLE
