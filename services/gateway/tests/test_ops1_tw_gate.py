"""OPS-1 C1, C2 (test-writer): gaps in the flag and read-only tests.

Off: the 404 is the unknown path's in status, body and headers, for paths the route walk does not
name (the bare prefix, a trailing slash, a query string, an unknown sub-path, a case variant) and
for every method including OPTIONS; the flag is read per request. On: HEAD and OPTIONS are the same
404 as an unknown path; an unknown sub-path stays 404. No staff request sets a cookie, and no
staff request changes any store.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from northwind_backend.audit import AuditContext, InMemoryAuditSink
from northwind_backend.fakes.cases import CaseState
from northwind_backend.fakes.handoffs import HandoffState
from northwind_backend.fakes.orders import OrderState
from northwind_backend.handoff import HandoffAmounts, HandoffCase, Identity
from northwind_backend.inventory import InventoryState
from northwind_gateway.app import app
from northwind_gateway.demo_orders import DemoOrders
from northwind_gateway.handoff import HandoffStores
from northwind_gateway.staff import views
from northwind_gateway.staff.flag import DEMO_STAFF_ENV

pytestmark = pytest.mark.req("OPS-1")

VIEWS = ("overview", "system", "activity", "handoffs", "orders", "stock")
UNKNOWN = "/api/no-such-path-anywhere"
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
ODD_PATHS = (
    "/api/staff",
    "/api/staff/",
    "/api/staff/overview/",
    "/api/staff/overview?x=1",
    "/api/staff/overview/extra",
    "/api/staff/nonexistent",
    "/api/staff/OVERVIEW",
    "/api/staff//overview",
    "/api/staff/overview;a=b",
    "/api/staff/stream",
)


def _case() -> HandoffCase:
    return HandoffCase(
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
        order_id=None,
        order_case_id=None,
        correlation_id="gateway.handoff",
    )


def _world() -> views.Sources:
    state = HandoffState()
    state.opened["H000001"] = Identity("shopper", "S0188", "US")
    state.cases["H000001"] = _case()
    handoff_audit = InMemoryAuditSink()
    checkout_audit = InMemoryAuditSink()
    for sink in (handoff_audit, checkout_audit):
        ctx = AuditContext(sink, correlation_id="t", actor="a")
        action = ctx.attempt("order.confirm", {"order_id": "O001001"})
        ctx.outcome(action, "order.confirm", {"order_id": "O001001"}, {"ok": True})
    demo = DemoOrders()
    demo.record(
        shopper="S0188",
        order_id="O001001",
        placed_on="2026-11-27",
        status="confirmed",
        lines=[{"product_id": "P0001", "variant_id": "P0001-BLK-REG-L", "quantity": 1}],
        total={"amount": "1.00", "currency": "USD"},
    )
    return views.Sources(
        HandoffStores(handoffs=state, cases=CaseState(), audit=handoff_audit),
        views.CheckoutStores(
            orders=OrderState((), []),
            inventory=InventoryState.from_fixtures(),
            audit=checkout_audit,
        ),
        demo,
    )


def _snapshot(src: views.Sources) -> object:
    assert src.handoffs is not None
    inv = src.checkout.inventory
    return (
        dict(src.handoffs.handoffs.opened),
        dict(src.handoffs.handoffs.cases),
        dict(src.handoffs.cases.__dict__.get("cases", {})),
        src.handoffs.audit.read_all(),
        src.checkout.audit.read_all(),
        dict(src.checkout.orders.orders),
        {k: v.on_hand for k, v in inv.rows.items()},
        dict(inv.records),
        src.demo.everyone(),
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.parametrize("value", [None, "0", "true", " 1"])
def test_off_odd_paths_match_unknown_in_status_body_and_headers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(DEMO_STAFF_ENV, raising=False)
    else:
        monkeypatch.setenv(DEMO_STAFF_ENV, value)

    def never() -> views.Sources:
        raise AssertionError("a store was read with the console off")

    app.dependency_overrides[views.sources] = never
    for method in METHODS:
        base = client.request(method, UNKNOWN)
        for path in ODD_PATHS:
            reply = client.request(method, path)
            assert reply.status_code == base.status_code == 404, (method, path)
            assert reply.content == base.content, (method, path)
            assert reply.headers.get("content-type") == base.headers.get("content-type")
            assert "set-cookie" not in reply.headers, (method, path)
            assert reply.headers.get("cache-control") == base.headers.get("cache-control")


def test_off_request_with_body_and_headers_is_still_the_same_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DEMO_STAFF_ENV, raising=False)
    app.dependency_overrides[views.sources] = lambda: pytest.fail("a store was read")
    client.cookies.set("nw_session", "x")
    unknown = client.post(UNKNOWN, json={"a": 1})
    for view in VIEWS:
        reply = client.post(
            f"/api/staff/{view}",
            json={"a": 1},
            headers={"Authorization": "Bearer x", "Origin": "http://evil.example"},
        )
        assert (reply.status_code, reply.content) == (unknown.status_code, unknown.content)


def test_the_flag_is_read_on_each_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _world()
    app.dependency_overrides[views.sources] = lambda: src
    for value, status in (("1", 200), ("0", 404), ("1", 200), (None, 404)):
        if value is None:
            monkeypatch.delenv(DEMO_STAFF_ENV, raising=False)
        else:
            monkeypatch.setenv(DEMO_STAFF_ENV, value)
        assert client.get("/api/staff/overview").status_code == status, value


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
def test_on_head_and_options_are_the_unknown_path_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    app.dependency_overrides[views.sources] = lambda: pytest.fail("a store was read")
    unknown = client.request(method, UNKNOWN)
    for view in VIEWS:
        reply = client.request(method, f"/api/staff/{view}")
        assert reply.status_code == unknown.status_code == 404, (method, view)
        assert reply.content == unknown.content


def test_on_an_unknown_staff_path_is_still_the_unknown_path_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    unknown = client.get(UNKNOWN)
    for path in ("/api/staff/nonexistent", "/api/staff/OVERVIEW", "/api/staff/overview/x"):
        reply = client.get(path)
        assert (reply.status_code, reply.content) == (unknown.status_code, unknown.content), path


def test_on_staff_requests_set_no_cookie_and_open_no_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    app.dependency_overrides[views.sources] = _world
    for view in VIEWS:
        reply = client.get(f"/api/staff/{view}")
        assert reply.status_code == 200
        assert not reply.headers.get_list("set-cookie"), view
    assert len(client.cookies) == 0


@pytest.mark.parametrize("flag", ["1", "0"])
def test_no_staff_request_changes_any_store(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    monkeypatch.setenv(DEMO_STAFF_ENV, flag)
    src = _world()
    app.dependency_overrides[views.sources] = lambda: src
    before = _snapshot(src)
    for view in VIEWS:
        for method in METHODS:
            client.request(method, f"/api/staff/{view}", json={"x": 1} if method != "GET" else None)
        client.get(f"/api/staff/{view}")
    assert _snapshot(src) == before
