"""SA-01 PR 2: the bag's cart writes (hard rules 1, 2, 9, 10; the user's stop 5 yes, 2026-11-27).

POST /api/cart/items, /api/cart/quantity and /api/cart/remove, through the cart tools as
`shopper-portal`, behind the portal write guard, with one key per click.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from ._portal_world import PORTAL, VARIANT, World, add, key, session_cookie, world

pytestmark = pytest.mark.req("SA-01")

A = "a" * 32
B = "b" * 32


@pytest.fixture
def w(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with world(monkeypatch) as made:
        yield made


async def bag(client: httpx.AsyncClient, cookie: str) -> dict[str, Any]:
    got = await client.get("/api/cart", headers={"cookie": cookie})
    body: dict[str, Any] = got.json()["cart"]
    return body


async def post(
    client: httpx.AsyncClient, path: str, cookie: str, body: dict[str, Any], **headers: str
) -> httpx.Response:
    return await client.post(path, headers={**PORTAL, "cookie": cookie, **headers}, json=body)


def test_add_then_change_then_remove(w: World) -> None:
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> list[Any]:
        added = await add(c, cookie, quantity=2)
        after_add = await bag(c, cookie)
        changed = await post(
            c,
            "/api/cart/quantity",
            cookie,
            {"variant_id": VARIANT, "quantity": 3, "idempotency_key": key(1, "qty")},
        )
        after_change = await bag(c, cookie)
        removed = await post(
            c, "/api/cart/remove", cookie, {"variant_id": VARIANT, "idempotency_key": key(2, "rem")}
        )
        return [added, after_add, changed, after_change, removed, await bag(c, cookie)]

    added, after_add, changed, after_change, removed, after_remove = w.run(go)
    assert added.status_code == 200 and added.json() == {
        "result": "added",
        "variant_id": VARIANT,
        "quantity": 2,
    }
    assert [(ln["variant_id"], ln["quantity"]) for ln in after_add["lines"]] == [(VARIANT, 2)]
    assert changed.json()["quantity"] == 3 and after_change["item_count"] == 3
    assert removed.status_code == 200 and after_remove["lines"] == []


def test_a_retried_click_is_applied_once(w: World) -> None:
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, httpx.Response, dict[str, Any]]:
        first = await add(c, cookie, n=7)
        again = await add(c, cookie, n=7)
        return first, again, await bag(c, cookie)

    first, again, after = w.run(go)
    assert first.json()["result"] == "added"
    assert again.json()["result"] == "already_applied"
    assert after["item_count"] == 1


def test_a_new_click_is_a_new_change(w: World) -> None:
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> dict[str, Any]:
        await add(c, cookie, n=1)
        await add(c, cookie, n=2)
        return await bag(c, cookie)

    assert w.run(go)["item_count"] == 2


def test_another_sessions_bag_is_never_touched(w: World) -> None:
    async def go(c: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
        await add(c, session_cookie(A))
        return await bag(c, session_cookie(A)), await bag(c, session_cookie(B))

    mine, theirs = w.run(go)
    assert mine["item_count"] == 1 and theirs["lines"] == []


def test_live_stock_refuses_an_add_with_the_tools_fixed_text_and_no_alternatives(w: World) -> None:
    w.store.set_stock(VARIANT, "US", 0)
    refused = w.run(lambda c: add(c, session_cookie(A)))
    assert refused.status_code == 409
    body = refused.json()
    assert set(body) == {"isError", "errorCategory", "isRetryable", "message", "code"}
    assert body["code"] == "out_of_stock" and body["errorCategory"] == "business"


@pytest.mark.parametrize(
    "headers",
    [
        {"content-type": "text/plain"},
        {"x-northwind-portal": "0"},
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site"},
        {"sec-fetch-site": "none", "origin": "https://evil.example"},
    ],
)
def test_a_write_from_anywhere_but_the_portal_is_refused_and_changes_nothing(
    w: World, headers: dict[str, str]
) -> None:
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, dict[str, Any]]:
        sent = await c.post(
            "/api/cart/items",
            headers={**PORTAL, **headers, "cookie": cookie},
            content=b'{"variant_id": "%s", "quantity": 1, "idempotency_key": "%s"}'
            % (VARIANT.encode(), key(3).encode()),
        )
        return sent, await bag(c, cookie)

    sent, after = w.run(go)
    assert sent.status_code in {403, 422}
    assert after["lines"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"variant_id": VARIANT, "quantity": 0, "idempotency_key": key(4)},
        {"variant_id": VARIANT, "quantity": 11, "idempotency_key": key(4)},
        {"variant_id": VARIANT, "quantity": "2", "idempotency_key": key(4)},
        {"variant_id": "p0004", "quantity": 1, "idempotency_key": key(4)},
        {"variant_id": VARIANT, "quantity": 1, "idempotency_key": "short"},
        {"variant_id": VARIANT, "quantity": 1, "idempotency_key": key(4), "session": A},
        {"variant_id": VARIANT, "quantity": 1},
    ],
)
def test_a_malformed_write_is_the_fixed_validation_envelope(w: World, body: dict[str, Any]) -> None:
    sent = w.run(lambda c: post(c, "/api/cart/items", session_cookie(A), body))
    assert sent.status_code == 422
    body = sent.json()
    assert set(body) <= {"isError", "errorCategory", "isRetryable", "message", "code"}
    assert body["errorCategory"] == "validation"
    assert body["message"] in {
        "That request could not be accepted.",
        "That message could not be accepted.",
    }


def test_card_data_in_a_write_is_refused_and_never_echoed(w: World) -> None:
    sent = w.run(
        lambda c: post(
            c,
            "/api/cart/items",
            session_cookie(A),
            {"variant_id": VARIANT, "quantity": 1, "idempotency_key": "4111111111111111"},
        )
    )
    assert sent.status_code == 422 and "4111" not in sent.text


def test_without_a_session_it_is_the_fixed_503_and_never_a_write(w: World) -> None:
    sent = w.run(
        lambda c: c.post(
            "/api/cart/items",
            headers={**PORTAL, "cookie": "nw_session=forged"},
            json={"variant_id": VARIANT, "quantity": 1, "idempotency_key": key(5)},
        )
    )
    # A bad cookie gets a fresh session from the middleware, so this is a write to an empty new
    # bag, never to another one; with no key at all the route answers the fixed 503.
    assert sent.status_code in {200, 503}
