"""POST /api/handoff (row #23, SA-06; hard rules 1, 9, 10): identity from the server only, card data
refused before anything, fixed text on every failure, the reference at once."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_assistant.caller import Caller
from northwind_backend.errors import Ok, transient
from northwind_backend.handoff import HandoffReceipt, HandoffRequest
from northwind_common.mcp_token import KEY_ENV
from northwind_gateway.app import app
from northwind_gateway.chat import CARD_MESSAGE, INVALID_MESSAGE
from northwind_gateway.handoff import (
    NOTICE,
    UNAVAILABLE,
    cart_reader,
    handoff_desk,
    session_ref,
)
from northwind_gateway.session import COOKIE
from northwind_gateway.settings import shopper_caller
from northwind_mcp_catalog import testing as catalog_testing

pytestmark = pytest.mark.req("SA-06")

KEY = catalog_testing.KEY
SESSION = "0123456789abcdef0123456789abcdef"
TEST_PAN = "4111 1111 1111 1111"  # the public sandbox test number
CART_VIEW = {
    "region": "US",
    "lines": [{"variant_id": "P0102-HGY-REG-M", "quantity": 2, "line_total": "179.98"}],
    "item_count": 2,
    "subtotal": "179.98",
    "currency": "USD",
}


class SpyDesk:
    def __init__(self, result: Any = None) -> None:
        self.requests: list[HandoffRequest] = []
        self.result = result

    async def request(self, request: HandoffRequest, *, dry_run: bool = False) -> Any:
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result or Ok(HandoffReceipt("H000007"))


class SpyCart:
    def __init__(self, result: Any = None) -> None:
        self.result = result

    async def read(self, caller: Caller) -> Any:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    yield TestClient(app)
    app.dependency_overrides.clear()


def use(desk: SpyDesk, caller: Caller | None = None, cart: SpyCart | None = None) -> None:
    app.dependency_overrides[handoff_desk] = lambda: desk
    app.dependency_overrides[cart_reader] = lambda: cart or SpyCart()
    if caller is not None:
        app.dependency_overrides[shopper_caller] = lambda: caller


def turns(*texts: str) -> list[dict[str, str]]:
    roles = ("shopper", "assistant")
    return [{"role": roles[i % 2], "text": t} for i, t in enumerate(texts)]


def test_an_anonymous_handoff_gets_a_reference_and_the_notice_at_once(client: TestClient) -> None:
    desk = SpyDesk()
    use(desk, Caller(region="EU", session=SESSION), SpyCart(CART_VIEW))
    response = client.post("/api/handoff", json={"turns": turns("a person please", "I'm an AI.")})
    assert response.status_code == 202
    assert response.json() == {"status": "queued", "reference": "H000007", "notice": NOTICE}
    assert response.headers["cache-control"] == "no-store"
    [request] = desk.requests
    assert request.identity.kind == "session" and request.identity.region == "EU"
    assert request.identity.ref == session_ref(KEY, SESSION)
    assert SESSION not in repr(request)
    assert request.cart is not None and request.cart.subtotal_minor == 17998
    assert request.trigger == "button"


def test_a_signed_in_shopper_is_named_by_their_id(client: TestClient) -> None:
    desk = SpyDesk()
    use(desk, Caller(region="UK", session=SESSION, shopper_id="S0103"))
    body = {"turns": turns("where is O000001? talk to a human"), "trigger": "message"}
    assert client.post("/api/handoff", json=body).status_code == 202
    [request] = desk.requests
    assert (request.identity.kind, request.identity.ref, request.identity.region) == (
        "shopper",
        "S0103",
        "UK",
    )
    assert request.trigger == "message"


@pytest.mark.parametrize(
    "texts",
    [
        (f"my card is {TEST_PAN}, get me a person",),
        ("my card is 4111 1111", "ok", "1111 1111 please, a human"),
    ],
)
def test_card_data_in_a_turn_or_across_turns_is_refused_before_the_desk(
    client: TestClient, texts: tuple[str, ...]
) -> None:
    desk = SpyDesk()
    use(desk, Caller(region="US", session=SESSION))
    response = client.post("/api/handoff", json={"turns": turns(*texts)})
    assert response.status_code == 422
    assert response.json()["message"] == CARD_MESSAGE
    assert "4111" not in response.text and not desk.requests


@pytest.mark.parametrize(
    "body",
    [
        {"turns": turns(*["hi"] * 11)},
        {"turns": [{"role": "shopper", "text": "x" * 2001}]},
        {"turns": [{"role": "system", "text": "a person"}]},
        {"turns": [], "session": SESSION},
        {"turns": [], "trigger": "model"},
    ],
)
def test_a_malformed_body_gets_chats_fixed_refusal(client: TestClient, body: Any) -> None:
    desk = SpyDesk()
    use(desk, Caller(region="US", session=SESSION))
    response = client.post("/api/handoff", json=body)
    assert response.status_code == 422 and response.json()["message"] == INVALID_MESSAGE
    assert not desk.requests


def test_no_session_fails_closed(client: TestClient) -> None:
    desk = SpyDesk()
    use(desk, Caller(region="US"))
    response = client.post("/api/handoff", json={"turns": []})
    assert response.status_code == 503
    assert response.json() == {
        "isError": True,
        "errorCategory": "transient",
        "isRetryable": True,
        "message": UNAVAILABLE,
    }
    assert not desk.requests


@pytest.mark.parametrize("result", [transient("service_unavailable", "x"), RuntimeError("secret")])
def test_a_desk_failure_is_one_fixed_envelope(client: TestClient, result: Any) -> None:
    use(SpyDesk(result), Caller(region="US", session=SESSION))
    response = client.post("/api/handoff", json={"turns": turns("a person please")})
    assert response.status_code == 503 and response.json()["message"] == UNAVAILABLE
    assert "secret" not in response.text


def test_a_failed_cart_read_is_no_amounts_not_a_failed_handoff(client: TestClient) -> None:
    desk = SpyDesk()
    use(desk, Caller(region="US", session=SESSION), SpyCart(RuntimeError("down")))
    assert client.post("/api/handoff", json={"turns": []}).status_code == 202
    assert desk.requests[0].cart is None


def test_the_response_never_echoes_the_transcript(client: TestClient) -> None:
    use(SpyDesk(), Caller(region="US", session=SESSION))
    response = client.post("/api/handoff", json={"turns": turns("marker-xyzzy, a human please")})
    assert "xyzzy" not in response.text


def test_the_session_reference_is_stable_letters_only_and_not_the_id() -> None:
    ref = session_ref(KEY, SESSION)
    assert re.fullmatch(r"[a-p]{32}", ref)
    assert ref == session_ref(KEY, SESSION) != session_ref(KEY, "f" * 32)
    assert ref != session_ref(b"another-key-another-key-another!", SESSION)


def test_the_handoff_path_gets_a_session_from_the_middleware(client: TestClient) -> None:
    desk = SpyDesk()
    app.dependency_overrides[handoff_desk] = lambda: desk
    app.dependency_overrides[cart_reader] = lambda: SpyCart()
    response = client.post("/api/handoff", json={"turns": []})
    assert response.status_code == 202, response.text
    assert response.headers["set-cookie"].startswith(f"{COOKIE}=")
    assert desk.requests[0].identity.kind == "session"


# ---- security review M1 and L6 ------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["text/plain", "application/x-www-form-urlencoded", None])
def test_only_a_json_request_is_taken(client: TestClient, kind: str | None) -> None:
    """A cross-site page can send a text or form body without a preflight, never a JSON one."""
    desk = SpyDesk()
    use(desk, Caller(region="US", session=SESSION))
    headers = {} if kind is None else {"content-type": kind}
    response = client.post("/api/handoff", content=b'{"turns": []}', headers=headers)
    assert response.status_code == 422 and response.json()["message"] == INVALID_MESSAGE
    assert not desk.requests


def test_a_malformed_identity_is_the_fixed_422_not_a_500(client: TestClient) -> None:
    desk = SpyDesk()
    use(desk, Caller(region="US", session=SESSION, shopper_id="not-a-shopper"))
    response = client.post("/api/handoff", json={"turns": []})
    assert response.status_code == 422 and response.json()["message"] == INVALID_MESSAGE
    assert not desk.requests


def test_the_desk_has_one_agent_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[object] = []

    def fake_runtime() -> object:
        made.append(object())
        return made[-1]

    monkeypatch.setattr("northwind_gateway.handoff.agent_runtime", fake_runtime)
    handoff_desk.cache_clear()
    try:
        desk = handoff_desk()
        assert desk._runtime() is desk._runtime() and len(made) == 1
    finally:
        handoff_desk.cache_clear()
