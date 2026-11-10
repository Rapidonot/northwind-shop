"""A test-only order driver for BE-08 (row #20). Not an agent and not product code.

It runs the synthetic orders in `data/backend/` through the audited views, as actor
`test-order-driver`, so that `reconstruct_order` has a full order flow to rebuild. It calls
`authorize` and `capture` on fakes, as FOUND-3's own tests do; hard rule 9's confirmation stays
#21's prerequisite before any agent calls them. Nothing under `src` may import this module (pinned
in test_be08_views.py).

Amounts are test figures (2,500 minor units per item), not prices: an order carries no amount (hard
rule 2), and nothing here is shown to a shopper.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from northwind_backend.audit import AuditContext, AuditSink, InMemoryAuditSink
from northwind_backend.errors import Ok, ToolError
from northwind_backend.fakes.carrier import CarrierFake
from northwind_backend.fakes.fraud import FraudFake
from northwind_backend.fakes.payment import PAYMENT_SCOPES, PaymentFake
from northwind_backend.generate import fraud_flagged_order_ids
from northwind_backend.ports import FraudDecision, Money, PaymentIntent, TrackingEvent
from northwind_backend.views import AuditedCarrierPort, AuditedFraudPort, AuditedPaymentPort

ACTOR = "test-order-driver"
DATA = Path(__file__).resolve().parents[4] / "data" / "backend"
CURRENCY = {"US": "USD", "CA": "CAD", "UK": "GBP", "EU": "EUR"}
PER_ITEM_MINOR = 2_500
FIXED_CLOCK = "2026-11-10T00:00:00+00:00"
# The driver is the order flow's owner here, so its views get every operation. Agents never do.
FULL_FRAUD = frozenset({"assess", "read"})
FULL_CARRIER = frozenset({"create_shipment", "track"})


def fixed_clock() -> str:
    return FIXED_CLOCK


def load_jsonl(name: str) -> list[dict[str, Any]]:
    with (DATA / name).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def orders() -> list[dict[str, Any]]:
    return load_jsonl("orders.jsonl")


def tracking_events() -> dict[str, tuple[TrackingEvent, ...]]:
    return {
        row["tracking_number"]: tuple(TrackingEvent(e["on"], e["status"]) for e in row["events"])
        for row in load_jsonl("tracking.jsonl")
    }


def amount_for(order: Mapping[str, Any]) -> Money:
    items = sum(int(line["quantity"]) for line in order["lines"])
    return Money(items * PER_ITEM_MINOR, CURRENCY[order["region"]])


@dataclass
class Fakes:
    payment: PaymentFake
    fraud: FraudFake
    carrier: CarrierFake

    @classmethod
    def for_orders(cls, rows: list[dict[str, Any]]) -> Fakes:
        return cls(
            PaymentFake(),
            FraudFake(flagged_order_ids=fraud_flagged_order_ids(rows)),
            CarrierFake(tracking=tracking_events()),
        )


@dataclass
class Views:
    payment: AuditedPaymentPort
    fraud: AuditedFraudPort
    carrier: AuditedCarrierPort

    @classmethod
    def full(cls, fakes: Fakes, audit: AuditContext) -> Views:
        return cls(
            AuditedPaymentPort(fakes.payment, audit, allowed=PAYMENT_SCOPES),
            AuditedFraudPort(fakes.fraud, audit, allowed=FULL_FRAUD),
            AuditedCarrierPort(fakes.carrier, audit, allowed=FULL_CARRIER),
        )


@dataclass
class Truth:
    """What the driver itself saw the fakes return, kept apart from the audit log."""

    intent_id: str | None = None
    shipment_id: str | None = None
    tracking_number: str | None = None
    held: str | None = None
    escalated: bool = False
    actions: list[str] = field(default_factory=list)


def context(sink: AuditSink, order_id: str) -> AuditContext:
    return AuditContext(sink, correlation_id=f"corr-{order_id}", actor=ACTOR, clock=fixed_clock)


def hold(audit: AuditContext, truth: Truth, order_id: str, reason: str, escalate: bool) -> None:
    audit.record(
        "order.held", {"order_id": order_id, "reason": reason}, {"ok": True, "escalated": escalate}
    )
    truth.held, truth.escalated = reason, escalate
    truth.actions.append("order.held")


def run_order(order: Mapping[str, Any], fakes: Fakes, sink: AuditSink) -> Truth:
    """One order's flow, with fixed keys, so a replayed run is byte-identical."""
    order_id = order["order_id"]
    audit = context(sink, order_id)
    views = Views.full(fakes, audit)
    truth = Truth()
    step: Callable[[str], None] = truth.actions.append

    if order["exception"] == "stock_out":
        hold(audit, truth, order_id, "stock_out", escalate=True)
        return truth

    assessed = views.fraud.assess(
        idempotency_key=f"{order_id}:assess", order_id=order_id, shopper_id=order["shopper_id"]
    )
    step("fraud.assess")
    assert isinstance(assessed, Ok), assessed
    if assessed.value.decision is FraudDecision.REVIEW:
        # PRD §7 "Checkout" step 2: the Orders subagent stops on a high-risk result.
        hold(audit, truth, order_id, "fraud_flag", escalate=True)
        return truth

    authorized = views.payment.authorize(
        idempotency_key=f"{order_id}:authorize",
        payment_token=order["payment_token"],
        amount=amount_for(order),
        order_id=order_id,
    )
    step("payment.authorize")
    if isinstance(authorized, ToolError):
        assert authorized.code == "soft_decline", authorized
        hold(audit, truth, order_id, "soft_decline", escalate=False)
        return truth
    intent_id = authorized.value.intent_id
    truth.intent_id = intent_id

    # Ship before capture, and void on a failed shipment, so a held order is never charged (S5).
    # This order is the driver's choice only; #21 decides its own.
    shipped = views.carrier.create_shipment(
        idempotency_key=f"{order_id}:ship",
        order_id=order_id,
        region=order["region"],
        postal_code=order["shipping_postal_code"],
    )
    step("carrier.create_shipment")
    if isinstance(shipped, ToolError):
        assert shipped.code == "address_undeliverable", shipped
        voided = views.payment.void(idempotency_key=f"{order_id}:void", intent_id=intent_id)
        step("payment.void")
        assert isinstance(voided, Ok), voided
        hold(audit, truth, order_id, "address_error", escalate=True)
        return truth
    truth.shipment_id = shipped.value.shipment_id
    truth.tracking_number = shipped.value.tracking_number

    captured = views.payment.capture(idempotency_key=f"{order_id}:capture", intent_id=intent_id)
    step("payment.capture")
    assert isinstance(captured, Ok), captured

    tracked = views.carrier.track(shipped.value.tracking_number)
    step("carrier.track")
    assert isinstance(tracked, Ok), tracked
    return truth


def run_all(
    rows: list[dict[str, Any]] | None = None,
) -> tuple[Fakes, InMemoryAuditSink, dict[str, Truth]]:
    rows = orders() if rows is None else rows
    fakes = Fakes.for_orders(rows)
    sink = InMemoryAuditSink()
    truths = {row["order_id"]: run_order(row, fakes, sink) for row in rows}
    return fakes, sink, truths


def by_exception(rows: list[dict[str, Any]]) -> Iterator[tuple[str | None, dict[str, Any]]]:
    seen: set[str | None] = set()
    for row in rows:
        if row["exception"] not in seen:
            seen.add(row["exception"])
            yield row["exception"], row


def first_of(rows: list[dict[str, Any]], exception: str | None) -> dict[str, Any]:
    return next(row for row in rows if row["exception"] == exception)


def setup_captured(order: Mapping[str, Any], fakes: Fakes, sink: AuditSink, *, assess: bool) -> str:
    """A clean order authorized and captured; with `assess=False` it is never assessed, the
    valid-empty state (M4, R5) the 1,000-order flow never produces."""
    order_id = order["order_id"]
    audit = context(sink, order_id)
    views = Views.full(fakes, audit)
    if assess:
        assert isinstance(
            views.fraud.assess(
                idempotency_key=f"{order_id}:assess",
                order_id=order_id,
                shopper_id=order["shopper_id"],
            ),
            Ok,
        )
    authorized = views.payment.authorize(
        idempotency_key=f"{order_id}:authorize",
        payment_token=order["payment_token"],
        amount=amount_for(order),
        order_id=order_id,
    )
    assert isinstance(authorized, Ok), authorized
    intent: PaymentIntent = authorized.value
    assert isinstance(
        views.payment.capture(idempotency_key=f"{order_id}:capture", intent_id=intent.intent_id),
        Ok,
    )
    return intent.intent_id


def setup_unassessed(order: Mapping[str, Any], fakes: Fakes, sink: AuditSink) -> str:
    return setup_captured(order, fakes, sink, assess=False)
