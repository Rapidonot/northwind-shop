"""OPS-1 C6, C7 (test-writer): counts come from the stores; empty is valid; failed is not empty.

Gaps past test_ops1_views.py: the overview's numbers equal the detail views' rows and the stores'
contents; counts move when the store moves (nothing is cached or invented); every empty store
gives 200 and an empty view in every view; a store that cannot be read is not a 200 empty view.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_backend.audit import AuditContext, InMemoryAuditSink
from northwind_backend.cases import CaseAmounts
from northwind_backend.fakes.cases import CaseState
from northwind_backend.fakes.handoffs import HandoffState
from northwind_backend.fakes.orders import OrderState
from northwind_backend.handoff import HandoffAmounts, HandoffCase, Identity
from northwind_backend.inventory import InventoryFake, InventoryState, Line
from northwind_gateway.app import app
from northwind_gateway.demo_orders import DemoOrders
from northwind_gateway.handoff import HandoffStores
from northwind_gateway.staff import views
from northwind_gateway.staff.flag import DEMO_STAFF_ENV

pytestmark = pytest.mark.req("OPS-1")

SEEDED = 1000


def _case(reference: str) -> HandoffCase:
    return HandoffCase(
        reference=reference,
        identity=Identity("shopper", "S0188", "US"),
        trigger="button",
        intent="order_issue",
        root_cause="shopper_requested",
        recommended_action="contact the shopper",
        amounts=HandoffAmounts(source="order", order=CaseAmounts(100, 0, 0, "USD", None)),
        summary="Needs help.",
        summary_source="model",
        flags=(),
        order_id=None,
        order_case_id=None,
        correlation_id="gateway.handoff",
    )


def _checkout(inventory: InventoryState | None = None) -> views.CheckoutStores:
    return views.CheckoutStores(
        orders=OrderState((), []),
        inventory=inventory or InventoryState.from_fixtures(),
        audit=InMemoryAuditSink(),
    )


def _outcome(sink: InMemoryAuditSink, action: str, ref: str, outcome: dict[str, Any]) -> None:
    ctx = AuditContext(sink, correlation_id="t", actor="agent")
    started = ctx.attempt(action, {"order_id": ref})
    ctx.outcome(started, action, {"order_id": ref}, outcome)


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    clients: list[TestClient] = []

    def use(src: views.Sources, *, raise_server_exceptions: bool = True) -> TestClient:
        app.dependency_overrides[views.sources] = lambda: src
        c = TestClient(app, raise_server_exceptions=raise_server_exceptions)
        clients.append(c)
        return c

    yield use
    app.dependency_overrides.clear()


def test_every_view_of_empty_stores_is_a_valid_empty_200(serve: Any) -> None:
    empty = InventoryState.from_rows([])
    src = views.Sources(None, _checkout(empty), DemoOrders())
    c = serve(src)
    assert c.get("/api/staff/handoffs").json()["handoffs"] == []
    assert c.get("/api/staff/activity").json()["events"] == []
    stock = c.get("/api/staff/stock")
    assert stock.status_code == 200
    assert (stock.json()["holds"], stock.json()["variants"], stock.json()["nodes"]) == ([], [], [])
    overview = c.get("/api/staff/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert body["holds"] == {"count": 0, "units": 0, "nodes": []}
    assert body["latest"] == []
    assert body["orders"]["total"] == SEEDED  # the seeded store is not empty; demo orders are
    assert c.get("/api/staff/orders").json()["counts"]["total"] == SEEDED
    assert c.get("/api/staff/system").status_code == 200


def test_empty_handoff_desk_is_not_the_same_as_no_desk_in_the_overview(serve: Any) -> None:
    desk = HandoffStores(handoffs=HandoffState(), cases=CaseState(), audit=InMemoryAuditSink())
    c = serve(views.Sources(desk, _checkout(), DemoOrders()))
    body = c.get("/api/staff/overview").json()
    assert body["handoffs"] == {"open": 0, "by_status": {}}


def test_a_store_that_cannot_be_read_is_not_a_valid_empty_view(serve: Any) -> None:
    def broken() -> views.Sources:
        raise RuntimeError("store unavailable")

    app.dependency_overrides[views.sources] = broken
    c = TestClient(app, raise_server_exceptions=False)
    for view in ("overview", "activity", "handoffs", "orders", "stock"):
        reply = c.get(f"/api/staff/{view}")
        assert reply.status_code >= 500, view
        assert "events" not in reply.text and 'handoffs":[' not in reply.text.replace(" ", "")


def test_a_reader_failing_mid_view_is_an_error_not_an_empty_list(serve: Any) -> None:
    class Dead(InMemoryAuditSink):
        def read_all(self) -> tuple[Any, ...]:
            raise OSError("audit unreadable")

    checkout = views.CheckoutStores(
        orders=OrderState((), []), inventory=InventoryState.from_fixtures(), audit=Dead()
    )
    c = serve(views.Sources(None, checkout, DemoOrders()), raise_server_exceptions=False)
    for view in ("activity", "overview"):
        reply = c.get(f"/api/staff/{view}")
        assert reply.status_code >= 500, view


def test_overview_numbers_equal_the_detail_views_and_the_stores(serve: Any) -> None:
    desk_state = HandoffState()
    desk_state.cases["H000001"] = _case("H000001")
    desk_state.cases["H000002"] = _case("H000002")
    desk_state.opened["H000003"] = Identity("shopper", "S0188", "US")
    handoff_audit = InMemoryAuditSink()
    _outcome(handoff_audit, "handoff.file", "H000001", {"ok": True, "status": "queued"})
    checkout = _checkout()
    _outcome(checkout.audit, "order.confirm", "O001001", {"ok": True, "status": "confirmed"})
    _outcome(
        checkout.audit, "payment.capture", "O001002", {"ok": False, "errorCategory": "business"}
    )
    _outcome(
        checkout.audit, "payment.capture", "O001003", {"ok": False, "errorCategory": "transient"}
    )
    demo = DemoOrders()
    for n, status in enumerate(("confirmed", "on_hold", "on_hold")):
        demo.record(
            shopper="S0188",
            order_id=f"O00200{n}",
            placed_on="2026-11-27",
            status=status,
            lines=[{"product_id": "P0001", "variant_id": "P0001-BLK-REG-L", "quantity": 2}],
            total={"amount": "1.00", "currency": "USD"},
        )
    now = datetime.now(UTC)
    InventoryFake(lambda: now, state=checkout.inventory).reserve(
        idempotency_key="k1",
        order_id="O002000",
        region="US",
        postal_code="US-7099",
        lines=(Line("P0001-BLK-REG-L", 3),),
    )
    stores = HandoffStores(handoffs=desk_state, cases=CaseState(), audit=handoff_audit)
    c = serve(views.Sources(stores, checkout, demo))
    overview = c.get("/api/staff/overview").json()
    handoffs = c.get("/api/staff/handoffs").json()["handoffs"]
    events = c.get("/api/staff/activity").json()["events"]
    orders = c.get("/api/staff/orders").json()
    stock = c.get("/api/staff/stock").json()

    assert overview["handoffs"]["open"] == len(handoffs) == 3
    assert overview["handoffs"]["by_status"] == {"queued": 2, "opening": 1}
    assert overview["agent_events"]["total"] == len(events) == 4  # outcomes only, both sinks
    assert overview["agent_events"]["by_outcome"] == {"ok": 2, "refused": 1, "error": 1}
    assert sum(overview["agent_events"]["by_outcome"].values()) == len(events)
    assert overview["orders"] == orders["counts"]
    assert overview["catalog"]["orders"] == len(orders["rows"]) == SEEDED + 3
    assert orders["counts"]["escalated"] >= 2  # the two on-hold demo orders are among them
    assert overview["holds"]["count"] == len(stock["holds"]) >= 1
    assert overview["holds"]["units"] == sum(h["quantity"] for h in stock["holds"]) == 3
    assert overview["latest"] == events[:10]


def test_counts_follow_the_store_nothing_is_cached(serve: Any) -> None:
    src = views.Sources(None, _checkout(), DemoOrders())
    c = serve(src)
    before = c.get("/api/staff/overview").json()
    src.demo.record(
        shopper="S0188",
        order_id="O003000",
        placed_on="2026-11-27",
        status="confirmed",
        lines=[],
        total={"amount": "1.00", "currency": "USD"},
    )
    _outcome(src.checkout.audit, "order.confirm", "O003000", {"ok": True})
    after = c.get("/api/staff/overview").json()
    assert after["orders"]["total"] == before["orders"]["total"] + 1
    assert after["agent_events"]["total"] == before["agent_events"]["total"] + 1
    assert after["catalog"]["orders"] == before["catalog"]["orders"] + 1


def test_an_audit_attempt_without_an_outcome_is_not_counted_as_an_event(serve: Any) -> None:
    checkout = _checkout()
    ctx = AuditContext(checkout.audit, correlation_id="t", actor="agent")
    ctx.attempt("order.confirm", {"order_id": "O004000"})  # started, never finished
    c = serve(views.Sources(None, checkout, DemoOrders()))
    assert c.get("/api/staff/overview").json()["agent_events"] == {"total": 0, "by_outcome": {}}
    assert c.get("/api/staff/activity").json()["events"] == []


def test_expired_holds_do_not_count_in_the_overview(serve: Any) -> None:
    checkout = _checkout()
    past = datetime.now(UTC) - timedelta(days=2)
    InventoryFake(lambda: past, state=checkout.inventory).reserve(
        idempotency_key="k2",
        order_id="O005000",
        region="US",
        postal_code="US-7099",
        lines=(Line("P0001-BLK-REG-L", 1),),
    )
    c = serve(views.Sources(None, checkout, DemoOrders()))
    assert c.get("/api/staff/overview").json()["holds"] == {"count": 0, "units": 0, "nodes": []}
    assert c.get("/api/staff/stock").json()["holds"] == []


def test_stock_on_hand_equals_the_inventory_rows(serve: Any) -> None:
    inventory = InventoryState.from_fixtures()
    c = serve(views.Sources(None, _checkout(inventory), DemoOrders()))
    stock = c.get("/api/staff/stock").json()
    shown = {
        (v["variant_id"], node): n["on_hand"]
        for v in stock["variants"]
        for node, n in v["nodes"].items()
    }
    assert shown, "the view must show stock"
    for key, on_hand in shown.items():
        assert inventory.rows[key].on_hand == on_hand, key
    assert all(n in {node for _, node in inventory.rows} for n in stock["nodes"])
