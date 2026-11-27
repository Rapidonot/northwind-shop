"""SA-01 PR 2: the security review's findings on the demo checkout, each pinned
(docs/reviews/2026-11-27-SA-01-pr2-security-review.md; hard rules 9, 10).

F1 a partly emptied paid bag is never paid twice (a concurrent remove; a transient failure);
F2 emptying uses fresh keys and trusts only a fresh read of the bag; F4 nothing after the
confirmation escapes as a bare 500, and the order-number cap refuses before any confirmation;
F5 a per-shopper order cap, before any confirmation; F6 the guard is per session.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from northwind_gateway import checkout as checkout_module
from northwind_gateway.cart_writes import CartWriter
from northwind_gateway.checkout import checkout_desk

from ._portal_world import PORTAL, VARIANT, World, add, key, pay, signed_in, summary, world

pytestmark = [pytest.mark.req("SA-01"), pytest.mark.req("CHK-05")]

A = "a" * 32
B = "b" * 32
OTHER = "P0004-BLK-REL-L"  # a second line, same jacket in another size


@pytest.fixture
def w(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with world(monkeypatch) as made:
        yield made


def captures() -> int:
    return sum(1 for e in checkout_desk().payment.effects if e.operation == "capture")


def issued() -> int:
    return len(checkout_desk().payment.confirmations._issued)


async def two_lines(c: httpx.AsyncClient, cookie: str) -> dict[str, Any]:
    assert (await add(c, cookie, VARIANT, n=1)).status_code == 200
    assert (await add(c, cookie, OTHER, n=2)).status_code == 200
    body: dict[str, Any] = (await summary(c, cookie)).json()
    assert len(body["lines"]) == 2
    return body


async def bag_variants(c: httpx.AsyncClient, cookie: str) -> set[str]:
    got = (await c.get("/api/cart", headers={"cookie": cookie})).json()["cart"]
    return {line["variant_id"] for line in got["lines"]}


def test_f1_a_line_removed_during_placement_still_leaves_the_bag_empty(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario A: the shopper removes X in another tab while Pay runs. Emptying must still remove
    Y (X's `not_in_cart` counts as removed), so nothing paid is left to pay for again."""
    cookie = signed_in(A)
    real_place = checkout_module.place

    async def place_then_remove(*args: Any, **kwargs: Any) -> Any:
        result = await real_place(*args, **kwargs)
        async with w.client() as c:  # "another tab"
            await c.post(
                "/api/cart/remove",
                headers={**PORTAL, "cookie": cookie},
                json={"variant_id": VARIANT, "idempotency_key": key(9, "tab")},
            )
        return result

    monkeypatch.setattr(checkout_module, "place", place_then_remove)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, set[str], httpx.Response]:
        shown = await two_lines(c, cookie)
        paid = await pay(c, cookie, shown["digest"], n=1)
        left = await bag_variants(c, cookie)
        again = await pay(c, cookie, (await summary(c, cookie)).json()["digest"], n=2)
        return paid, left, again

    paid, left, again = w.run(go)
    assert paid.status_code == 200 and paid.json()["bag_emptied"] is True
    assert left == set()
    assert again.status_code == 409 and again.json()["code"] == "empty"
    assert captures() == 1


def test_f1_a_line_that_failed_to_clear_is_never_paid_twice(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario B: X is removed, Y's removal fails. The bag [Y] has a new digest; Pay on it is
    refused with the earlier order's ID, and the refusal removes Y."""
    cookie = signed_in(A)
    real_write = CartWriter.write
    failed: list[str] = []

    async def flaky(self: CartWriter, caller: Any, tool: str, args: dict[str, Any]) -> Any:
        if tool == "remove_from_cart" and args.get("variant_id") == OTHER and not failed:
            failed.append(OTHER)
            raise TimeoutError
        return await real_write(self, caller, tool, args)

    monkeypatch.setattr(CartWriter, "write", flaky)

    async def go(c: httpx.AsyncClient) -> list[Any]:
        shown = await two_lines(c, cookie)
        first = await pay(c, cookie, shown["digest"], n=1)
        left = await bag_variants(c, cookie)
        fresh = (await summary(c, cookie)).json()
        second = await pay(c, cookie, fresh["digest"], n=2)
        return [first, left, fresh, second, await bag_variants(c, cookie)]

    first, left, fresh, second, after = w.run(go)
    assert first.status_code == 200 and first.json()["bag_emptied"] is False
    assert left == {OTHER} and fresh["digest"] != ""
    assert second.status_code == 409 and second.json()["code"] == "already_placed"
    assert second.json()["order_id"] == first.json()["order_id"]
    assert after == set()  # the refusal removed the paid line
    assert captures() == 1


def test_f2_a_lost_response_after_a_real_removal_is_not_a_failure(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The removal reached the cart but its answer was lost: the fresh read of the bag decides,
    so the bag counts as emptied and nothing is held."""
    cookie = signed_in(A)
    real_write = CartWriter.write
    lost: list[str] = []

    async def lose_answer(self: CartWriter, caller: Any, tool: str, args: dict[str, Any]) -> Any:
        result = await real_write(self, caller, tool, args)
        if tool == "remove_from_cart" and not lost:
            lost.append(args["variant_id"])
            raise TimeoutError
        return result

    monkeypatch.setattr(CartWriter, "write", lose_answer)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        assert (await add(c, cookie, n=1)).status_code == 200
        return await pay(c, cookie, (await summary(c, cookie)).json()["digest"], n=1)

    paid = w.run(go)
    assert paid.status_code == 200 and paid.json()["bag_emptied"] is True
    assert checkout_desk().unemptied == {} or all(not v for v in checkout_desk().unemptied.values())


def test_f2_each_attempt_uses_fresh_keys(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    cookie = signed_in(A)
    real_write = CartWriter.write
    keys: list[str] = []

    async def record(self: CartWriter, caller: Any, tool: str, args: dict[str, Any]) -> Any:
        if tool == "remove_from_cart":
            keys.append(args["idempotency_key"])
            if len(keys) == 1:
                raise TimeoutError  # the first attempt fails before reaching the cart
        return await real_write(self, caller, tool, args)

    monkeypatch.setattr(CartWriter, "write", record)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        assert (await add(c, cookie, n=1)).status_code == 200
        first = await pay(c, cookie, (await summary(c, cookie)).json()["digest"], n=1)
        assert first.json()["bag_emptied"] is False
        return await pay(c, cookie, (await summary(c, cookie)).json()["digest"], n=2)

    second = w.run(go)
    assert second.status_code == 409 and second.json()["code"] == "already_placed"
    assert len(keys) == 2 and keys[0] != keys[1]


def test_f4_an_exception_after_the_confirmation_is_an_envelope_and_holds_the_bag(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie = signed_in(A)

    async def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("after issue")

    monkeypatch.setattr(checkout_module, "place", boom)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, httpx.Response]:
        assert (await add(c, cookie, n=1)).status_code == 200
        digest = (await summary(c, cookie)).json()["digest"]
        first = await pay(c, cookie, digest, n=1)
        return first, await pay(c, cookie, digest, n=2)

    first, second = w.run(go)
    assert first.status_code == 503 and first.headers["content-type"].startswith("application/json")
    assert first.json()["errorCategory"] == "transient" and "after issue" not in first.text
    assert second.status_code == 409 and second.json()["code"] == "already_placed"


def test_f4_the_order_number_cap_refuses_before_any_confirmation(w: World) -> None:
    checkout_desk()._next = 1_000_000
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        assert (await add(c, cookie, n=1)).status_code == 200
        return await pay(c, cookie, (await summary(c, cookie)).json()["digest"], n=1)

    refused = w.run(go)
    assert refused.status_code == 409 and refused.json()["code"] == "demo_full"
    assert issued() == 0


def test_f5_the_per_shopper_order_cap_refuses_before_any_confirmation(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(checkout_module, "MAX_ORDERS_PER_SHOPPER", 1)
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, httpx.Response]:
        assert (await add(c, cookie, n=1)).status_code == 200
        first = await pay(c, cookie, (await summary(c, cookie)).json()["digest"], n=1)
        assert (await add(c, cookie, n=2)).status_code == 200
        return first, await pay(c, cookie, (await summary(c, cookie)).json()["digest"], n=2)

    first, second = w.run(go)
    assert first.status_code == 200
    assert second.status_code == 409 and second.json()["code"] == "demo_full"
    assert issued() == 1


def test_f6_another_sessions_held_bag_never_blocks_or_empties_this_one(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine, theirs = signed_in(A), signed_in(B)  # the same demo shopper, two browsers
    real_write = CartWriter.write

    async def fail_removals(self: CartWriter, caller: Any, tool: str, args: dict[str, Any]) -> Any:
        if tool == "remove_from_cart" and caller.session == A:
            raise TimeoutError
        return await real_write(self, caller, tool, args)

    monkeypatch.setattr(CartWriter, "write", fail_removals)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, httpx.Response]:
        assert (await add(c, mine, n=1)).status_code == 200
        held = await pay(c, mine, (await summary(c, mine)).json()["digest"], n=1)
        assert held.json()["bag_emptied"] is False
        assert (await add(c, theirs, n=1)).status_code == 200
        return held, await pay(c, theirs, (await summary(c, theirs)).json()["digest"], n=2)

    held, other = w.run(go)
    assert other.status_code == 200 and other.json()["order_id"] != held.json()["order_id"]


def test_n2_a_failed_placement_is_escalated_shown_on_hold_and_claims_no_payment(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie = signed_in(A)

    async def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("before authorize")

    monkeypatch.setattr(checkout_module, "place", boom)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, httpx.Response]:
        assert (await add(c, cookie, n=1)).status_code == 200
        paid = await pay(c, cookie, (await summary(c, cookie)).json()["digest"], n=1)
        return paid, await c.get("/api/orders", headers={"cookie": cookie})

    paid, orders = w.run(go)
    body = paid.json()
    assert paid.status_code == 503 and body["isRetryable"] is False
    assert body["message"] == checkout_module.BEING_CHECKED
    assert "try again" not in body["message"].lower() and "paid" not in body["message"].lower()
    order_id = body["order_id"]
    assert checkout_desk().orders.orders[order_id].status.value == "escalated"
    shown = next(o for o in orders.json()["orders"] if o["order_id"] == order_id)
    assert shown["status"] == "on_hold"


def test_n3_an_unreadable_bag_after_an_order_is_held_never_called_empty(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from northwind_assistant.catalog_client import CartReader

    cookie = signed_in(A)
    real_read = CartReader.read
    reads: list[int] = []

    async def malformed_after_pay(self: CartReader, caller: Any) -> Any:
        body = await real_read(self, caller)
        reads.append(1)
        if checkout_desk().orders.orders and len(reads) >= 2:
            return {"region": "US"}  # no lines at all: unreadable, never "empty"
        return body

    monkeypatch.setattr(CartReader, "read", malformed_after_pay)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        assert (await add(c, cookie, n=1)).status_code == 200
        reads.clear()
        return await pay(c, cookie, (await summary(c, cookie)).json()["digest"], n=1)

    paid = w.run(go)
    assert paid.status_code == 200 and paid.json()["bag_emptied"] is False
    assert any(checkout_desk().unemptied.values())  # held: the next Pay is refused, not placed
