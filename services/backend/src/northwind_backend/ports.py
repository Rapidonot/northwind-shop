"""The three ports the backend reaches external systems through (PAY-01 interface only, FOUND-3).

Adapters come later: the MCP and A2A payment routes (PAY-02, PAY-03) and the A2A fraud and carrier
agents. Here each port has a deterministic in-memory fake in `northwind_backend.fakes`.

Every state-changing call takes an `idempotency_key` and a `dry_run` flag (hard rule 9). A `dry_run`
call runs every check against current state and reports the outcome, changes nothing, and neither
reads nor records the key.

No port takes a card number. Agents see only tokens and payment intents (hard rule 1, PAY-05).

Hard rule 9 (row #21, decision 0014): `authorize` and `capture` take a required
`confirmation_ref`, issued by the portal control when the shopper confirms (CHK-02; a test fixture
until then), and `authorize` has a deterministic per-order limit. It is on the contract, not a layer
above it, so the PAY-01 contract suite checks it on every adapter. The rule is stated in
`northwind_backend.confirmations`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

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
        confirmation_ref: str,
        dry_run: bool = False,
    ) -> Result[PaymentIntent]:
        """Needs an unused confirmation for this order, currency and exact amount; uses it up."""
        ...

    def capture(
        self,
        *,
        idempotency_key: str,
        intent_id: str,
        confirmation_ref: str,
        amount: Money | None = None,
        dry_run: bool = False,
    ) -> Result[PaymentIntent]:
        """`amount=None` captures the whole authorization. Needs the confirmation that authorized
        this intent, and no other."""
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


# Row #21: the order store and the catalog's live tables, as stubs behind interfaces. `mcp-orders`
# (FOUND-3 rest) and `mcp-catalog` (#10b) replace the fakes later; the Orders agent sees only
# these protocols, through audited views.


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAYING = "paying"  # claimed by one run for payment (row #21, F1)
    CONFIRMED = "confirmed"
    ESCALATED = "escalated"


@dataclass(frozen=True, slots=True)
class OrderLine:
    product_id: str
    variant_id: str
    quantity: int


@dataclass(frozen=True, slots=True)
class Order:
    """One order as the store holds it. `payment_token` and `shipping_postal_code` never reach a
    model or the audit: the audit writes only the allow-listed fields of an `Order`."""

    order_id: str
    shopper_id: str
    region: str
    lines: tuple[OrderLine, ...]
    payment_token: str = field(repr=False)  # re-review N2: never in a repr or a log
    shipping_postal_code: str = field(repr=False)
    status: OrderStatus = OrderStatus.PENDING
    intent_id: str | None = None
    amount_minor: int = 0
    currency: str | None = None
    escalation: str | None = None
    address_on_file: bool = False
    dry_run: bool = False
    # The run that holds the payment claim: a code-only identity, never in model input or the
    # audit's allow-list (F1, the user's Option 1).
    claimed_by: str | None = field(default=None, repr=False)
    # What the claim owner last reported of its intent (F1): status and captured minor units.
    payment_status: str | None = None
    captured_minor: int = 0


def claim_payment_view(order: Order, claim: str) -> dict[str, Any]:
    """The claim and any payment on it, as an escalation context shows them (F1)."""
    return {
        "status": order.payment_status or "payment_claimed",
        "claim": claim,
        "intent_id": order.intent_id,
        "authorized_minor": order.amount_minor if order.intent_id else None,
        "captured_minor": order.captured_minor,
        "currency": order.currency,
    }


@dataclass(frozen=True, slots=True)
class PriceQuote:
    variant_id: str
    region: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class StockLevel:
    variant_id: str
    region: str
    on_hand: int


@runtime_checkable
class CatalogPort(Protocol):
    """Live price and stock, never the index (hard rule 2)."""

    def price(self, variant_id: str, region: str) -> Result[PriceQuote]: ...

    def stock(self, variant_id: str, region: str) -> Result[StockLevel]: ...


@runtime_checkable
class OrderStorePort(Protocol):
    def get(self, order_id: str) -> Result[Order]: ...

    def use_address_on_file(
        self, *, idempotency_key: str, order_id: str, dry_run: bool = False
    ) -> Result[Order]:
        """Replaces an undeliverable delivery address with the shopper's address on file, if that
        one is in the order's region and deliverable."""
        ...

    def confirm(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        intent_id: str,
        amount: Money,
        dry_run: bool = False,
    ) -> Result[Order]:
        """One confirmed order per order ID, whatever the key (CHK-05)."""
        ...

    def claim_payment(
        self,
        *,
        order_id: str,
        run_id: str,
        payment: PaymentIntent | None = None,
        dry_run: bool = False,
    ) -> Result[Order]:
        """A compare-and-set on the order's state, never resolved through the idempotency store:
        duplicates share the order-ID keys, so a replay would hand the claim to both. With no
        intent, it claims: pending becomes paying, owned by `run_id`; the owner claiming again gets
        `Ok`; anyone else gets `claimed_by_another_run`. With a payment, the owner reports its
        intent's state on the claim (after authorize and after capture), which an escalation of the
        order then shows."""
        ...

    def escalate(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        reason: str,
        context: Mapping[str, Any],
        dry_run: bool = False,
    ) -> Result[Order]:
        """Queues the order for a person with its context. `context` is kept, not hashed: a
        same-key replay with the same reason returns the first result."""
        ...
