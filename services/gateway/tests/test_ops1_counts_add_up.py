"""OPS-1: every count the console shows adds up to the whole it sits under (the user's look fix,
2026-11-28).

The "Escalated orders" tile showed 50 escalated orders over a note that listed every order with an
exception (25 x 4 = 100). Now:
- orders: total = escalated + other exceptions + processed; exceptions = escalated + other
  exceptions; the escalated orders' reasons add up to the escalated count (an escalated order with
  no recorded reason is counted as `unrecorded`, never dropped);
- the overview: agent events by outcome add up to the total, handoffs by status to the open count,
  and its order counts are the orders view's.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_backend.audit import AuditContext, InMemoryAuditSink
from northwind_backend.fakes.cases import CaseState
from northwind_backend.fakes.handoffs import HandoffState
from northwind_backend.fakes.orders import DATA, OrderState
from northwind_backend.handoff import Identity
from northwind_backend.inventory import InventoryState
from northwind_gateway.app import app
from northwind_gateway.demo_orders import DemoOrders
from northwind_gateway.handoff import HandoffStores
from northwind_gateway.staff import views
from northwind_gateway.staff.flag import DEMO_STAFF_ENV

pytestmark = pytest.mark.req("OPS-1")

LINE = [{"product_id": "P0001", "variant_id": "P0001-BLK-REG-L", "quantity": 1}]
TOTAL = {"amount": "89.00", "currency": "USD"}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def adds_up(counts: Mapping[str, Any]) -> None:
    assert counts["total"] == counts["escalated"] + counts["other_exceptions"] + counts["processed"]
    assert counts["exceptions"] == counts["escalated"] + counts["other_exceptions"]
    assert sum(counts["escalated_by_reason"].values()) == counts["escalated"]
    assert all(n > 0 for n in counts["escalated_by_reason"].values())


@pytest.fixture
def world() -> views.Sources:
    checkout = views.CheckoutStores(
        orders=OrderState((), []),
        inventory=InventoryState.from_fixtures(),
        audit=InMemoryAuditSink(),
    )
    return views.Sources(handoffs=None, checkout=checkout, demo=DemoOrders())


@pytest.fixture
def client(world: views.Sources, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    app.dependency_overrides[views.sources] = lambda: world
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_the_seeded_orders_add_up_and_match_an_independent_recount(client: TestClient) -> None:
    orders = {o["order_id"]: o for o in _jsonl(DATA / "orders.jsonl")}
    labels = {r["order_id"]: r for r in _jsonl(DATA / "replay_labels.jsonl")}
    escalated = [i for i in orders if labels[i]["expected_outcome"] == "escalated"]
    counts = client.get("/api/staff/orders").json()["counts"]
    adds_up(counts)
    assert counts["escalated"] == len(escalated) == 50
    assert counts["exceptions"] == sum(1 for o in orders.values() if o["exception"]) == 100
    assert counts["escalated_by_reason"] == dict(
        sorted(Counter(orders[i]["exception"] for i in escalated).items())
    )
    # Only escalated orders are broken down: an exception that did not escalate is not a part.
    assert set(counts["escalated_by_reason"]) == {"fraud_flag", "stock_out"}


def test_pay_demo_orders_still_add_up_including_an_escalation_with_no_code(
    world: views.Sources, client: TestClient
) -> None:
    for order_id, status in (("O900001", "on_hold"), ("O900002", "confirmed")):
        world.demo.record(
            shopper="S0188",
            order_id=order_id,
            placed_on="2026-11-28",
            status=status,
            lines=LINE,
            total=TOTAL,
        )
    counts = client.get("/api/staff/orders").json()["counts"]
    adds_up(counts)
    assert counts["total"] == 1002
    assert counts["escalated"] == 51
    assert counts["escalated_by_reason"]["unrecorded"] == 1


def test_the_overview_tiles_add_up(
    world: views.Sources, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    handoffs = HandoffState()
    handoffs.opened["H000001"] = Identity("shopper", "S0188", "US")
    stores = HandoffStores(handoffs=handoffs, cases=CaseState(), audit=InMemoryAuditSink())
    with_handoffs = views.Sources(handoffs=stores, checkout=world.checkout, demo=world.demo)
    monkeypatch.setitem(app.dependency_overrides, views.sources, lambda: with_handoffs)
    audit = AuditContext(world.checkout.audit, correlation_id="t", actor="portal-checkout")
    for i, outcome in enumerate(
        ({"ok": True}, {"escalated": True}, {"errorCategory": "business"}, None, {"ok": True})
    ):
        action = audit.attempt("order.confirm", {"order_id": f"O90000{i}"})
        if outcome is not None:
            audit.outcome(action, "order.confirm", {"order_id": f"O90000{i}"}, outcome)
    overview = client.get("/api/staff/overview").json()
    assert sum(overview["agent_events"]["by_outcome"].values()) == overview["agent_events"]["total"]
    assert sum(overview["handoffs"]["by_status"].values()) == overview["handoffs"]["open"] == 1
    adds_up(overview["orders"])
    assert overview["orders"] == client.get("/api/staff/orders").json()["counts"]
