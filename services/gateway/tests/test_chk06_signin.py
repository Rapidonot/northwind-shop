"""Row #27 (CHK-06, NFR-01; decision 0023): the demo sign-in.

- Only with `NORTHWIND_DEMO_SIGNIN=1` does `POST /api/session/shopper` bind the one fixed synthetic
  shopper to the session; with the flag off it is the same 404 as a path that does not exist.
- No request value can name the shopper: the body is ignored.
- The shopper cookie is valid only with the session it was issued for, only while the flag is on,
  and only with the gateway's own signature.
- A signed-in chat turn passes the orders endpoint and a caller with the shopper and the shopper's
  region; an anonymous one passes a caller with no shopper (so it gets no order tool).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_assistant.caller import Caller
from northwind_backend.fakes.orders import DATA
from northwind_common.mcp_token import KEY_ENV
from northwind_gateway import chat as chat_module
from northwind_gateway.app import app
from northwind_gateway.cart import cart_reader
from northwind_gateway.chat import ChatServices, chat_services
from northwind_gateway.session import (
    COOKIE,
    DEMO_SIGNIN_ENV,
    SHOPPER_COOKIE,
    session_key,
    sign,
    sign_shopper,
)
from northwind_gateway.signin import DEMO_SHOPPER, DEMO_SHOPPERS
from northwind_mcp_catalog import testing as catalog_testing

pytestmark = [pytest.mark.req("CHK-06"), pytest.mark.req("NFR-01")]

KEY = catalog_testing.KEY
SESSION = "e" * 32
PATH = "/api/session/shopper"
OTHER_KEY_SIGNED = sign_shopper(session_key(b"x" * 40), SESSION, "S0188")


class Spy:
    def __init__(self) -> None:
        self.callers: list[Caller] = []

    async def read(self, caller: Caller) -> dict[str, Any]:
        self.callers.append(caller)
        return {
            "region": caller.region,
            "lines": [],
            "item_count": 0,
            "subtotal": None,
            "currency": None,
        }


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Iterator[Spy]:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    monkeypatch.setenv("DEV_SHOPPER_REGION", "EU")  # the anonymous region, so a change shows
    reader = Spy()
    app.dependency_overrides[cart_reader] = lambda: reader
    try:
        yield reader
    finally:
        app.dependency_overrides.clear()


def session_cookie(session: str = SESSION) -> str:
    return f"{COOKIE}={sign(session_key(KEY), session)}"


def shopper_cookies(response: Any) -> list[str]:
    return [
        h for h in response.headers.get_list("set-cookie") if h.startswith(f"{SHOPPER_COOKIE}=")
    ]


def signed_in_header(session: str = SESSION, shopper: str = DEMO_SHOPPER) -> str:
    signed = sign_shopper(session_key(KEY), session, shopper)
    return f"{session_cookie(session)}; {SHOPPER_COOKIE}={signed}"


def test_the_demo_shopper_and_region_are_on_file() -> None:
    rows = [json.loads(line) for line in (DATA / "shoppers.jsonl").read_text().splitlines() if line]
    on_file = {row["shopper_id"]: row["region"] for row in rows}
    assert DEMO_SHOPPERS == {DEMO_SHOPPER: on_file[DEMO_SHOPPER]} == {"S0188": "US"}


def test_with_the_flag_off_sign_in_is_the_same_404_as_no_route(
    spy: Spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DEMO_SIGNIN_ENV, raising=False)
    client = TestClient(app)
    response = client.post(PATH, headers={"cookie": session_cookie()})
    missing = client.post("/api/session/nobody")
    assert response.status_code == missing.status_code == 404
    assert response.json() == missing.json()
    assert shopper_cookies(response) == []


@pytest.mark.parametrize("value", ["0", "true", "yes", " 1", ""])
def test_only_exactly_1_turns_the_flag_on(
    spy: Spy, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(DEMO_SIGNIN_ENV, value)
    assert TestClient(app).post(PATH, headers={"cookie": session_cookie()}).status_code == 404


def test_sign_in_binds_the_demo_shopper_to_the_session(
    spy: Spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEMO_SIGNIN_ENV, "1")
    client = TestClient(app)
    response = client.post(PATH, headers={"cookie": session_cookie()}, json={"shopper_id": "S0103"})
    assert response.status_code == 200 and response.json() == {"signed_in": True}
    assert response.headers["cache-control"] == "no-store"
    [header] = shopper_cookies(response)
    assert header.endswith("; HttpOnly; SameSite=Strict; Path=/api")
    value = header.split(";", 1)[0].split("=", 1)[1]
    assert value == sign_shopper(session_key(KEY), SESSION, DEMO_SHOPPER)  # the body was ignored
    client.get("/api/cart", headers={"cookie": f"{session_cookie()}; {SHOPPER_COOKIE}={value}"})
    caller = spy.callers[-1]
    assert (caller.shopper_id, caller.region, caller.session) == ("S0188", "US", SESSION)


def test_a_new_visitor_signing_in_gets_both_cookies_bound_together(
    spy: Spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEMO_SIGNIN_ENV, "1")
    response = TestClient(app).post(PATH)
    cookies = response.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith(f"{COOKIE}=")).split("=", 1)[1].split(".")[0]
    [shopper] = shopper_cookies(response)
    assert shopper.split(";")[0].split("=", 1)[1] == sign_shopper(
        session_key(KEY), session, "S0188"
    )


@pytest.mark.parametrize(
    "header",
    [
        session_cookie(),  # never signed in
        f"{session_cookie()}; {SHOPPER_COOKIE}=S0188",  # no mac
        f"{session_cookie()}; {SHOPPER_COOKIE}=S0188.{'0' * 64}",  # a forged mac
        signed_in_header(session="f" * 32).replace(session_cookie("f" * 32), session_cookie()),
        f"{session_cookie()}; {SHOPPER_COOKIE}={OTHER_KEY_SIGNED}",  # another key
        signed_in_header(shopper="S0103"),  # signed, but not a demo shopper
    ],
)
def test_anything_but_this_sessions_signed_demo_shopper_is_anonymous(
    spy: Spy, monkeypatch: pytest.MonkeyPatch, header: str
) -> None:
    monkeypatch.setenv(DEMO_SIGNIN_ENV, "1")
    TestClient(app).get("/api/cart", headers={"cookie": header})
    caller = spy.callers[-1]
    assert caller.shopper_id is None and caller.region == "EU"


def test_turning_the_flag_off_signs_everyone_out(spy: Spy, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEMO_SIGNIN_ENV, "1")
    client = TestClient(app)
    client.get("/api/cart", headers={"cookie": signed_in_header()})
    assert spy.callers[-1].shopper_id == "S0188"
    monkeypatch.delenv(DEMO_SIGNIN_ENV)
    client.get("/api/cart", headers={"cookie": signed_in_header()})
    assert spy.callers[-1].shopper_id is None


def test_a_reissued_session_drops_the_sign_in(spy: Spy, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEMO_SIGNIN_ENV, "1")
    forged_session = f"{COOKIE}={'a' * 32}.{'0' * 64}"
    shopper = sign_shopper(session_key(KEY), "a" * 32, "S0188")
    TestClient(app).get(
        "/api/cart", headers={"cookie": f"{forged_session}; {SHOPPER_COOKIE}={shopper}"}
    )
    assert spy.callers[-1].shopper_id is None


class Captured:
    def __init__(self) -> None:
        self.calls: list[tuple[Caller, Any]] = []

    def stream(self, message: str, caller: Caller, *args: Any, orders: Any = None) -> Any:
        self.calls.append((caller, orders))

        async def nothing() -> Any:
            return
            yield

        return nothing()


@pytest.mark.parametrize("signed_in", [True, False])
def test_a_chat_turn_gets_the_orders_endpoint_and_the_caller_as_signed_in(
    spy: Spy, monkeypatch: pytest.MonkeyPatch, signed_in: bool
) -> None:
    monkeypatch.setenv(DEMO_SIGNIN_ENV, "1")
    captured = Captured()
    monkeypatch.setattr(chat_module, "stream_answer", captured.stream)
    services = ChatServices(rag=None, runtime=None, catalog=None, orders="orders-endpoint")  # type: ignore[arg-type]
    app.dependency_overrides[chat_services] = lambda: services
    header = signed_in_header() if signed_in else session_cookie()
    TestClient(app).post(
        "/api/chat", json={"message": "Where is my order?"}, headers={"cookie": header}
    )
    [(caller, orders)] = captured.calls
    assert orders == "orders-endpoint"
    assert caller.shopper_id == ("S0188" if signed_in else None)
    assert caller.region == ("US" if signed_in else "EU")


def test_chat_services_always_sets_the_orders_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    monkeypatch.setenv("MCP_ORDERS_URL", "http://127.0.0.1:18434/mcp")
    chat_services.cache_clear()
    try:
        services = chat_services()
        assert services.orders is not None and services.orders.url == "http://127.0.0.1:18434/mcp"
    finally:
        chat_services.cache_clear()


# Security review pass 1, informational: with the flag off the route is indistinguishable from a
# missing one, even for a visitor with no session (no Set-Cookie either).
@pytest.mark.parametrize("method", ["post", "get"])
def test_with_the_flag_off_a_new_visitor_cannot_tell_the_route_exists(
    spy: Spy, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    monkeypatch.delenv(DEMO_SIGNIN_ENV, raising=False)
    client = TestClient(app)
    here = getattr(client, method)(PATH)
    missing = getattr(client, method)("/api/session/nobody")
    assert (here.status_code, here.json()) == (missing.status_code, missing.json())
    assert here.headers.get_list("set-cookie") == missing.headers.get_list("set-cookie") == []


def test_two_shopper_cookies_are_no_sign_in(spy: Spy, monkeypatch: pytest.MonkeyPatch) -> None:
    """Security review pass 1, L3: a planted second `nw_shopper` is refused, never picked."""
    monkeypatch.setenv(DEMO_SIGNIN_ENV, "1")
    good = sign_shopper(session_key(KEY), SESSION, "S0188")
    header = f"{session_cookie()}; {SHOPPER_COOKIE}={good}; {SHOPPER_COOKIE}={good}"
    TestClient(app).get("/api/cart", headers={"cookie": header})
    assert spy.callers[-1].shopper_id is None


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "TRACE"])
def test_n2_with_the_flag_off_no_method_tells_the_route_exists(
    spy: Spy, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """Scoped re-review N2: HEAD and OPTIONS too get the missing path's answer."""
    monkeypatch.delenv(DEMO_SIGNIN_ENV, raising=False)
    client = TestClient(app)
    here = client.request(method, PATH)
    missing = client.request(method, "/api/session/nobody")
    assert here.status_code == missing.status_code
    assert "allow" not in here.headers and here.headers.get_list("set-cookie") == []
