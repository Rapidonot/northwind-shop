"""SA-01 PR 2, test-writer pass: CHK-02's confirmation control, edges and hard-rule failures.

"Order cannot be placed in any test without the confirmation event": every order the desk holds
must have come through a Pay press that was well formed, signed in, matched the live summary and
inside the limit. Also: failures hold the order and release stock (CHK-05), access failures are
503 and never a valid empty result (hard rule 10), live prices only (hard rule 2), no card data
(hard rule 1).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from northwind_assistant.catalog_client import CartReader
from northwind_backend.fakes.payment import (
    SANDBOX_APPROVED,
    SANDBOX_HARD_DECLINE,
    SANDBOX_SOFT_DECLINE,
)
from northwind_backend.inventory import HoldStatus
from northwind_backend.ports import OrderStatus
from northwind_gateway import checkout as checkout_module
from northwind_gateway.app import app
from northwind_gateway.cart import cart_reader
from northwind_gateway.cart_writes import CartWriter, cart_writer
from northwind_gateway.checkout import checkout_desk

from ._portal_world import (
    KEY,
    OTHER_SHOPPER,
    PORTAL,
    VARIANT,
    World,
    add,
    key,
    pay,
    session_cookie,
    signed_in,
    summary,
    world,
)

pytestmark = pytest.mark.req("SA-01")

A = "a" * 32
B = "b" * 32
DEAD = "http://127.0.0.1:9/mcp"  # nothing listens here: a failed access, not an empty answer


@pytest.fixture
def w(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with world(monkeypatch) as made:
        yield made


def book_size() -> int:
    return len(checkout_desk().payment.confirmations._issued)


def effects(kind: str) -> int:
    return sum(1 for e in checkout_desk().payment.effects if e.operation == kind)


def holding() -> list[Any]:
    live = {HoldStatus.HELD, HoldStatus.COMMITTED}
    return [r for r in checkout_desk().inventory.records.values() if r.status in live]


def nothing_was_placed() -> None:
    """CHK-02: no order, no confirmation, no money, no reservation."""
    desk = checkout_desk()
    assert desk.orders.orders == {}
    assert book_size() == 0
    assert effects("authorize") == 0 and effects("capture") == 0
    assert desk.inventory.records == {}


async def shown(c: httpx.AsyncClient, cookie: str, quantity: int = 1) -> dict[str, Any]:
    assert (await add(c, cookie, quantity=quantity, n=quantity)).status_code == 200
    got = await summary(c, cookie)
    assert got.status_code == 200, got.text
    body: dict[str, Any] = got.json()
    return body


async def raw_pay(c: httpx.AsyncClient, cookie: str, body: Any, **headers: str) -> httpx.Response:
    return await c.post(
        "/api/checkout/pay", headers={**PORTAL, **headers, "cookie": cookie}, json=body
    )


# ---- the summary --------------------------------------------------------------------------------


def test_an_empty_bag_summary_is_a_real_answer_that_cannot_be_paid(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, httpx.Response]:
        got = await summary(c, cookie)
        return got, await pay(c, cookie, got.json()["digest"])

    got, paid = w.run(go)
    assert got.status_code == 200 and got.json()["lines"] == [] and "empty" in got.json()["issues"]
    assert paid.status_code == 409 and paid.json()["code"] == "empty"
    nothing_was_placed()


def test_a_signed_out_summary_says_so_and_still_shows_the_live_total(w: World) -> None:
    body = w.run(lambda c: shown(c, session_cookie(A)))
    assert body["signed_in"] is False and body["total"]["amount"] == "160.00"


def test_the_digest_is_stable_between_reads_and_follows_the_bag_and_the_price(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> list[str]:
        first = await shown(c, cookie)
        again = (await summary(c, cookie)).json()
        await add(c, cookie, n=50)  # quantity 2
        more = (await summary(c, cookie)).json()
        w.store.set_price(VARIANT, "US", "150.00", "USD")
        cheaper = (await summary(c, cookie)).json()
        return [first["digest"], again["digest"], more["digest"], cheaper["digest"]]

    first, again, more, cheaper = w.run(go)
    assert first == again
    assert len({first, more, cheaper}) == 3
    assert all(len(d) == 32 and set(d) <= set("0123456789abcdef") for d in (first, more, cheaper))


def test_the_summary_never_carries_stock_counts_or_card_or_token_fields(w: World) -> None:
    text = w.run(lambda c: shown(c, signed_in(A)))
    flat = repr(text).lower()
    for banned in ("on_hand", "stock", "tok_", "payment_token", "card", "cvv", "pan"):
        assert banned not in flat.replace("return_policy", ""), banned


def test_the_summary_and_pay_are_never_cached(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> list[httpx.Response]:
        got = await c.get("/api/checkout/summary", headers={"cookie": cookie})
        await add(c, cookie)
        got = await c.get("/api/checkout/summary", headers={"cookie": cookie})
        return [got, await pay(c, cookie, got.json()["digest"])]

    for response in w.run(go):
        assert response.headers.get("cache-control") == "no-store"


def test_an_unreadable_bag_is_a_503_never_an_empty_summary_and_never_an_order(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> list[httpx.Response]:
        got = await shown(c, cookie)
        app.dependency_overrides[cart_reader] = lambda: CartReader(DEAD, KEY)
        return [await summary(c, cookie), await pay(c, cookie, got["digest"])]

    read, paid = w.run(go)
    for response in (read, paid):
        assert response.status_code == 503
        assert response.json()["errorCategory"] == "transient"
        assert response.json()["isRetryable"] is True
        assert "lines" not in response.json() and "total" not in response.json()
    nothing_was_placed()


# ---- the confirmation event: CHK-02's criterion -------------------------------------------------


@pytest.mark.req("CHK-02")
def test_nothing_but_a_pay_press_ever_creates_an_order_or_a_confirmation(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> None:
        body = await shown(c, cookie, quantity=2)
        await c.get("/api/orders", headers={"cookie": cookie})
        await c.get("/api/cart", headers={"cookie": cookie})
        await c.post(
            "/api/cart/quantity",
            headers={**PORTAL, "cookie": cookie},
            json={"variant_id": VARIANT, "quantity": 3, "idempotency_key": key(1, "q")},
        )
        # every way of pressing Pay that is not a genuine, matching, portal press
        await c.get("/api/checkout/pay", headers={"cookie": cookie})
        await c.post("/api/checkout/pay", headers={"cookie": cookie}, json={})
        await c.post(
            "/api/checkout/pay",
            headers={**PORTAL, "sec-fetch-site": "cross-site", "cookie": cookie},
            json={"digest": body["digest"], "idempotency_key": key(2, "p")},
        )
        await raw_pay(c, cookie, {"digest": body["digest"], "idempotency_key": key(2, "p")})
        # (the bag is now 3 and the digest is stale: summary_changed)

    w.run(go)
    nothing_was_placed()


@pytest.mark.req("CHK-02")
def test_every_order_the_desk_holds_has_exactly_one_confirmation_bound_to_it(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> None:
        for n in (1, 2, 3):
            body = await shown(c, cookie, quantity=n)
            assert (await pay(c, cookie, body["digest"], n=n)).status_code == 200

    w.run(go)
    desk = checkout_desk()
    assert sorted(desk.orders.orders) == ["O900001", "O900002", "O900003"]
    assert book_size() == len(desk.orders.orders)
    assert effects("authorize") == effects("capture") == 3


@pytest.mark.req("CHK-02")
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"digest": "d" * 32},
        {"idempotency_key": "k" * 20},
        {"digest": "D" * 32, "idempotency_key": "k" * 20},  # upper-case hex
        {"digest": "d" * 31, "idempotency_key": "k" * 20},
        {"digest": "d" * 33, "idempotency_key": "k" * 20},
        {"digest": "g" * 32, "idempotency_key": "k" * 20},
        {"digest": 12345678901234567890123456789012, "idempotency_key": "k" * 20},
        {"digest": None, "idempotency_key": "k" * 20},
        {"digest": "d" * 32, "idempotency_key": "k" * 15},
        {"digest": "d" * 32, "idempotency_key": "k" * 65},
        {"digest": "d" * 32, "idempotency_key": "k" * 15 + "!"},
        {"digest": "d" * 32, "idempotency_key": "k" * 20, "confirmed": True},
        {"digest": "d" * 32, "idempotency_key": "k" * 20, "confirmation": "ref-1"},
        {"digest": "d" * 32, "idempotency_key": "k" * 20, "shopper_id": "S0001"},
    ],
)
def test_a_malformed_pay_is_a_422_and_nothing_is_confirmed(w: World, body: dict[str, Any]) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        await shown(c, cookie)
        return await raw_pay(c, cookie, body)

    refused = w.run(go)
    assert refused.status_code == 422 and refused.json()["errorCategory"] == "validation"
    nothing_was_placed()


@pytest.mark.req("CHK-02")
def test_a_non_object_or_unparseable_pay_body_is_a_422_and_nothing_is_confirmed(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> list[httpx.Response]:
        await shown(c, cookie)
        out = []
        for raw in (b"", b"null", b"[]", b'"pay"', b"{not json", b"true"):
            out.append(
                await c.post("/api/checkout/pay", headers={**PORTAL, "cookie": cookie}, content=raw)
            )
        return out

    assert {r.status_code for r in w.run(go)} == {422}
    nothing_was_placed()


@pytest.mark.req("CHK-02")
def test_a_forged_shopper_cookie_cannot_pay(w: World) -> None:
    good = signed_in(A)
    forged = f"{session_cookie(A)}; nw_shopper=S0001.forged"

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        body = await shown(c, good)
        return await pay(c, forged, body["digest"])

    refused = w.run(go)
    assert refused.status_code == 401 and refused.json()["errorCategory"] == "permission"
    nothing_was_placed()


@pytest.mark.req("CHK-02")
def test_another_shoppers_summary_digest_cannot_pay_my_bag_or_theirs(w: World) -> None:
    mine, theirs = signed_in(A), signed_in(B, OTHER_SHOPPER)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        body = await shown(c, mine)
        return await pay(c, theirs, body["digest"])  # their bag is empty

    refused = w.run(go)
    assert refused.status_code == 409 and refused.json()["code"] in {"empty", "summary_changed"}
    nothing_was_placed()


@pytest.mark.req("CHK-02")
def test_a_quantity_change_after_the_summary_voids_the_press(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        body = await shown(c, cookie)
        await c.post(
            "/api/cart/quantity",
            headers={**PORTAL, "cookie": cookie},
            json={"variant_id": VARIANT, "quantity": 5, "idempotency_key": key(1, "q")},
        )
        return await pay(c, cookie, body["digest"])

    refused = w.run(go)
    assert refused.status_code == 409 and refused.json()["code"] == "summary_changed"
    nothing_was_placed()


@pytest.mark.req("CHK-02")
def test_stock_gone_after_the_summary_is_refused_and_nothing_is_confirmed(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        body = await shown(c, cookie)
        w.store.set_stock(VARIANT, "US", 0)
        return await pay(c, cookie, body["digest"])

    refused = w.run(go)
    assert refused.status_code == 409
    assert refused.json()["code"] in {"summary_changed", "not_ready", "no_longer_available"}
    assert effects("authorize") == 0 and effects("capture") == 0 and holding() == []


# ---- the order limit's boundary -----------------------------------------------------------------


@pytest.mark.parametrize(("limit", "allowed"), [(16_000, True), (15_999, False)])
@pytest.mark.req("CHK-05")
def test_an_order_exactly_at_the_limit_is_placed_and_one_minor_unit_over_is_refused(
    w: World, monkeypatch: pytest.MonkeyPatch, limit: int, allowed: bool
) -> None:
    monkeypatch.setattr(checkout_module, "DEFAULT_ORDER_LIMITS", {"USD": limit})
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        body = await shown(c, cookie)  # total 160.00 = 16,000 minor units
        return await pay(c, cookie, body["digest"])

    paid = w.run(go)
    if allowed:
        assert paid.status_code == 200 and paid.json()["result"] == "placed"
    else:
        assert paid.status_code == 409 and paid.json()["code"] == "over_order_limit"
        nothing_was_placed()


# ---- failures hold the order and release stock --------------------------------------------------


@pytest.mark.req("CHK-05")
@pytest.mark.parametrize("token", [SANDBOX_HARD_DECLINE, SANDBOX_SOFT_DECLINE])
def test_a_declined_payment_is_on_record_moves_no_money_and_keeps_no_stock(
    w: World, monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    monkeypatch.setattr(checkout_module, "SANDBOX_APPROVED", token)
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> list[httpx.Response]:
        body = await shown(c, cookie)
        paid = await pay(c, cookie, body["digest"], n=1)
        replay = await pay(c, cookie, body["digest"], n=1)
        orders = await c.get("/api/orders", headers={"cookie": cookie})
        bag = await c.get("/api/cart", headers={"cookie": cookie})
        return [paid, replay, orders, bag]

    paid, replay, orders, bag = w.run(go)
    assert paid.status_code == 409 and paid.json()["isError"] is True
    assert paid.json()["code"] in {"payment_declined", "on_hold"}
    assert replay.json() == paid.json()  # the same key replays the same refusal
    assert effects("capture") == 0
    assert holding() == []  # nothing stays held
    assert all(
        r.status is not HoldStatus.COMMITTED for r in checkout_desk().inventory.records.values()
    )
    [order] = checkout_desk().orders.orders.values()
    assert order.status is not OrderStatus.CONFIRMED
    assert orders.json()["orders"][0]["status"] != "confirmed"
    assert bag.json()["cart"]["lines"]  # the shopper's bag is kept for another try


@pytest.mark.req("CHK-05")
def test_a_declined_press_then_a_fresh_press_with_a_good_method_places_one_order(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> list[httpx.Response]:
        body = await shown(c, cookie)
        monkeypatch.setattr(checkout_module, "SANDBOX_APPROVED", SANDBOX_HARD_DECLINE)
        declined = await pay(c, cookie, body["digest"], n=1)
        monkeypatch.setattr(checkout_module, "SANDBOX_APPROVED", SANDBOX_APPROVED)
        placed = await pay(c, cookie, body["digest"], n=2)
        return [declined, placed]

    declined, placed = w.run(go)
    assert declined.status_code == 409
    assert placed.status_code == 200  # a fresh key is a fresh press; the declined one was refused
    confirmed = [
        o for o in checkout_desk().orders.orders.values() if o.status is OrderStatus.CONFIRMED
    ]
    assert len(confirmed) == 1 and effects("capture") == len(confirmed)
    assert len(holding()) == len(confirmed)


# ---- more than two racers -----------------------------------------------------------------------


@pytest.mark.req("CHK-05")
def test_eight_presses_with_one_key_make_one_order_one_authorize_one_capture(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> list[httpx.Response]:
        body = await shown(c, cookie)
        return list(await asyncio.gather(*(pay(c, cookie, body["digest"], n=1) for _ in range(8))))

    results = w.run(go)
    assert {r.status_code for r in results} == {200}
    assert len({r.json()["order_id"] for r in results}) == 1
    assert len(checkout_desk().orders.orders) == 1
    assert effects("authorize") == 1 and effects("capture") == 1
    assert len(checkout_desk().inventory.records) == 1 and book_size() == 1


@pytest.mark.req("CHK-05")
def test_eight_different_keys_racing_make_exactly_one_order(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> list[httpx.Response]:
        body = await shown(c, cookie)
        return list(
            await asyncio.gather(*(pay(c, cookie, body["digest"], n=n) for n in range(1, 9)))
        )

    results = w.run(go)
    assert sorted(r.status_code for r in results) == [200] + [409] * 7
    assert {r.json()["code"] for r in results if r.status_code == 409} <= {
        "empty",
        "summary_changed",
    }
    assert len(checkout_desk().orders.orders) == 1 and effects("capture") == 1
    assert len(checkout_desk().inventory.records) == 1


@pytest.mark.req("CHK-05")
def test_a_bag_that_could_not_be_emptied_still_refuses_a_second_key(w: World) -> None:
    """The paid order's bag-clearing write fails; a second key must not buy the same bag again."""
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> list[httpx.Response]:
        body = await shown(c, cookie)
        app.dependency_overrides[cart_writer] = lambda: CartWriter(DEAD, KEY)
        first = await pay(c, cookie, body["digest"], n=1)
        second = await pay(c, cookie, body["digest"], n=2)
        return [first, second]

    first, second = w.run(go)
    assert first.status_code == 200 and first.json()["bag_emptied"] is False
    assert second.status_code == 409, "a second key on a paid, unemptied bag placed another order"
    assert len(checkout_desk().orders.orders) == 1 and effects("capture") == 1


# ---- hard rule 2 and the response -----------------------------------------------------------


def test_the_amount_paid_is_the_live_price_at_the_press_and_matches_the_summary(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> tuple[dict[str, Any], httpx.Response]:
        w.store.set_price(VARIANT, "US", "123.45", "USD")
        body = await shown(c, cookie, quantity=3)
        return body, await pay(c, cookie, body["digest"])

    body, paid = w.run(go)
    assert body["total"] == {"amount": "370.35", "currency": "USD"}
    assert paid.json()["total"] == body["total"]
    [intent] = checkout_desk().payment.intents.values()
    assert intent.authorized_minor == 37_035 and intent.captured_minor == 37_035


def test_two_orders_in_a_row_are_numbered_apart_and_listed_newest_first(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        for n in (1, 2):
            body = await shown(c, cookie, quantity=n)
            assert (await pay(c, cookie, body["digest"], n=n)).status_code == 200
        return await c.get("/api/orders", headers={"cookie": cookie})

    got = w.run(go).json()
    assert got["total"] == 13  # 11 seeded + 2 placed
    assert {o["order_id"] for o in got["orders"][:2]} == {"O900001", "O900002"}  # same-day: newest
    demo = [o for o in got["orders"] if o.get("demo")]
    assert {o["order_id"] for o in demo} == {"O900001", "O900002"}
    assert all(o["total"]["currency"] == "USD" for o in demo)
    assert all("payment" not in o and "token" not in repr(o).lower() for o in got["orders"])


# ---- hard rule 1 --------------------------------------------------------------------------------


@pytest.mark.req("CHK-02")
@pytest.mark.parametrize("field", ["digest", "idempotency_key", "extra"])
@pytest.mark.parametrize("pan", ["4242424242424242", "4242 4242 4242 4242", "4242-4242-4242-4242"])
def test_card_data_in_any_pay_field_is_a_422_with_no_echo_and_nothing_is_confirmed(
    w: World, field: str, pan: str
) -> None:
    body: dict[str, Any] = {"digest": "d" * 32, "idempotency_key": key(1, "pan")}
    body[field] = pan
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        await shown(c, cookie)
        return await raw_pay(c, cookie, body)

    refused = w.run(go)
    assert refused.status_code == 422
    assert "4242" not in refused.text
    nothing_was_placed()
