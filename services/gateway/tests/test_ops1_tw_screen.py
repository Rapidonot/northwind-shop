"""OPS-1 C8 (test-writer): the screen, past what test_ops1_screen.py covers.

Dict keys, nesting, non-strings, idempotence, NFKC and full-width card forms, a CVV, a canary or
key inside a longer or multi-line string; and end to end: a canary, a full-width PAN or a key
planted in each store field a view shows never reaches a response, and a hold on an internal-only
variant never appears in the stock view.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_backend.audit import AuditContext, InMemoryAuditSink
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
from northwind_gateway.staff.screen import (
    CARD_WITHHELD,
    INTERNAL_WITHHELD,
    SECRET_WITHHELD,
    canaries,
    screen,
    screen_text,
)

pytestmark = pytest.mark.req("OPS-1")

FIXTURES = Path(__file__).resolve().parents[3] / "data" / "fixtures"
PAN = "4111111111111111"
FULLWIDTH_PAN = "".join(chr(0xFF10 + int(d)) for d in PAN)
KEY = "sk-ant-api03-abcdefghijklmnop"
VIEW_NAMES = ("overview", "system", "activity", "handoffs", "orders", "stock")
ANY_WITHHELD = (CARD_WITHHELD, SECRET_WITHHELD, INTERNAL_WITHHELD)


def _canary() -> str:
    return sorted(canaries())[0]


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


# -- the function ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (FULLWIDTH_PAN, CARD_WITHHELD),
        (f"pay with {FULLWIDTH_PAN} ok", CARD_WITHHELD),
        ("card 4111 1111 1111 1111", CARD_WITHHELD),
        ("4111_1111_1111_1111", CARD_WITHHELD),
        ("CVV: 123", CARD_WITHHELD),
        ("cvc 456", CARD_WITHHELD),
        (f"line one\nline two {KEY}", SECRET_WITHHELD),
        ("a\nAuthorization: Bearer abcdefghijklmnopqrstuvwxyz\nb", SECRET_WITHHELD),
        ("X-API-KEY: abc", SECRET_WITHHELD),
        ("x-api-key=abc", SECRET_WITHHELD),
        ("sk-" + "A1_-" * 6, SECRET_WITHHELD),
    ],
)
def test_screen_text_withholds(text: str, expected: str) -> None:
    assert screen_text(text) == expected


def test_screen_text_withholds_every_canary_anywhere_in_a_string() -> None:
    assert canaries()
    for canary in canaries():
        for text in (canary, f"x{canary}", f"{canary}y", f"a\n{canary}\nb", f"[{canary}]"):
            assert screen_text(text) == INTERNAL_WITHHELD, text


def test_screen_covers_keys_nesting_tuples_and_leaves_other_values() -> None:
    value = {
        PAN: "k",
        "ok": [{"deep": KEY}, (f"n {_canary()}", "fine")],
        "nested": {"a": {"b": ["x", FULLWIDTH_PAN]}},
        "n": 3,
        "f": 1.5,
        "b": True,
        "none": None,
    }
    out = screen(value)
    assert CARD_WITHHELD in out
    assert out["ok"] == [{"deep": SECRET_WITHHELD}, [INTERNAL_WITHHELD, "fine"]]
    assert out["nested"] == {"a": {"b": ["x", CARD_WITHHELD]}}
    assert (out["n"], out["f"], out["b"], out["none"]) == (3, 1.5, True, None)
    assert PAN not in json.dumps(out)


def test_screen_is_idempotent_and_does_not_mutate_its_input() -> None:
    value = {"a": [f"{KEY}", PAN, "plain"], "b": {"c": _canary()}}
    snapshot = json.dumps(value, sort_keys=True)
    once = screen(value)
    assert json.dumps(value, sort_keys=True) == snapshot
    assert screen(once) == once


@pytest.mark.parametrize("text", ["", "   ", "O000800", "S0188", "1234", "2026-11-27", "sk-short"])
def test_screen_text_passes_harmless_strings(text: str) -> None:
    assert screen_text(text) == text


# -- end to end -------------------------------------------------------------------------------


def _internal_variant() -> tuple[str, str]:
    rows = [
        json.loads(line)
        for line in (FIXTURES / "variants.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    row = next(r for r in rows if r.get("visibility") == "internal")
    return str(row["variant_id"]), str(row["product_id"])


def _case(summary: str, order_id: str | None) -> HandoffCase:
    return HandoffCase(
        reference="H000001",
        identity=Identity("shopper", "S0188", "US"),
        trigger="button",
        intent="order_issue",
        root_cause="shopper_requested",
        recommended_action="contact the shopper",
        amounts=HandoffAmounts(source="none"),
        summary=summary,
        summary_source="model",
        flags=(),
        order_id=order_id,
        order_case_id=None,
        correlation_id="gateway.handoff",
    )


@pytest.fixture
def planted(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    canary = _canary()
    state = HandoffState()
    state.cases["H000001"] = _case(f"see {canary}", f"{FULLWIDTH_PAN}")
    handoff_audit = InMemoryAuditSink()
    hctx = AuditContext(handoff_audit, correlation_id="t", actor="handoff-desk")
    a = hctx.attempt("handoff.file", {"case_id": FULLWIDTH_PAN})
    hctx.outcome(
        a, "handoff.file", {"case_id": FULLWIDTH_PAN}, {"ok": True, "status": canary, "code": KEY}
    )

    checkout_audit = InMemoryAuditSink()
    cctx = AuditContext(checkout_audit, correlation_id="t", actor="portal-checkout")
    b = cctx.attempt("payment.capture", {"order_id": "O001002"})
    cctx.outcome(
        b,
        "payment.capture",
        {"order_id": "O001002"},
        {"ok": False, "errorCategory": "business", "code": "Bearer abcdefghijklmnopqrstuvwxyz"},
    )
    demo = DemoOrders()
    demo.record(
        shopper=canary,
        order_id="O001003",
        placed_on="2026-11-27",
        status="confirmed",
        lines=[],
        total={"amount": "1.00", "currency": "USD"},
    )
    stores = HandoffStores(handoffs=state, cases=CaseState(), audit=handoff_audit)
    checkout = views.CheckoutStores(
        orders=OrderState((), []), inventory=InventoryState.from_fixtures(), audit=checkout_audit
    )
    app.dependency_overrides[views.sources] = lambda: views.Sources(stores, checkout, demo)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_planted_values_in_every_field_never_reach_any_response(planted: TestClient) -> None:
    for view in VIEW_NAMES:
        reply = planted.get(f"/api/staff/{view}")
        assert reply.status_code == 200, view
        text = reply.text
        for secret in (PAN, FULLWIDTH_PAN, KEY, "abcdefghijklmnopqrstuvwxyz", *canaries()):
            assert secret not in text, (view, secret)
        for s in _strings(reply.json()):
            assert screen_text(s) == s, (view, s)  # nothing left that the screen would withhold


def test_planted_values_are_withheld_not_dropped(planted: TestClient) -> None:
    [row] = planted.get("/api/staff/handoffs").json()["handoffs"]
    assert row["summary"] == INTERNAL_WITHHELD
    assert row["order_id"] == CARD_WITHHELD
    events = planted.get("/api/staff/activity").json()["events"]
    assert len(events) == 2  # the count is kept: withheld, never silently removed
    assert any(e["detail"] == SECRET_WITHHELD for e in events)


def test_a_hold_on_an_internal_variant_never_appears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    variant, product = _internal_variant()
    now = datetime.now(UTC)
    state = InventoryState.from_rows(
        [
            {"variant_id": variant, "node": "DC-EAST", "region": "US", "on_hand": 5},
            {"variant_id": "P0001-BLK-REG-L", "node": "DC-EAST", "region": "US", "on_hand": 5},
        ]
    )
    held = InventoryFake(lambda: now, state=state).reserve(
        idempotency_key="k9",
        order_id="O001009",
        region="US",
        postal_code="US-7099",
        lines=(Line(variant, 1),),
    )
    src = views.Sources(
        None,
        views.CheckoutStores(orders=OrderState((), []), inventory=state, audit=InMemoryAuditSink()),
        DemoOrders(),
    )
    app.dependency_overrides[views.sources] = lambda: src
    try:
        with TestClient(app) as c:
            texts = [c.get(f"/api/staff/{v}").text for v in VIEW_NAMES]
    finally:
        app.dependency_overrides.clear()
    assert not getattr(held, "is_error", False), (
        "the hold must exist for this test to mean anything"
    )
    for text in texts:
        assert variant not in text and product not in text
    stock = json.loads(texts[VIEW_NAMES.index("stock")])
    assert all(h["variant_id"] != variant for h in stock["holds"])
    overview = json.loads(texts[0])
    assert overview["holds"]["units"] == 0 and overview["holds"]["count"] == 0
