"""OPS-1 C6, C7: the views read the real stores and count what is there; nothing is invented.

- Orders: the 1,000 seeded orders, counted independently from data/backend here; each shopper
  status equal to northwind-orders' own (`book`), order by order; Pay (demo) orders added.
- Handoffs, agent activity and stock holds: from their in-memory stores; an empty store is a valid
  empty view, distinct from a failed read.
- Internal-only products never appear (hard rule 7).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_backend.audit import AuditContext, InMemoryAuditSink
from northwind_backend.cases import SUMMARY_LABEL, CaseAmounts
from northwind_backend.fakes.cases import CaseState
from northwind_backend.fakes.handoffs import HandoffState
from northwind_backend.fakes.orders import DATA, OrderState
from northwind_backend.handoff import HandoffAmounts, HandoffCase, Identity
from northwind_backend.inventory import InventoryFake, InventoryState, Line
from northwind_gateway.app import app
from northwind_gateway.demo_orders import DemoOrders
from northwind_gateway.handoff import HandoffStores
from northwind_gateway.staff import views
from northwind_gateway.staff.flag import DEMO_STAFF_ENV

pytestmark = pytest.mark.req("OPS-1")


def _checkout() -> views.CheckoutStores:
    """The checkout's stores as the app registers them, fresh: placed orders, inventory, audit."""
    return views.CheckoutStores(
        orders=OrderState((), []),
        inventory=InventoryState.from_fixtures(),
        audit=InMemoryAuditSink(),
    )


FIXTURES = Path(__file__).resolve().parents[3] / "data" / "fixtures"


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


@pytest.fixture
def world() -> views.Sources:
    return views.Sources(handoffs=None, checkout=_checkout(), demo=DemoOrders())


@pytest.fixture
def client(world: views.Sources, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    app.dependency_overrides[views.sources] = lambda: world
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# -- orders (C6) -------------------------------------------------------------------------------


def test_order_counts_are_an_independent_recount_of_the_store(client: TestClient) -> None:
    orders = _jsonl(DATA / "orders.jsonl")
    labels = {r["order_id"]: r for r in _jsonl(DATA / "replay_labels.jsonl")}
    body = client.get("/api/staff/orders").json()
    counts = body["counts"]
    assert counts["total"] == len(orders) == 1000
    assert counts["escalated"] == sum(
        1 for r in labels.values() if r["expected_outcome"] == "escalated"
    )
    assert counts["escalated_by_reason"] == dict(
        sorted(
            Counter(
                o["exception"]
                for o in orders
                if labels[o["order_id"]]["expected_outcome"] == "escalated"
            ).items()
        )
    )
    assert counts["other_exceptions"] == sum(
        1
        for o in orders
        if o["exception"] and labels[o["order_id"]]["expected_outcome"] != "escalated"
    )
    assert counts["processed"] == sum(
        1
        for o in orders
        if not o["exception"] and labels[o["order_id"]]["expected_outcome"] != "escalated"
    )
    assert len(body["rows"]) == counts["total"]


def test_every_status_is_northwind_orders_own(client: TestClient) -> None:
    from northwind_mcp_orders.book import OrderBook

    book = OrderBook.from_data(DATA)
    rows = {r["order_id"]: r for r in client.get("/api/staff/orders").json()["rows"]}
    for order in book.orders.state.orders.values():
        theirs = book.view(order.order_id, shopper=order.shopper_id, region=order.region)
        assert rows[order.order_id]["status"] == theirs["status"], order.order_id
        assert rows[order.order_id]["placed_on"] == theirs["placed_on"], order.order_id


def test_the_damaged_parcel_order_is_the_demo_shoppers_delivered_one(client: TestClient) -> None:
    rows = {r["order_id"]: r for r in client.get("/api/staff/orders").json()["rows"]}
    assert rows["O000800"]["shopper_id"] == "S0188"
    assert rows["O000800"]["status"] == "delivered"


def test_a_pay_demo_order_joins_the_orders(world: views.Sources, client: TestClient) -> None:
    world.demo.record(
        shopper="S0188",
        order_id="O001001",
        placed_on="2026-11-27",
        status="on_hold",
        lines=[{"product_id": "P0001", "variant_id": "P0001-BLK-REG-L", "quantity": 2}],
        total={"amount": "178.00", "currency": "USD"},
    )
    body = client.get("/api/staff/orders").json()
    row = next(r for r in body["rows"] if r["order_id"] == "O001001")
    assert row["source"] == "demo" and row["escalated"] is True and row["units"] == 2
    assert body["counts"]["total"] == 1001


# -- handoffs, activity, stock (C7) --------------------------------------------------------------


def _case(reference: str, summary: str) -> HandoffCase:
    return HandoffCase(
        reference=reference,
        identity=Identity("shopper", "S0188", "US"),
        trigger="button",
        intent="order_issue",
        root_cause="shopper_requested",
        recommended_action="contact the shopper",
        amounts=HandoffAmounts(source="order", order=CaseAmounts(24400, 0, 0, "USD", None)),
        summary=summary,
        summary_source="model",
        flags=(),
        order_id="O000800",
        order_case_id=None,
        correlation_id="gateway.handoff",
    )


def test_handoffs_come_from_the_desks_queue(world: views.Sources, client: TestClient) -> None:
    state = HandoffState()
    state.opened["H000001"] = Identity("shopper", "S0188", "US")
    state.cases["H000001"] = _case("H000001", "Parcel arrived damaged; wants a replacement.")
    state.opened["H000002"] = Identity("shopper", "S0188", "US")  # opened, case not filed yet
    stores = HandoffStores(handoffs=state, cases=CaseState(), audit=InMemoryAuditSink())
    app.dependency_overrides[views.sources] = lambda: views.Sources(
        stores, world.checkout, world.demo
    )
    rows = client.get("/api/staff/handoffs").json()["handoffs"]
    assert [(r["reference"], r["status"]) for r in rows] == [
        ("H000001", "queued"),
        ("H000002", "opening"),
    ]
    first = rows[0]
    assert first["summary"] == "Parcel arrived damaged; wants a replacement."
    assert first["summary_label"] == SUMMARY_LABEL["model"]
    assert first["amount"] == {"minor": 24400, "currency": "USD"}
    assert first["time"] is None  # the queue keeps no time; none is guessed


def test_no_desk_yet_is_a_valid_empty_view(client: TestClient) -> None:
    reply = client.get("/api/staff/handoffs")
    assert reply.status_code == 200 and reply.json()["handoffs"] == []
    overview = client.get("/api/staff/overview").json()
    assert overview["handoffs"] == {"open": 0, "by_status": {}}
    assert overview["agent_events"] == {"total": 0, "by_outcome": {}}


def test_agent_activity_is_the_audit_outcomes(world: views.Sources, client: TestClient) -> None:
    audit = AuditContext(world.checkout.audit, correlation_id="t", actor="portal-checkout")
    ok = audit.attempt("order.confirm", {"order_id": "O001001"})
    audit.outcome(ok, "order.confirm", {"order_id": "O001001"}, {"ok": True, "status": "confirmed"})
    bad = audit.attempt("payment.capture", {"order_id": "O001002"})
    audit.outcome(
        bad,
        "payment.capture",
        {"order_id": "O001002"},
        {"ok": False, "errorCategory": "business", "code": "declined"},
    )
    events = client.get("/api/staff/activity").json()["events"]
    assert [(e["action"], e["ref"], e["outcome"], e["detail"]) for e in events] == [
        ("payment.capture", "O001002", "refused", "declined"),
        ("order.confirm", "O001001", "ok", "confirmed"),
    ]


@pytest.mark.parametrize(
    ("outcome", "shown"),
    [
        ({"ok": True}, "ok"),
        ({"ok": True, "escalated": True}, "escalated"),
        ({"ok": True, "status": "escalated"}, "escalated"),
        ({"ok": False, "errorCategory": "permission"}, "refused"),
        ({"ok": False, "errorCategory": "validation"}, "refused"),
        ({"ok": False, "errorCategory": "transient"}, "error"),
        ({"ok": False, "crashed": "RuntimeError"}, "error"),
        (None, "error"),
    ],
)
def test_outcomes_are_read_not_guessed(outcome: dict[str, Any] | None, shown: str) -> None:
    assert views.outcome_of(outcome) == shown


def test_stock_is_on_hand_less_live_holds_and_expired_holds_do_not_count(
    world: views.Sources,
) -> None:
    now = datetime(2026, 11, 27, 15, 0, tzinfo=UTC)
    inventory = InventoryFake(lambda: now, state=world.checkout.inventory)
    held = inventory.reserve(
        idempotency_key="k1",
        order_id="O001002",
        region="US",
        postal_code="US-7099",
        lines=(Line("P0001-BLK-REG-L", 2),),
    )
    assert not getattr(held, "is_error", False)
    stock = views.stock_view(world, now=now)
    [hold] = stock["holds"]
    assert (hold["order_id"], hold["variant_id"], hold["quantity"], hold["product"]) == (
        "O001002",
        "P0001-BLK-REG-L",
        2,
        "Tidewick Harbor Rain Jacket",
    )
    variant = next(v for v in stock["variants"] if v["variant_id"] == "P0001-BLK-REG-L")
    assert variant["nodes"][hold["node"]]["held"] == 2
    later = views.stock_view(world, now=now + timedelta(days=1))
    assert later["holds"] == []
    assert all(n["held"] == 0 for v in later["variants"] for n in v["nodes"].values())


def test_internal_products_never_appear(client: TestClient) -> None:
    internal = {
        p["product_id"] for p in _jsonl(FIXTURES / "products.jsonl") if p["visibility"] != "public"
    }
    assert internal  # the fixture has one; the test means something
    text = json.dumps(
        [client.get(f"/api/staff/{v}").json() for v in ("stock", "overview", "orders")]
    )
    for product in internal:
        assert product not in text
    overview = client.get("/api/staff/overview").json()
    public = sum(1 for p in _jsonl(FIXTURES / "products.jsonl") if p["visibility"] == "public")
    assert overview["catalog"]["products"] == public
