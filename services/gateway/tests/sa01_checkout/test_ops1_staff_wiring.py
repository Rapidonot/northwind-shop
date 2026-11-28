"""OPS-1 C6, C7: the console reads the real stores through the app's real wiring, with no override
(advisor-reviewer findings 1 and 2).

A Pay (demo) driven through the real routes (SA-01's portal world: real mcp-catalog and
mcp-orders) shows up in the console's orders, agent activity and stock, read through `app.py`'s
`use_checkout` provider. A fraud review shows the escalated order with its checkout escalation code
as the reason. The handoff desk the app builds registers the very stores the console reads. (The
handoff route itself runs the Care agent, a model, so it is not driven here: the SA-06 tests cover
the route; this covers the wiring from the desk to the console.)
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from northwind_backend.handoff import HandoffAmounts, HandoffCase, Identity
from northwind_gateway import checkout as checkout_module
from northwind_gateway import handoff as handoff_module
from northwind_gateway.checkout import checkout_desk
from northwind_gateway.staff import traces
from northwind_gateway.staff.flag import DEMO_STAFF_ENV

from ._portal_world import VARIANT, World, add, pay, signed_in, summary, world

pytestmark = pytest.mark.req("OPS-1")

A = "a" * 32


async def bag_then_summary(client: httpx.AsyncClient, cookie: str) -> dict[str, Any]:
    """As test_sa01_pr2_checkout.py: one unit in the bag, then the live summary."""
    assert (await add(client, cookie)).status_code == 200
    got = await summary(client, cookie)
    assert got.status_code == 200, got.text
    body: dict[str, Any] = got.json()
    return body


@pytest.fixture
def w(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    with world(monkeypatch) as made:
        yield made


def test_a_pay_through_the_real_routes_reaches_the_console(w: World) -> None:
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, dict[str, dict]]:  # type: ignore[type-arg]
        shown = await bag_then_summary(c, cookie)
        paid = await pay(c, cookie, shown["digest"])
        views = {
            v: (await c.get(f"/api/staff/{v}")).json()
            for v in ("orders", "activity", "stock", "overview")
        }
        return paid, views

    paid, views = w.run(go)
    assert paid.status_code == 200, paid.text
    order_id = paid.json()["order_id"]
    assert order_id == f"O{checkout_module.FIRST_ORDER_NUMBER}"

    row = next(r for r in views["orders"]["rows"] if r["order_id"] == order_id)
    assert (row["source"], row["status"], row["escalated"], row["reason"]) == (
        "demo",
        "confirmed",
        False,
        None,
    )
    assert views["orders"]["counts"]["total"] == 1001

    actions = {e["action"] for e in views["activity"]["events"] if e["ref"] == order_id}
    # Order-level actions carry the order; payment actions carry their intent, not the order.
    assert {"inventory.reserve", "fraud.assess", "inventory.commit", "order.confirm"} <= actions, (
        actions
    )
    assert any(e["action"] == "payment.capture" for e in views["activity"]["events"])
    assert views["overview"]["agent_events"]["total"] == views["activity"]["total"] > 0

    desk = checkout_desk()
    variant = next(v for v in views["stock"]["variants"] if v["variant_id"] == VARIANT)
    for node, shown in variant["nodes"].items():
        assert shown["on_hand"] == desk.inventory.rows[(VARIANT, node)].on_hand, node


def test_a_fraud_review_shows_escalated_with_its_reason(w: World) -> None:
    checkout_desk().fraud.flagged = frozenset({f"O{checkout_module.FIRST_ORDER_NUMBER}"})
    cookie = signed_in(A)

    async def go(c: httpx.AsyncClient) -> tuple[httpx.Response, dict]:  # type: ignore[type-arg]
        shown = await bag_then_summary(c, cookie)
        paid = await pay(c, cookie, shown["digest"])
        return paid, (await c.get("/api/staff/orders")).json()

    paid, orders = w.run(go)
    assert paid.status_code == 409 and paid.json()["code"] == "on_hold"
    order_id = f"O{checkout_module.FIRST_ORDER_NUMBER}"
    row = next(r for r in orders["rows"] if r["order_id"] == order_id)
    assert (row["status"], row["escalated"]) == ("on_hold", True)
    assert row["reason"] == checkout_desk().orders.orders[order_id].escalation
    assert row["reason"]  # the checkout's own code, never a guessed one
    assert orders["counts"]["escalated_by_reason"].get(row["reason"]) == 1


def test_the_desk_the_app_builds_registers_the_stores_the_console_reads(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handoff_module, "_stores", None)
    handoff_module.handoff_desk.cache_clear()
    try:
        desk = handoff_module.handoff_desk()
        stores = handoff_module.handoff_stores()
        assert stores is not None
        assert desk._queue.state is stores.handoffs  # type: ignore[attr-defined]
        assert desk._care.cases.state is stores.cases  # type: ignore[attr-defined]
        assert desk._audit.sink is stores.audit
        stores.handoffs.opened["H000001"] = Identity("shopper", "S0188", "US")
        stores.handoffs.cases["H000001"] = HandoffCase(
            reference="H000001",
            identity=Identity("shopper", "S0188", "US"),
            trigger="button",
            intent="order_issue",
            root_cause="shopper_requested",
            recommended_action="contact the shopper",
            amounts=HandoffAmounts(source="none"),
            summary="Parcel arrived damaged.",
            summary_source="model",
            flags=(),
            order_id="O000800",
            order_case_id=None,
            correlation_id="gateway.handoff",
        )
        rows = w.run(lambda c: c.get("/api/staff/handoffs")).json()["handoffs"]
        assert [(r["reference"], r["status"], r["summary"]) for r in rows] == [
            ("H000001", "queued", "Parcel arrived damaged.")
        ]
    finally:
        handoff_module.handoff_desk.cache_clear()


def test_the_checkout_trace_uses_the_checkouts_first_order_number() -> None:
    trace = next(t for t in traces.TRACES if t["id"] == "checkout")
    assert trace["refs"] == [f"O{checkout_module.FIRST_ORDER_NUMBER}"]
