"""H10, bundled into row #27 (decision 0021 rule 6): another localhost app's cookie must not hide
the shopper's session.

`http.cookies.SimpleCookie` drops the whole jar on a valueless cookie (`flag`) or a name with a
space (`other app=1`), both of which browsers send for other apps on `localhost`. The session (and,
from row #27, the demo sign-in) then silently resets on every request. The gateway parses the header
leniently instead, and still accepts only a cookie value its own key signed."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from northwind_common.mcp_token import KEY_ENV
from northwind_gateway.app import app
from northwind_gateway.cart import cart_reader
from northwind_gateway.session import COOKIE, session_key, sign
from northwind_mcp_catalog import testing as catalog_testing

pytestmark = pytest.mark.req("CHK-01")

KEY = catalog_testing.KEY
SESSION = "b" * 32


class Spy:
    def __init__(self) -> None:
        self.sessions: list[str | None] = []

    async def read(self, caller: object) -> dict[str, object]:
        self.sessions.append(getattr(caller, "session", None))
        return {"region": "US", "lines": [], "item_count": 0, "subtotal": None, "currency": None}


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Iterator[Spy]:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    reader = Spy()
    app.dependency_overrides[cart_reader] = lambda: reader
    try:
        yield reader
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "template",
    [
        "other app=1; {cookie}",  # a name with a space
        "flag; {cookie}",  # a valueless cookie before ours
        "{cookie}; flag",  # and after it
        'a="x; {cookie}',  # an unclosed quote
        "theme=dark;{cookie}",  # no space after the semicolon
        "  {cookie}  ",
    ],
)
def test_another_apps_cookie_does_not_hide_the_session(spy: Spy, template: str) -> None:
    signed = f"{COOKIE}={sign(session_key(KEY), SESSION)}"
    response = TestClient(app).get("/api/cart", headers={"cookie": template.format(cookie=signed)})
    assert response.status_code == 200
    assert response.headers.get_list("set-cookie") == []  # the session was kept, not reissued
    assert spy.sessions[-1] == SESSION


@pytest.mark.parametrize(
    "value",
    [
        "a" * 32 + "." + "0" * 64,  # forged
        sign(session_key(b"x" * 40), "a" * 32),  # another key
        '"' + sign(session_key(KEY), "a" * 32) + '"',  # quoted: not the exact signed bytes
    ],
)
def test_lenient_parsing_still_takes_only_a_value_the_gateway_signed(spy: Spy, value: str) -> None:
    response = TestClient(app).get("/api/cart", headers={"cookie": f"flag; {COOKIE}={value}"})
    assert response.headers.get_list("set-cookie") != []  # a new session
    assert spy.sessions[-1] != "a" * 32


# Security review pass 1, L3: another localhost app can plant a second `nw_session` with a longer
# path, which browsers send first. Two values are refused: a new session, never either one.
def test_two_session_cookies_get_a_new_session_never_either_one(spy: Spy) -> None:
    first = sign(session_key(KEY), SESSION)
    second = sign(session_key(KEY), "c" * 32)
    header = f"{COOKIE}={first}; {COOKIE}={second}"
    response = TestClient(app).get("/api/cart", headers={"cookie": header})
    assert response.headers.get_list("set-cookie") != []
    assert spy.sessions[-1] not in (SESSION, "c" * 32)
