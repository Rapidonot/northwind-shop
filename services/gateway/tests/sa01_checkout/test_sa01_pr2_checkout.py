"""SA-01 PR 2: the demo checkout, CHK-02's portal confirmation control (decision 0027; hard rules 1,
2, 9, 10; CHK-05's conditions from the plan).

Pay (demo) through the fakes: the gateway is the one confirmation issuer, BE-04's inventory store
holds the stock, and `orders_place`'s deterministic steps run in code. No model call: condition (3)
makes every LLM entry point raise and counts zero requests at the replay proxy.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from northwind_backend.agents import orders_place, runtime
from northwind_backend.inventory import HoldStatus
from northwind_backend.ports import OrderStatus
from northwind_common import llm
from northwind_gateway import checkout as checkout_module
from northwind_gateway.checkout import checkout_desk

from ._portal_world import (
    OTHER_SHOPPER,
    VARIANT,
    World,
    add,
    pay,
    session_cookie,
    signed_in,
    summary,
    world,
)

pytestmark = pytest.mark.req("SA-01")

A = "a" * 32
B = "b" * 32


@pytest.fixture
def w(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with world(monkeypatch) as made:
        yield made


def book_size() -> int:
    """How many confirmations the desk's book has issued."""
    return len(checkout_desk().payment.confirmations._issued)


def effects(kind: str) -> int:
    """Money moved: the payment fake's own effects of one kind (authorize, capture, void)."""
    return sum(1 for e in checkout_desk().payment.effects if e.operation == kind)


def holding() -> list[Any]:
    """The reservations that hold or committed stock (never released or expired ones)."""
    live = {HoldStatus.HELD, HoldStatus.COMMITTED}
    return [r for r in checkout_desk().inventory.records.values() if r.status in live]


async def bag_then_summary(
    client: httpx.AsyncClient, cookie: str, quantity: int = 1
) -> dict[str, Any]:
    assert (await add(client, cookie, quantity=quantity)).status_code == 200
    got = await summary(client, cookie)
    assert got.status_code == 200, got.text
    body: dict[str, Any] = got.json()
    return body


# ---- the summary --------------------------------------------------------------------------------


def test_the_summary_shows_the_live_lines_total_delivery_and_returns_policy(w: World) -> None:
    body = w.run(lambda c: bag_then_summary(c, signed_in(A), quantity=2))
    assert [line["variant_id"] for line in body["lines"]] == [VARIANT]
    assert body["lines"][0]["unit_price"] == {"amount": "160.00", "currency": "USD"}
    assert body["total"] == {"amount": "320.00", "currency": "USD"}
    assert body["return_policy"] == "POL-RETURNS-US"
    assert "delivery" in body and "$" not in body["delivery"]
    assert body["issues"] == [] and body["signed_in"] is True
    assert len(body["digest"]) == 32
    assert book_size() == 0  # the summary issues no confirmation


def test_a_price_changed_in_the_live_store_is_the_one_summarised(w: World) -> None:
    w.store.set_price(VARIANT, "US", "151.25", "USD")
    body = w.run(lambda c: bag_then_summary(c, signed_in(A)))
    assert body["total"] == {"amount": "151.25", "currency": "USD"}


# ---- Pay (demo) ---------------------------------------------------------------------------------


def test_pay_places_the_order_empties_the_bag_and_lists_it(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        shown = await bag_then_summary(c, cookie)
        paid = await pay(c, cookie, shown["digest"])
        bag = await c.get("/api/cart", headers={"cookie": cookie})
        orders = await c.get("/api/orders", headers={"cookie": cookie})
        return paid, bag, orders

    paid, bag, orders = w.run(go)
    assert paid.status_code == 200, paid.text
    order_id = paid.json()["order_id"]
    assert paid.json()["result"] == "placed" and paid.json()["bag_emptied"] is True
    assert paid.json()["total"] == {"amount": "160.00", "currency": "USD"}
    assert checkout_desk().orders.orders[order_id].status is OrderStatus.CONFIRMED
    assert bag.json()["cart"]["lines"] == []
    listed = orders.json()["orders"]
    assert listed[0]["order_id"] == order_id and listed[0]["status"] == "confirmed"
    assert book_size() == 1
    [record] = checkout_desk().inventory.records.values()
    assert record.status is HoldStatus.COMMITTED  # committed before capture


def test_no_confirmation_exists_before_pay_is_pressed(w: World) -> None:
    w.run(lambda c: bag_then_summary(c, signed_in(A)))
    assert book_size() == 0
    assert checkout_desk().orders.orders == {}


def test_pay_needs_the_demo_sign_in(w: World) -> None:
    cookie = session_cookie(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        shown = await bag_then_summary(c, cookie)
        assert shown["signed_in"] is False
        return await pay(c, cookie, shown["digest"])

    paid = w.run(go)
    assert paid.status_code == 401 and paid.json()["errorCategory"] == "permission"
    assert book_size() == 0


def test_a_changed_bag_is_refused_before_any_confirmation(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        shown = await bag_then_summary(c, cookie)
        w.store.set_price(VARIANT, "US", "99.00", "USD")
        return await pay(c, cookie, shown["digest"])

    paid = w.run(go)
    assert paid.status_code == 409 and paid.json()["code"] == "summary_changed"
    assert book_size() == 0 and checkout_desk().orders.orders == {}


def test_over_the_order_limit_is_refused_before_issue(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(checkout_module, "DEFAULT_ORDER_LIMITS", {"USD": 10_000})
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        shown = await bag_then_summary(c, cookie)
        assert shown["issues"] == ["over_order_limit"]
        return await pay(c, cookie, shown["digest"])

    paid = w.run(go)
    assert paid.status_code == 409 and paid.json()["code"] == "over_order_limit"
    assert book_size() == 0 and effects("authorize") == 0


@pytest.mark.req("CHK-05")
def test_two_pays_with_the_same_key_make_one_order(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> list[httpx.Response]:
        shown = await bag_then_summary(c, cookie)
        return list(await asyncio.gather(*(pay(c, cookie, shown["digest"], n=1) for _ in range(2))))

    first, second = w.run(go)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(checkout_desk().orders.orders) == 1
    assert effects("authorize") == 1 and effects("capture") == 1
    assert len(checkout_desk().inventory.records) == 1 and book_size() == 1


@pytest.mark.req("CHK-05")
def test_two_keys_racing_on_one_bag_make_one_order(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> list[httpx.Response]:
        shown = await bag_then_summary(c, cookie)
        return list(await asyncio.gather(*(pay(c, cookie, shown["digest"], n=n) for n in (1, 2))))

    results = w.run(go)
    assert sorted(r.status_code for r in results) == [200, 409]
    refused = next(r for r in results if r.status_code == 409)
    assert refused.json()["code"] in {"empty", "summary_changed"}
    assert len(checkout_desk().orders.orders) == 1 and effects("capture") == 1


@pytest.mark.req("CHK-05")
def test_a_second_key_on_a_paid_bag_is_refused(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        shown = await bag_then_summary(c, cookie)
        assert (await pay(c, cookie, shown["digest"], n=1)).status_code == 200
        return await pay(c, cookie, shown["digest"], n=2)

    again = w.run(go)
    assert again.status_code == 409 and again.json()["code"] == "empty"
    assert len(checkout_desk().orders.orders) == 1


def test_the_same_key_with_another_summary_is_refused(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        shown = await bag_then_summary(c, cookie)
        assert (await pay(c, cookie, shown["digest"], n=1)).status_code == 200
        return await pay(c, cookie, "f" * 32, n=1)  # well formed, never card-like

    reused = w.run(go)
    assert reused.status_code == 409 and reused.json()["code"] == "idempotency_conflict"


@pytest.mark.req("BE-04")
def test_two_shoppers_racing_for_the_last_unit_make_one_reservation(w: World) -> None:
    rows = checkout_desk().inventory.rows
    mine_only = sorted(k for k, row in rows.items() if k[0] == VARIANT and row.region == "US")
    for row_key, row in rows.items():
        if row_key[0] == VARIANT:
            row.on_hand = 1 if row_key == mine_only[0] else 0  # one unit left, in one US node
    mine, theirs = signed_in(A), signed_in(B, OTHER_SHOPPER)

    async def go(c: httpx.AsyncClient) -> list[httpx.Response]:
        shown = [await bag_then_summary(c, cookie) for cookie in (mine, theirs)]
        return list(
            await asyncio.gather(
                pay(c, mine, shown[0]["digest"]), pay(c, theirs, shown[1]["digest"])
            )
        )

    results = w.run(go)
    assert sorted(r.status_code for r in results) == [200, 409]
    lost = next(r for r in results if r.status_code == 409).json()
    assert lost["code"] == "no_longer_available"
    assert lost["message"] == checkout_module.NO_LONGER
    assert lost["short"][0]["variant_id"] == VARIANT
    assert VARIANT in lost["substitutes"]
    assert len(holding()) == 1  # one unit, one hold: never oversold
    assert effects("capture") == 1


def test_a_fraud_review_holds_the_order_releases_the_stock_and_moves_no_money(w: World) -> None:
    desk = checkout_desk()
    desk.fraud.flagged = frozenset({"O900001"})
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        shown = await bag_then_summary(c, cookie)
        paid = await pay(c, cookie, shown["digest"])
        return (
            paid,
            await c.get("/api/orders", headers={"cookie": cookie}),
            await c.get("/api/cart", headers={"cookie": cookie}),
        )

    paid, orders, bag = w.run(go)
    assert paid.status_code == 409 and paid.json()["code"] == "on_hold"
    assert desk.orders.orders["O900001"].status is OrderStatus.ESCALATED
    assert effects("authorize") == 0 and effects("capture") == 0
    assert holding() == []  # fraud review comes before any hold; nothing is kept
    assert orders.json()["orders"][0]["status"] == "on_hold"
    assert bag.json()["cart"]["lines"]  # the bag is kept


@pytest.mark.parametrize(
    "headers",
    [
        {"content-type": "text/plain", "x-northwind-portal": "1"},
        {"content-type": "application/json"},
        {
            "content-type": "application/json",
            "x-northwind-portal": "1",
            "sec-fetch-site": "cross-site",
        },
        {
            "content-type": "application/json",
            "x-northwind-portal": "1",
            "origin": "https://evil.example",
        },
    ],
)
def test_pay_from_anywhere_but_the_portal_is_refused(w: World, headers: dict[str, str]) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        shown = await bag_then_summary(c, cookie)
        return await c.post(
            "/api/checkout/pay",
            headers={**headers, "cookie": cookie},
            content=b'{"digest": "%s", "idempotency_key": "payabcdefghijklmnopqrstu"}'
            % shown["digest"].encode(),
        )

    refused = w.run(go)
    assert refused.status_code in {403, 422}
    assert book_size() == 0


def test_card_data_in_a_pay_request_is_refused_and_never_echoed(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        return await c.post(
            "/api/checkout/pay",
            headers={
                "content-type": "application/json",
                "x-northwind-portal": "1",
                "cookie": cookie,
            },
            json={"digest": "0" * 32, "idempotency_key": "4242424242424242"},
        )

    refused = w.run(go)
    assert refused.status_code == 422 and "4242" not in refused.text
    assert book_size() == 0


# ---- condition (3): no model, ever ------------------------------------------------------------


def _raise(*_: Any, **__: Any) -> Any:
    raise AssertionError("the checkout path reached a model entry point")


def test_a_full_pay_never_reaches_any_llm_entry_point(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    for module, name in (
        (llm, "anthropic_client"),
        (llm, "agent_options"),
        (runtime, "run_subagent"),
        (orders_place, "run_subagent"),
        (orders_place, "run_order_placement"),
    ):
        monkeypatch.setattr(module, name, _raise)
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        shown = await bag_then_summary(c, cookie)
        return await pay(c, cookie, shown["digest"])

    paid = w.run(go)
    assert paid.status_code == 200 and paid.json()["result"] == "placed"


@pytest.mark.llm_suite("backend-replay")
def test_the_replay_proxy_sees_zero_requests_during_pay(w: World, llm_proxy: Any) -> None:
    cookie = signed_in(A)
    state = llm_proxy.state
    before = (len(state.calls), len(state.misses), len(state.fallback_attempts))

    async def go(c: httpx.AsyncClient) -> httpx.Response:
        shown = await bag_then_summary(c, cookie)
        return await pay(c, cookie, shown["digest"])

    paid = w.run(go)
    assert paid.status_code == 200 and paid.json()["result"] == "placed"
    assert (
        (len(state.calls), len(state.misses), len(state.fallback_attempts)) == before == (0, 0, 0)
    )
