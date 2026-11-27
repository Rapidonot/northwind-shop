"""SA-01 PR 2, test-writer pass: order history edges. Identity comes only from the signed cookie
(hard rule 7), an access failure is a 503 and never an empty list (hard rule 10), and no payment
detail is shown (hard rule 1)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from northwind_gateway.app import app
from northwind_gateway.orders import OrdersLister, orders_lister

from ._portal_world import KEY, OTHER_SHOPPER, World, session_cookie, signed_in, world

pytestmark = pytest.mark.req("SA-01")

A = "a" * 32
B = "b" * 32


@pytest.fixture
def w(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with world(monkeypatch) as made:
        yield made


@pytest.mark.parametrize("probe", ["?shopper_id=S0001", "?shopper=S0001&customer_id=S0001"])
def test_a_query_parameter_never_chooses_whose_orders_are_shown(w: World, probe: str) -> None:
    anonymous = w.run(lambda c: c.get(f"/api/orders{probe}", headers={"cookie": session_cookie(A)}))
    assert anonymous.status_code == 401 and "orders" not in anonymous.json()
    other = w.run(
        lambda c: c.get(f"/api/orders{probe}", headers={"cookie": signed_in(B, OTHER_SHOPPER)})
    )
    assert other.status_code == 200
    mine = w.run(lambda c: c.get("/api/orders", headers={"cookie": signed_in(A)})).json()
    assert {o["order_id"] for o in other.json()["orders"]}.isdisjoint(
        {o["order_id"] for o in mine["orders"]}
    )


def test_an_unreachable_orders_service_is_a_503_with_no_orders_key(w: World) -> None:
    app.dependency_overrides[orders_lister] = lambda: OrdersLister("http://127.0.0.1:9/mcp", KEY)
    got = w.run(lambda c: c.get("/api/orders", headers={"cookie": signed_in(A)}))
    assert got.status_code == 503
    assert got.json()["errorCategory"] == "transient" and got.json()["isRetryable"] is True
    assert "orders" not in got.json() and "total" not in got.json()


def test_the_history_is_never_cached_and_shows_no_payment_detail(w: World) -> None:
    got = w.run(lambda c: c.get("/api/orders", headers={"cookie": signed_in(A)}))
    assert got.headers.get("cache-control") == "no-store"
    text = got.text.lower()
    for banned in ("tok_", "payment_token", "card", "cvv", "intent", "postal", "address"):
        assert banned not in text, banned


def test_every_listed_order_has_an_id_a_status_a_status_text_and_lines(w: World) -> None:
    body = w.run(lambda c: c.get("/api/orders", headers={"cookie": signed_in(A)})).json()
    assert body["total"] == len(body["orders"]) > 0
    for order in body["orders"]:
        assert order["order_id"].startswith("O") and order["status"] and order["status_text"]
        assert order["lines"] and all(
            ln["quantity"] >= 1 and ln["variant_id"].startswith(ln["product_id"])
            for ln in order["lines"]
        )
