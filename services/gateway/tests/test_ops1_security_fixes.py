"""OPS-1: the security review's lows, fixed (docs/reviews/2026-11-27-OPS-1-security-review.raw.md).

- L1: with the flag off nothing under /api/staff answers differently from a path that does not
  exist: no trailing-slash redirect, no 405 or Allow header for an unlisted method. Decided by an
  ASGI gate before routing; on, only GET on an exact view path gets through.
- L2: the checkout desk is built once, when app.py is imported, before any request can race to
  build a second one in the threadpool.
- L3: an audit time is shown to the second, so a clock time is never withheld as card data.
- L4: Agent activity sends the newest events and the total; the overview counts them all; a
  screened string is cached.
- L5: the canary screen fails closed: every view is a structured 503 while the answer key cannot
  be read.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from northwind_backend.audit import AuditContext, InMemoryAuditSink
from northwind_backend.fakes.orders import OrderState
from northwind_backend.inventory import InventoryState
from northwind_gateway.app import app
from northwind_gateway.demo_orders import DemoOrders
from northwind_gateway.staff import screen as staff_screen
from northwind_gateway.staff import views
from northwind_gateway.staff.flag import DEMO_STAFF_ENV
from northwind_gateway.staff.routes import UNAVAILABLE

pytestmark = pytest.mark.req("OPS-1")

APP = Path(__file__).resolve().parents[1] / "src" / "northwind_gateway" / "app.py"
VIEWS = ("overview", "system", "activity", "handoffs", "orders", "stock")


def _checkout() -> views.CheckoutStores:
    return views.CheckoutStores(
        orders=OrderState((), []),
        inventory=InventoryState.from_fixtures(),
        audit=InMemoryAuditSink(),
    )


@pytest.fixture
def world() -> views.Sources:
    return views.Sources(handoffs=None, checkout=_checkout(), demo=DemoOrders())


@pytest.fixture
def client(world: views.Sources) -> Iterator[TestClient]:
    app.dependency_overrides[views.sources] = lambda: world
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


# -- L1 --------------------------------------------------------------------------------------------


def _same_as_unknown(client: TestClient, method: str, path: str, unknown: str) -> None:
    mine = client.request(method, path)
    theirs = client.request(method, unknown)
    assert (mine.status_code, mine.headers.get("allow"), mine.headers.get("location")) == (
        theirs.status_code,
        theirs.headers.get("allow"),
        theirs.headers.get("location"),
    ), (method, path)
    assert mine.status_code == 404, (method, path)
    if method != "HEAD":
        assert mine.json() == theirs.json(), (method, path)


@pytest.mark.parametrize("flag", [None, "0"])
def test_off_no_redirect_and_no_405_reveal_the_console(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, flag: str | None
) -> None:
    if flag is None:
        monkeypatch.delenv(DEMO_STAFF_ENV, raising=False)
    else:
        monkeypatch.setenv(DEMO_STAFF_ENV, flag)
    for path, unknown in (
        ("/api/staff/overview/", "/api/no-such-path/"),
        ("/api/staff", "/api/no-such-path"),
        ("/api/staff/", "/api/no-such-path/"),
        ("/api/staff/orders", "/api/no-such-path"),
    ):
        for method in ("GET", "HEAD", "POST", "TRACE", "PROPFIND", "FROB"):
            _same_as_unknown(client, method, path, unknown)


def test_on_only_get_on_an_exact_view_gets_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    for view in VIEWS:
        assert client.get(f"/api/staff/{view}").status_code == 200, view
        _same_as_unknown(client, "GET", f"/api/staff/{view}/", "/api/no-such-path/")
        for method in ("TRACE", "PROPFIND", "HEAD", "OPTIONS"):
            _same_as_unknown(client, method, f"/api/staff/{view}", "/api/no-such-path")


def test_the_gate_is_on_the_app() -> None:
    from northwind_gateway.staff.routes import StaffGate

    assert any(getattr(m, "cls", None) is StaffGate for m in app.user_middleware)


# -- L2 --------------------------------------------------------------------------------------------


def test_app_builds_the_checkout_desk_on_import() -> None:
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    calls = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]
    assert any(
        isinstance(c.func, ast.Attribute)
        and c.func.attr == "checkout_desk"
        and isinstance(c.func.value, ast.Name)
        and c.func.value.id == "checkout"
        for c in calls
    )


_IMPORT_PROBE = """
import northwind_gateway.app as app_module
from northwind_backend.inventory import InventoryState
from northwind_gateway import checkout
info = checkout.checkout_desk.cache_info()
assert info.currsize == 1, info
assert type(checkout.checkout_desk().inventory) is InventoryState
print("imported")
"""


def test_importing_the_app_builds_the_desk_without_a_database() -> None:
    """The user's question on L2: building the desk at import opens no database connection. The
    import runs in a fresh process with DATABASE_URL on a port nothing listens on (9, discard)."""
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql://nw:nw@127.0.0.1:9/northwind?connect_timeout=1",
    }
    env.pop(DEMO_STAFF_ENV, None)
    # A fixed probe from this file, never input.
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _IMPORT_PROBE], env=env, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().endswith("imported")


# -- L3 --------------------------------------------------------------------------------------------


def test_a_clock_time_is_never_withheld_as_card_data() -> None:
    base = datetime(2026, 11, 27, 10, 15, 30, tzinfo=UTC)
    raw = next(
        text
        for micro in range(0, 1_000_000, 7)
        if staff_screen.screen_text(text := base.replace(microsecond=micro).isoformat())
        == staff_screen.CARD_WITHHELD
    )
    shown = views._iso_text(raw)
    assert shown == "2026-11-27T10:15:30Z"
    assert staff_screen.screen_text(shown) == shown


def test_activity_times_from_a_real_clock_pass_the_screen(
    world: views.Sources, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    audit = AuditContext(world.checkout.audit, correlation_id="t", actor="portal-checkout")
    for i in range(50):
        action = audit.attempt("order.confirm", {"order_id": f"O{900000 + i}"})
        audit.outcome(action, "order.confirm", {"order_id": f"O{900000 + i}"}, {"ok": True})
    events = client.get("/api/staff/activity").json()["events"]
    assert len(events) == 50
    for event in events:
        assert event["time"].endswith("Z") and "." not in event["time"], event["time"]
        assert datetime.fromisoformat(event["time"])


# -- L4 --------------------------------------------------------------------------------------------


def test_activity_sends_the_newest_events_and_the_total(
    world: views.Sources, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    audit = AuditContext(world.checkout.audit, correlation_id="t", actor="portal-checkout")
    for i in range(views.EVENT_LIMIT + 100):
        action = audit.attempt("order.confirm", {"order_id": f"O{900000 + i}"})
        audit.outcome(action, "order.confirm", {"order_id": f"O{900000 + i}"}, {"ok": True})
    body = client.get("/api/staff/activity").json()
    assert len(body["events"]) == views.EVENT_LIMIT
    assert body["total"] == views.EVENT_LIMIT + 100
    assert body["events"][0]["ref"] == f"O{900000 + views.EVENT_LIMIT + 99}"  # newest first
    overview = client.get("/api/staff/overview").json()
    assert overview["agent_events"]["total"] == views.EVENT_LIMIT + 100


def test_a_screened_string_is_cached() -> None:
    before = staff_screen.screen_text.cache_info().hits
    staff_screen.screen_text("O000800")
    staff_screen.screen_text("O000800")
    assert staff_screen.screen_text.cache_info().hits > before


# -- L5 --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("content", [None, "not json", '{"internal_only": []}'])
def test_no_readable_answer_key_is_a_503_on_every_view(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str | None
) -> None:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    key = tmp_path / "_answer_key.json"
    if content is not None:
        key.write_text(content, encoding="utf-8")
    monkeypatch.setattr(staff_screen, "ANSWER_KEY", key)
    for view in VIEWS:
        reply = client.get(f"/api/staff/{view}")
        assert reply.status_code == 503, view
        assert reply.json() == UNAVAILABLE
        assert reply.headers["cache-control"] == "no-store"
