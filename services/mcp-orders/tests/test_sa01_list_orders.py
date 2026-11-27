"""SA-01 PR 2: `list_orders`, the portal's order history (hard rules 7, 8, 10).

The signed-in shopper's own orders in their own region, newest first, with the allow-listed fields
only. Portal-only: the assistant's agent is refused it, and the portal's agent is refused
`get_order` (test_chk06). Plain JSON, never an `<order_data>` label: no model reads it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.fakes.orders import DATA
from northwind_backend.ports import OrderStatus
from northwind_mcp_orders import testing
from northwind_mcp_orders.book import OrderBook
from northwind_mcp_orders.server import OrdersServer

pytestmark = pytest.mark.req("SA-01")

DEMO = testing.DEMO_SHOPPER
PORTAL = "shopper-portal"
ORDER_KEYS = {"order_id", "placed_on", "status", "status_text", "lines"}
FORBIDDEN = (
    "payment_token",
    "shipping_postal_code",
    "postal",
    "exception",
    "escalation",
    "shopper",
)


def _rows(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (Path(DATA) / name).read_text().splitlines() if line]


ORDERS = _rows("orders.jsonl")


@pytest.fixture(scope="module")
def live() -> Iterator[tuple[OrdersServer, str]]:
    instance = testing.server(OrderBook.from_data())
    with testing.running(instance) as url:
        yield instance, url


def call(url: str, args: dict[str, Any], **token: Any) -> tuple[bool, Any]:
    bearer, _ = testing.token(**{"agent": PORTAL, **token})

    async def go() -> tuple[bool, Any]:
        async with testing.connect(url, bearer) as client:
            result = await client.call_tool("list_orders", args)
        text = "".join(getattr(b, "text", "") for b in result.content)
        return bool(result.is_error), json.loads(text)

    return asyncio.run(go())


def test_the_demo_shopper_gets_exactly_their_own_orders_newest_first(
    live: tuple[OrdersServer, str],
) -> None:
    _, url = live
    is_error, body = call(url, {})
    assert not is_error
    own = [o for o in ORDERS if o["shopper_id"] == DEMO and o["region"] == "US"]
    assert body["total"] == len(own) == 11
    ids = [o["order_id"] for o in body["orders"]]
    expected = sorted(own, key=lambda o: (o["placed_on"], o["order_id"]), reverse=True)
    assert ids == [o["order_id"] for o in expected]
    dates = [o["placed_on"] for o in body["orders"]]
    assert dates == sorted(dates, reverse=True)


def test_each_order_has_the_allow_listed_fields_only(live: tuple[OrdersServer, str]) -> None:
    _, url = live
    _, body = call(url, {})
    for order in body["orders"]:
        assert set(order) == ORDER_KEYS
        for line in order["lines"]:
            assert set(line) == {"product_id", "variant_id", "quantity"}
        assert isinstance(order["status_text"], str) and order["status_text"]
    text = json.dumps(body)
    for word in FORBIDDEN:
        assert word not in text
    for row in ORDERS:
        if row["shopper_id"] == DEMO:
            assert row["payment_token"] not in text
            assert row["shipping_postal_code"] not in text


def test_another_region_or_shopper_sees_none_of_them(live: tuple[OrdersServer, str]) -> None:
    _, url = live
    _, other_region = call(url, {}, region="CA")
    assert other_region == {"orders": [], "total": 0}  # a valid empty result, not an error
    other = next(o["shopper_id"] for o in ORDERS if o["shopper_id"] != DEMO)
    _, theirs = call(
        url, {}, shopper=other, region=next(o["region"] for o in ORDERS if o["shopper_id"] == other)
    )
    assert theirs["orders"] and all(
        o["order_id"] not in {r["order_id"] for r in ORDERS if r["shopper_id"] == DEMO}
        for o in theirs["orders"]
    )


def test_a_status_changed_while_running_shows_in_the_next_list(
    live: tuple[OrdersServer, str],
) -> None:
    server, url = live
    first = next(o["order_id"] for o in ORDERS if o["shopper_id"] == DEMO)
    before = server.book.orders.state.orders[first].status
    try:
        server.book.set_status(first, OrderStatus.ESCALATED)
        _, body = call(url, {})
        shown = next(o for o in body["orders"] if o["order_id"] == first)
        assert shown["status"] == "on_hold"
    finally:
        server.book.set_status(first, before)


def test_without_a_sign_in_it_is_refused_not_empty(live: tuple[OrdersServer, str]) -> None:
    _, url = live
    is_error, body = call(url, {}, shopper=None)
    assert is_error and body["errorCategory"] == "permission"


@pytest.mark.parametrize(
    "args", [{"shopper_id": "S0001"}, {"region": "CA"}, {"limit": 3}, {"note": "4111111111111111"}]
)
def test_any_argument_is_refused_so_none_can_choose_the_shopper(
    live: tuple[OrdersServer, str], args: dict[str, Any]
) -> None:
    _, url = live
    is_error, body = call(url, args)
    assert is_error and body["errorCategory"] == "validation"
    assert "4111" not in json.dumps(body)


@pytest.mark.req("NFR-01")
@pytest.mark.parametrize("agent", ["shopper-assistant", "shopper-query"])
def test_no_other_agent_is_offered_or_allowed_it(
    live: tuple[OrdersServer, str], agent: str
) -> None:
    _, url = live
    bearer, _ = testing.token(agent=agent)

    async def names() -> list[str]:
        async with testing.connect(url, bearer) as client:
            return [tool.name for tool in (await client.list_tools()).tools]

    assert "list_orders" not in asyncio.run(names())
    is_error, body = call(url, {}, agent=agent)
    assert is_error and body["code"] == "not_allowed"
