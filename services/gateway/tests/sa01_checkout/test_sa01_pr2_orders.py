"""SA-01 PR 2: GET /api/orders, the signed-in shopper's order history (hard rules 7, 8, 10)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from northwind_backend.fakes.orders import DATA
from northwind_gateway.app import app
from northwind_gateway.demo_orders import STATUS_TEXT, demo_orders
from northwind_gateway.orders import SIGN_IN, UNAVAILABLE, OrdersLister, orders_lister
from northwind_gateway.signin import DEMO_SHOPPER
from northwind_mcp_orders.wording import STATUS_WORDING

from ._portal_world import KEY, World, session_cookie, signed_in, world

pytestmark = pytest.mark.req("SA-01")

A = "a" * 32
SEEDED = [
    json.loads(line)
    for line in (Path(DATA) / "orders.jsonl").read_text().splitlines()
    if line and json.loads(line)["shopper_id"] == DEMO_SHOPPER
]


@pytest.fixture
def w(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with world(monkeypatch) as made:
        yield made


def test_anonymous_gets_the_sign_in_prompt_never_an_empty_list(w: World) -> None:
    got = w.run(lambda c: c.get("/api/orders", headers={"cookie": session_cookie(A)}))
    # Signed out is a state, not an error: 200, so the browser logs no failed request on a normal
    # page (the e2e "no errors" journey), and never an empty list (hard rule 10).
    assert got.status_code == 200
    assert got.json() == {"signed_in": False, "message": SIGN_IN}
    assert "orders" not in got.json()
    # A 200 can be cached, so it must say not to (scoped security read S1).
    assert got.headers.get("cache-control") == "no-store"


def test_the_signed_in_shopper_gets_their_seeded_orders_newest_first(w: World) -> None:
    got = w.run(lambda c: c.get("/api/orders", headers={"cookie": signed_in(A)}))
    assert got.status_code == 200
    body = got.json()
    assert set(body) == {"signed_in", "orders", "total"} and body["signed_in"] is True
    assert body["total"] == len(SEEDED) == 11
    assert {o["order_id"] for o in body["orders"]} == {o["order_id"] for o in SEEDED}
    dates = [o["placed_on"] for o in body["orders"]]
    assert dates == sorted(dates, reverse=True)
    text = json.dumps(body)
    for row in SEEDED:
        assert row["payment_token"] not in text and row["shipping_postal_code"] not in text
    assert "shopper" not in text


def test_demo_orders_join_the_history_for_their_shopper_only(w: World) -> None:
    demo_orders().record(
        shopper=DEMO_SHOPPER,
        order_id="O900001",
        placed_on="2099-01-01",
        status="confirmed",
        lines=[{"product_id": "P0004", "variant_id": "P0004-BLK-REL-M", "quantity": 1}],
        total={"amount": "160.00", "currency": "USD"},
    )
    demo_orders().record(
        shopper="S0002",
        order_id="O900002",
        placed_on="2099-01-02",
        status="confirmed",
        lines=[{"product_id": "P0004", "variant_id": "P0004-BLK-REL-M", "quantity": 1}],
        total={"amount": "160.00", "currency": "USD"},
    )
    body = w.run(lambda c: c.get("/api/orders", headers={"cookie": signed_in(A)})).json()
    assert body["orders"][0]["order_id"] == "O900001"
    assert body["orders"][0]["status_text"] == STATUS_WORDING["confirmed"]
    assert "O900002" not in json.dumps(body)


def test_a_failed_read_is_the_fixed_503_never_an_empty_list(w: World) -> None:
    app.dependency_overrides[orders_lister] = lambda: OrdersLister("http://127.0.0.1:9/mcp", KEY)
    got = w.run(lambda c: c.get("/api/orders", headers={"cookie": signed_in(A)}))
    assert got.status_code == 503 and got.json()["message"] == UNAVAILABLE


def test_a_forged_sign_in_is_anonymous(w: World) -> None:
    forged = signed_in(A).rsplit(".", 1)[0] + "." + "0" * 64
    got = w.run(lambda c: c.get("/api/orders", headers={"cookie": forged}))
    assert got.status_code == 200 and got.json() == {"signed_in": False, "message": SIGN_IN}
    assert got.headers.get("cache-control") == "no-store"


def test_the_demo_statuses_use_the_same_plain_words_as_mcp_orders() -> None:
    for status, words in STATUS_TEXT.items():
        assert STATUS_WORDING[status] == words


def test_a_signed_in_shopper_with_no_orders_gets_a_valid_empty_list(w: World) -> None:
    """Scoped security read S2: the third state, distinct from signed out (no `orders` key) and from
    access failed (503): signed in, with an empty list."""

    class NoOrders:
        async def list(self, caller: object) -> list[dict[str, object]]:
            return []

    app.dependency_overrides[orders_lister] = lambda: NoOrders()
    got = w.run(lambda c: c.get("/api/orders", headers={"cookie": signed_in(A)}))
    assert got.status_code == 200
    assert got.json() == {"signed_in": True, "orders": [], "total": 0}
    assert got.headers.get("cache-control") == "no-store"
