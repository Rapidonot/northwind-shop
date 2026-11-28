"""OPS-1 C8: card data, secrets and internal canaries never leave /api/staff (hard rules 1, 4, 7).

The screen's key patterns are a copy of the cassette scanner's, because the gateway must not import
`northwind_llm_replay` (not a gateway dependency; it must not ship in the hosted server). This test
may import it: it pins the copy to the original and proves every string `scan_text` flags, with the
same canaries, is withheld by the staff screen. End to end, card data planted in a handoff summary,
an audit field and an order never reaches a response.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path

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
from northwind_gateway.staff import screen as staff_screen
from northwind_gateway.staff import views
from northwind_gateway.staff.flag import DEMO_STAFF_ENV
from northwind_llm_replay import scan

pytestmark = pytest.mark.req("OPS-1")


def _checkout() -> views.CheckoutStores:
    """The checkout's stores as the app registers them, fresh: placed orders, inventory, audit."""
    return views.CheckoutStores(
        orders=OrderState((), []),
        inventory=InventoryState.from_fixtures(),
        audit=InMemoryAuditSink(),
    )


REPO = Path(__file__).resolve().parents[3]
PAN = "4111111111111111"  # a sandbox test card number, Luhn-valid
GROUPED = "4111 1111 1111 1111"
SAMPLES = (
    PAN,
    GROUPED,
    "4111-1111-1111-1111",
    f"card {PAN} please",
    "sk-ant-api03-abcdefghijklmnop",
    "sk-" + "a" * 24,
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
    "x-api-key: abc123",
    '{\\"x-api-key\\": \\"abc123\\"}',
)


def test_the_key_patterns_are_the_scanners_own() -> None:
    mine = [(kind, p.pattern, p.flags) for kind, p in staff_screen.KEY_PATTERNS]
    theirs = [(kind, p.pattern, p.flags) for kind, p in scan._KEY_PATTERNS]
    assert mine == theirs


def test_the_canaries_are_the_scanners_own() -> None:
    assert staff_screen.canaries() == scan.load_canaries(REPO)
    assert staff_screen.canaries()  # the answer key has some: the check below means something


def test_every_string_the_scanner_flags_is_withheld() -> None:
    canaries = scan.load_canaries(REPO)
    strings = [*SAMPLES, *canaries, *(f"note {c} here" for c in canaries)]
    for text in strings:
        assert scan.scan_text(text, canaries=canaries), text  # the scanner flags it...
        assert staff_screen.screen_text(text) in (  # ...and the screen withholds all of it
            staff_screen.CARD_WITHHELD,
            staff_screen.SECRET_WITHHELD,
            staff_screen.INTERNAL_WITHHELD,
        ), text


def test_plain_text_passes_unchanged() -> None:
    for text in (
        "O000800",
        "P0001-BLK-REG-L",
        "US-7099",
        "2026-11-27T15:04:18Z",
        "Tidewick Harbor Rain Jacket",
    ):
        assert staff_screen.screen_text(text) == text


def test_the_gateway_never_imports_the_proxy_package() -> None:
    root = REPO / "services" / "gateway" / "src"
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("northwind_llm_replay"), path
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith("northwind_llm_replay") for a in node.names), path


@pytest.fixture
def planted(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Card data and a key where a store would not normally let them in: the edge still holds."""
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    state = HandoffState()
    state.cases["H000001"] = HandoffCase(
        reference="H000001",
        identity=Identity("shopper", "S0188", "US"),
        trigger="button",
        intent="order_issue",
        root_cause="shopper_requested",
        recommended_action="contact the shopper",
        amounts=HandoffAmounts(source="none"),
        summary=f"Shopper typed {GROUPED} and sk-ant-api03-abcdefghijklmnop",
        summary_source="model",
        flags=(),
        order_id=None,
        order_case_id=None,
        correlation_id="gateway.handoff",
    )
    checkout = _checkout()
    audit = AuditContext(checkout.audit, correlation_id="t", actor="portal-checkout")
    action = audit.attempt("order.confirm", {"order_id": "O001001"})
    audit.outcome(action, "order.confirm", {"order_id": "O001001"}, {"ok": True, "status": PAN})
    demo = DemoOrders()
    demo.record(
        shopper="S0188",
        order_id=PAN,
        placed_on="2026-11-27",
        status="confirmed",
        lines=[],
        total={"amount": "1.00", "currency": "USD"},
    )
    stores = HandoffStores(handoffs=state, cases=CaseState(), audit=InMemoryAuditSink())
    app.dependency_overrides[views.sources] = lambda: views.Sources(stores, checkout, demo)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_planted_card_data_and_keys_never_reach_a_response(planted: TestClient) -> None:
    for view in ("overview", "system", "activity", "handoffs", "orders", "stock"):
        text = planted.get(f"/api/staff/{view}").text
        assert PAN not in text and "1111 1111" not in text, view
        assert "sk-ant-" not in text, view
        assert not scan.scan_text(
            json.dumps(json.loads(text)), canaries=scan.load_canaries(REPO)
        ), view
    summary = planted.get("/api/staff/handoffs").json()["handoffs"][0]["summary"]
    assert summary == staff_screen.CARD_WITHHELD
