"""SA-01 PR 2, test-writer pass: boundaries and adversarial input on the bag's cart writes.

From the criteria: quantity 1-10, a key of 16-64 characters of [A-Za-z0-9_-], a repeated key gives
"already_applied" and one change, a refusal passes on the catalog tool's own category and code,
malformed input is a 422 validation envelope, card data is a 422 with no echo, and every write is
behind the portal guard (a 403 `permission` envelope). Hard rules 1, 2, 9, 10.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from ._portal_world import PORTAL, VARIANT, World, key, session_cookie, world

pytestmark = pytest.mark.req("SA-01")

A = "a" * 32
ITEMS = "/api/cart/items"
QUANTITY = "/api/cart/quantity"
REMOVE = "/api/cart/remove"


@pytest.fixture
def w(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with world(monkeypatch) as made:
        yield made


async def bag(client: httpx.AsyncClient, cookie: str) -> dict[str, Any]:
    got = await client.get("/api/cart", headers={"cookie": cookie})
    body: dict[str, Any] = got.json()["cart"]
    return body


def quantities(cart: dict[str, Any]) -> dict[str, int]:
    return {ln["variant_id"]: ln["quantity"] for ln in cart["lines"]}


async def post(
    client: httpx.AsyncClient,
    path: str,
    cookie: str,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return await client.post(path, headers={**(headers or PORTAL), "cookie": cookie}, json=body)


def item(quantity: Any, k: str) -> dict[str, Any]:
    return {"variant_id": VARIANT, "quantity": quantity, "idempotency_key": k}


# ---- quantity boundaries ------------------------------------------------------------------------


@pytest.mark.parametrize("quantity", [1, 10])
def test_the_quantity_limits_themselves_are_accepted(w: World, quantity: int) -> None:
    w.store.set_stock(VARIANT, "US", 50)
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, httpx.Response, dict[str, Any]]:
        added = await post(c, ITEMS, cookie, item(quantity, key(1, "lim")))
        set_to = await post(c, QUANTITY, cookie, item(quantity, key(2, "lim")))
        return added, set_to, await bag(c, cookie)

    added, set_to, after = w.run(go)
    assert added.status_code == 200 and set_to.status_code == 200
    assert quantities(after) == {VARIANT: quantity}


@pytest.mark.parametrize("quantity", [0, -1, 11, 100, 1.5, "3", None, True, [1]])
@pytest.mark.parametrize("path", [ITEMS, QUANTITY])
def test_a_quantity_outside_1_to_10_or_not_an_integer_is_a_422_and_changes_nothing(
    w: World, path: str, quantity: Any
) -> None:
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, dict[str, Any]]:
        sent = await post(c, path, cookie, item(quantity, key(3, "bad")))
        return sent, await bag(c, cookie)

    sent, after = w.run(go)
    assert sent.status_code == 422 and sent.json()["errorCategory"] == "validation"
    assert after["lines"] == []


def test_an_omitted_quantity_on_add_defaults_to_one(w: World) -> None:
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, dict[str, Any]]:
        sent = await post(
            c, ITEMS, cookie, {"variant_id": VARIANT, "idempotency_key": key(4, "dflt")}
        )
        return sent, await bag(c, cookie)

    sent, after = w.run(go)
    assert sent.status_code == 200 and sent.json()["quantity"] == 1
    assert quantities(after) == {VARIANT: 1}


# ---- key boundaries -----------------------------------------------------------------------------


@pytest.mark.parametrize("length", [16, 64])
def test_a_key_of_16_or_64_characters_is_accepted(w: World, length: int) -> None:
    k = ("Ab-_" * 20)[:length]
    sent = w.run(lambda c: post(c, ITEMS, session_cookie(A), item(1, k)))
    assert sent.status_code == 200 and sent.json()["result"] == "added"


@pytest.mark.parametrize(
    "bad",
    [
        "a" * 15,
        "a" * 65,
        "",
        "a" * 15 + "!",
        "a" * 15 + " ",
        "a" * 15 + "\n",
        "a" * 15 + "é",
        "a" * 15 + "٣",  # an Arabic-Indic digit is not [0-9]
        "a" * 8 + "/" + "a" * 8,
    ],
)
@pytest.mark.parametrize("path", [ITEMS, QUANTITY, REMOVE])
def test_a_key_that_breaks_the_rule_is_a_422_and_changes_nothing(
    w: World, path: str, bad: str
) -> None:
    cookie = session_cookie(A)
    body: dict[str, Any] = {"variant_id": VARIANT, "idempotency_key": bad}
    if path != REMOVE:
        body["quantity"] = 1

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, dict[str, Any]]:
        sent = await post(c, path, cookie, body)
        return sent, await bag(c, cookie)

    sent, after = w.run(go)
    assert sent.status_code == 422 and sent.json()["errorCategory"] == "validation"
    assert after["lines"] == []


# ---- idempotency on all three writes ------------------------------------------------------------


def test_a_repeated_quantity_key_is_applied_once(w: World) -> None:
    w.store.set_stock(VARIANT, "US", 50)
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> list[Any]:
        await post(c, ITEMS, cookie, item(1, key(1, "i")))
        first = await post(c, QUANTITY, cookie, item(3, key(2, "q")))
        await post(c, QUANTITY, cookie, item(5, key(3, "q")))
        replay = await post(c, QUANTITY, cookie, item(3, key(2, "q")))  # the first click again
        return [first, replay, await bag(c, cookie)]

    first, replay, after = w.run(go)
    assert first.json()["result"] != "already_applied"
    assert replay.status_code == 200 and replay.json()["result"] == "already_applied"
    assert quantities(after) == {VARIANT: 5}  # the replay did not set it back to 3


def test_a_repeated_remove_key_is_applied_once(w: World) -> None:
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> list[Any]:
        await post(c, ITEMS, cookie, item(1, key(1, "i")))
        first = await post(
            c, REMOVE, cookie, {"variant_id": VARIANT, "idempotency_key": key(2, "r")}
        )
        await post(c, ITEMS, cookie, item(2, key(3, "i")))
        replay = await post(
            c, REMOVE, cookie, {"variant_id": VARIANT, "idempotency_key": key(2, "r")}
        )
        return [first, replay, await bag(c, cookie)]

    first, replay, after = w.run(go)
    assert first.json()["result"] != "already_applied"
    assert replay.json()["result"] == "already_applied"
    assert quantities(after) == {VARIANT: 2}  # the replay did not remove the new line


def test_ten_simultaneous_clicks_with_one_key_make_one_change(w: World) -> None:
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> tuple[list[httpx.Response], dict[str, Any]]:
        sent = await asyncio.gather(
            *(post(c, ITEMS, cookie, item(1, key(9, "race"))) for _ in range(10))
        )
        return list(sent), await bag(c, cookie)

    sent, after = w.run(go)
    assert all(r.status_code == 200 for r in sent)
    assert sorted(r.json()["result"] for r in sent).count("already_applied") == 9
    assert quantities(after) == {VARIANT: 1}


# ---- live stock and the tool's own refusals -----------------------------------------------------


def test_setting_a_quantity_above_live_stock_is_the_tools_refusal_and_keeps_the_bag(
    w: World,
) -> None:
    w.store.set_stock(VARIANT, "US", 2)
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, dict[str, Any]]:
        await post(c, ITEMS, cookie, item(1, key(1, "i")))
        sent = await post(c, QUANTITY, cookie, item(5, key(2, "q")))
        return sent, await bag(c, cookie)

    sent, after = w.run(go)
    assert sent.status_code == 409 and sent.json()["code"] in {"out_of_stock", "insufficient_stock"}
    assert sent.json()["isError"] is True and sent.json()["errorCategory"] == "business"
    assert quantities(after) == {VARIANT: 1}


@pytest.mark.parametrize("path", [QUANTITY, REMOVE])
def test_changing_a_line_that_is_not_in_the_bag_is_a_structured_refusal(
    w: World, path: str
) -> None:
    cookie = session_cookie(A)
    body: dict[str, Any] = {"variant_id": VARIANT, "idempotency_key": key(5, "ghost")}
    if path == QUANTITY:
        body["quantity"] = 4

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, dict[str, Any]]:
        sent = await post(c, path, cookie, body)
        return sent, await bag(c, cookie)

    sent, after = w.run(go)
    assert 400 <= sent.status_code < 500
    assert sent.json()["isError"] is True and "errorCategory" in sent.json()
    assert after["lines"] == []


def test_an_unknown_variant_is_a_structured_refusal_never_a_server_error(w: World) -> None:
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, dict[str, Any]]:
        sent = await post(
            c, ITEMS, cookie, {**item(1, key(6, "nope")), "variant_id": "P9999-XXX-REG-M"}
        )
        return sent, await bag(c, cookie)

    sent, after = w.run(go)
    assert 400 <= sent.status_code < 500
    assert sent.json()["isError"] is True
    assert after["lines"] == []


# ---- hard rule 1: card data in every write ------------------------------------------------------


@pytest.mark.parametrize("path", [ITEMS, QUANTITY, REMOVE])
@pytest.mark.parametrize("field", ["idempotency_key", "variant_id"])
@pytest.mark.parametrize("pan", ["4111111111111111", "4111 1111 1111 1111", "4111-1111-1111-1111"])
def test_card_data_in_any_write_field_is_a_422_with_no_echo(
    w: World, path: str, field: str, pan: str
) -> None:
    body: dict[str, Any] = {"variant_id": VARIANT, "idempotency_key": key(7, "pan")}
    if path != REMOVE:
        body["quantity"] = 1
    body[field] = pan
    sent = w.run(lambda c: post(c, path, session_cookie(A), body))
    assert sent.status_code == 422
    assert "4111" not in sent.text and "1111" not in sent.text


def test_card_data_in_an_unknown_extra_field_is_refused_with_no_echo(w: World) -> None:
    body = {**item(1, key(8, "pan")), "note": "my card is 4111 1111 1111 1111"}
    sent = w.run(lambda c: post(c, ITEMS, session_cookie(A), body))
    assert sent.status_code == 422 and "4111" not in sent.text


# ---- the write guard on every cart write --------------------------------------------------------

GUARD_REFUSED = [
    {**PORTAL, "x-northwind-portal": "0"},
    {k: v for k, v in PORTAL.items() if k != "x-northwind-portal"},
    {**PORTAL, "sec-fetch-site": "cross-site"},
    {**PORTAL, "sec-fetch-site": "same-site"},
    {**PORTAL, "sec-fetch-site": "none"},
    {k: v for k, v in PORTAL.items() if k != "sec-fetch-site"} | {"origin": "https://evil.example"},
    {k: v for k, v in PORTAL.items() if k != "sec-fetch-site"} | {"origin": "null"},
    {k: v for k, v in PORTAL.items() if k != "sec-fetch-site"} | {"origin": "http://testserver:81"},
    {**PORTAL, "sec-fetch-site": "none", "origin": "http://testserver"},
]


@pytest.mark.parametrize("headers", GUARD_REFUSED)
@pytest.mark.parametrize("path", [ITEMS, QUANTITY, REMOVE])
def test_every_cart_write_is_behind_the_guard_with_a_403_permission_envelope(
    w: World, path: str, headers: dict[str, str]
) -> None:
    cookie = session_cookie(A)
    body: dict[str, Any] = {"variant_id": VARIANT, "idempotency_key": key(10, "grd")}
    if path != REMOVE:
        body["quantity"] = 2

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, dict[str, Any]]:
        sent = await post(c, path, cookie, body, headers)
        return sent, await bag(c, cookie)

    sent, after = w.run(go)
    assert sent.status_code == 403
    assert sent.json()["errorCategory"] == "permission" and sent.json()["isError"] is True
    assert after["lines"] == []


@pytest.mark.parametrize(
    "extra",
    [
        {"content-type": "application/json; charset=utf-8"},
        {"content-type": "Application/JSON"},
        {"origin": "http://testserver"},  # the request's own host, with a same-origin fetch site
        {"origin": "https://evil.example"},  # same-origin fetch metadata wins; the browser sets it
    ],
)
def test_a_genuine_portal_write_passes_the_guard(w: World, extra: dict[str, str]) -> None:
    sent = w.run(
        lambda c: post(c, ITEMS, session_cookie(A), item(1, key(11, "ok")), {**PORTAL, **extra})
    )
    assert sent.status_code == 200 and sent.json()["result"] == "added"


def test_an_own_origin_with_no_fetch_metadata_passes_the_guard(w: World) -> None:
    headers = {k: v for k, v in PORTAL.items() if k != "sec-fetch-site"} | {
        "origin": "http://testserver"
    }
    sent = w.run(lambda c: post(c, ITEMS, session_cookie(A), item(1, key(12, "ok")), headers))
    assert sent.status_code == 200


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH"])
@pytest.mark.parametrize("path", [ITEMS, QUANTITY, REMOVE])
def test_a_cart_write_route_answers_nothing_to_another_method(
    w: World, path: str, method: str
) -> None:
    sent = w.run(lambda c: c.request(method, path, headers={**PORTAL, "cookie": session_cookie(A)}))
    assert sent.status_code in {404, 405}
