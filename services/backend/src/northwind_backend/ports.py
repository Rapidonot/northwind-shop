"""The three ports the backend reaches external systems through (PAY-01 interface only, FOUND-3).

Adapters come later: the MCP and A2A payment routes (PAY-02, PAY-03) and the A2A fraud and carrier
agents. Here each port has a deterministic in-memory fake in `northwind_backend.fakes`.

Every state-changing call takes an `idempotency_key` and a `dry_run` flag (hard rule 9). A `dry_run`
call runs every check against current state and reports the outcome, changes nothing, and neither
reads nor records the key.

No port takes a card number. Agents see only tokens and payment intents (hard rule 1, PAY-05).

No port carries a confirmation reference. Hard rule 9 needs a portal confirmation for money to
move, so before the PAY-01 contract suite either this contract gains one or a named layer above it
enforces that. Nothing reaches these ports yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from northwind_backend.errors import Result


@dataclass(frozen=True, slots=True)
class Money:
    """Integer minor units, so no amount ever rounds."""

    amount_minor: int
    currency: str


class PaymentStatus(StrEnum):
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    VOIDED = "voided"


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    intent_id: str
    order_id: str
    status: PaymentStatus
    currency: str
    authorized_minor: int
    captured_minor: int = 0
    refunded_minor: int = 0
    dry_run: bool = False


class FraudDecision(StrEnum):
    ALLOW = "allow"
    REVIEW = "review"


@dataclass(frozen=True, slots=True)
class FraudAssessment:
    assessment_id: str
    order_id: str
    shopper_id: str
    score: int
    decision: FraudDecision
    reasons: tuple[str, ...] = ()
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class Shipment:
    shipment_id: str
    order_id: str
    carrier: str
    tracking_number: str
    service: str
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class TrackingEvent:
    on: str
    status: str


@dataclass(frozen=True, slots=True)
class Tracking:
    """`events` is empty for a label with no scans yet: a valid result, not a failure."""

    tracking_number: str
    carrier: str
    events: tuple[TrackingEvent, ...] = ()


@runtime_checkable
class PaymentPort(Protocol):
    def authorize(
        self,
        *,
        idempotency_key: str,
        payment_token: str,
        amount: Money,
        order_id: str,
        dry_run: bool = False,
    ) -> Result[PaymentIntent]: ...

    def capture(
        self,
        *,
        idempotency_key: str,
        intent_id: str,
        amount: Money | None = None,
        dry_run: bool = False,
    ) -> Result[PaymentIntent]:
        """`amount=None` captures the whole authorization."""
        ...

    def void(
        self, *, idempotency_key: str, intent_id: str, dry_run: bool = False
    ) -> Result[PaymentIntent]: ...

    def refund(
        self, *, idempotency_key: str, intent_id: str, amount: Money, dry_run: bool = False
    ) -> Result[PaymentIntent]: ...

    def status(self, intent_id: str) -> Result[PaymentIntent]: ...


@runtime_checkable
class FraudPort(Protocol):
    def assess(
        self, *, idempotency_key: str, order_id: str, shopper_id: str, dry_run: bool = False
    ) -> Result[FraudAssessment]: ...

    def assessments_for(self, order_id: str) -> Result[tuple[FraudAssessment, ...]]:
        """An order not yet assessed gives `Ok(())`."""
        ...


@runtime_checkable
class CarrierPort(Protocol):
    def create_shipment(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        region: str,
        postal_code: str,
        service: str = "standard",
        dry_run: bool = False,
    ) -> Result[Shipment]: ...

    def track(self, tracking_number: str) -> Result[Tracking]: ...
