"""Audited, scoped per-caller views over one shared fake (BE-08, hard rule 8; row #20).

Each view binds one caller's `AuditContext` and an allow-list of operations. Every protocol method
goes through `_run`, which writes the attempt, runs the call (or refuses it with `NOT_PERMITTED`),
and writes the one outcome. If the attempt can't be written the call doesn't run
(`AUDIT_UNAVAILABLE`); if the outcome can't be written the caller gets `AUDIT_INCOMPLETE`, and a
same-key retry settles it.

Views share the fake instance they wrap, so the one-payment-per-order rule, the idempotency store
and the refund cap hold across callers. This closes FOUND-3 open medium (1).

Agents get the read-only sets below, the Orders agent's placement task (row #21) gets the named
placement sets, in `agents/orders_place.py` only, and the Care agent (row #26) gets `CARE_READ` (a
read) and `CASE_CREATE` (its one write, made by code, never by its model), in `agents/care.py`
only. All are pinned in `test_be08_views.py` (row #26's change approved by the user, stop 5), with
the AST pin that keeps every money method name out of `src` except here, in `fakes/`, and
`authorize` and `capture` in `orders_place.py` (L2, N3; the user's approval of row #21's plan,
stop 5). No agent set holds `void` or `refund` (hard rule 8, PRD §7). This is the only module that
maps port method names to audit action names (R7).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from northwind_backend.audit import AuditContext, AuditWriteError, outcome_ref
from northwind_backend.cases import Case, CaseDraft, CasePort
from northwind_backend.errors import AUDIT_INCOMPLETE, AUDIT_UNAVAILABLE, UNEXPECTED, Result
from northwind_backend.fakes._base import NOT_PERMITTED
from northwind_backend.inventory.model import InventoryPort, Line, Reservation
from northwind_backend.ports import (
    CarrierPort,
    CatalogPort,
    FraudAssessment,
    FraudPort,
    Money,
    Order,
    OrderStorePort,
    PaymentIntent,
    PaymentPort,
    PriceQuote,
    Shipment,
    StockLevel,
    Tracking,
)

log = logging.getLogger(__name__)

PAYMENT_READ_ONLY = frozenset({"status"})
FRAUD_READ_ONLY = frozenset({"read"})
CARRIER_READ_ONLY = frozenset({"track"})

# The Orders agent's placement task (row #21). No void, no refund, no carrier. Narrowed to what the
# code calls (security review F6, the user's decision): no payment status, no fraud read.
PAYMENT_PLACE = frozenset({"authorize", "capture"})
FRAUD_PLACE = frozenset({"assess"})
CATALOG_READ = frozenset({"price", "stock"})
# `claim_payment`: the payment claim (F1, the user's Option 1; pin change approved, stop 5).
ORDERS_PLACE = frozenset({"get", "use_address_on_file", "confirm", "escalate", "claim_payment"})

# Row #25 (BE-04): placement holds stock before payment, commits it before capture and releases it
# when the order is escalated. Code calls these, never the model; no `available` read.
INVENTORY_PLACE = frozenset({"reserve", "commit", "release"})

# The Care agent (row #26, BE-07; PRD §7 Care row). Its model's one data tool reads the order. Code,
# never the model, queues the case. No catalog read (the user, row #26).
CARE_READ = frozenset({"get"})
CASE_CREATE = frozenset({"create_case"})


def _amount(amount: Money | None) -> dict[str, object]:
    if amount is None:
        return {"amount_minor": None, "currency": None}
    return {"amount_minor": amount.amount_minor, "currency": amount.currency}


class _View:
    def __init__(self, audit: AuditContext, allowed: frozenset[str]) -> None:
        self._audit = audit
        self._allowed = frozenset(allowed)

    def _run[T](
        self,
        scope: str,
        action: str,
        inputs: Mapping[str, object],
        call: Callable[[], Result[T]],
    ) -> Result[T]:
        try:
            action_id = self._audit.attempt(action, inputs)
        except AuditWriteError:
            log.error("audit attempt not written; %s not run", action)
            return AUDIT_UNAVAILABLE
        outcome: dict[str, Any]
        result: Result[T]
        if scope not in self._allowed:
            result = NOT_PERMITTED
            outcome = outcome_ref(result)
        else:
            try:
                result = call()
                outcome = outcome_ref(result)
            except Exception as error:  # noqa: BLE001 - its text must go nowhere (hard rule 1)
                log.error("%s failed: %s", action, type(error).__name__)
                result = UNEXPECTED
                outcome = {**outcome_ref(result), "crashed": type(error).__name__}
        # A failed intent-keyed call names no order; the reconstruction joins it through
        # `intent_id` from the authorize outcome, so it is still found (M1).
        try:
            self._audit.outcome(action_id, action, inputs, outcome)
        except AuditWriteError:
            log.error("audit outcome not written for %s", action)
            return AUDIT_INCOMPLETE
        return result


class AuditedPaymentPort(_View):
    def __init__(self, fake: PaymentPort, audit: AuditContext, *, allowed: frozenset[str]) -> None:
        super().__init__(audit, allowed)
        self._fake = fake

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
        inputs = {
            "idempotency_key": idempotency_key,
            "order_id": order_id,
            "payment_token": payment_token,
            **_amount(amount),
            "confirmation_ref": confirmation_ref,  # written as type and length only
            "dry_run": dry_run,
        }
        return self._run(
            "authorize",
            "payment.authorize",
            inputs,
            lambda: self._fake.authorize(
                idempotency_key=idempotency_key,
                payment_token=payment_token,
                amount=amount,
                order_id=order_id,
                confirmation_ref=confirmation_ref,
                dry_run=dry_run,
            ),
        )

    def capture(
        self,
        *,
        idempotency_key: str,
        intent_id: str,
        confirmation_ref: str,
        amount: Money | None = None,
        dry_run: bool = False,
    ) -> Result[PaymentIntent]:
        inputs = {
            "idempotency_key": idempotency_key,
            "intent_id": intent_id,
            **_amount(amount),
            "confirmation_ref": confirmation_ref,
            "dry_run": dry_run,
        }
        return self._run(
            "capture",
            "payment.capture",
            inputs,
            lambda: self._fake.capture(
                idempotency_key=idempotency_key,
                intent_id=intent_id,
                confirmation_ref=confirmation_ref,
                amount=amount,
                dry_run=dry_run,
            ),
        )

    def void(
        self, *, idempotency_key: str, intent_id: str, dry_run: bool = False
    ) -> Result[PaymentIntent]:
        inputs = {"idempotency_key": idempotency_key, "intent_id": intent_id, "dry_run": dry_run}
        return self._run(
            "void",
            "payment.void",
            inputs,
            lambda: self._fake.void(
                idempotency_key=idempotency_key, intent_id=intent_id, dry_run=dry_run
            ),
        )

    def refund(
        self, *, idempotency_key: str, intent_id: str, amount: Money, dry_run: bool = False
    ) -> Result[PaymentIntent]:
        inputs = {
            "idempotency_key": idempotency_key,
            "intent_id": intent_id,
            **_amount(amount),
            "dry_run": dry_run,
        }
        return self._run(
            "refund",
            "payment.refund",
            inputs,
            lambda: self._fake.refund(
                idempotency_key=idempotency_key, intent_id=intent_id, amount=amount, dry_run=dry_run
            ),
        )

    def status(self, intent_id: str) -> Result[PaymentIntent]:
        return self._run(
            "status",
            "payment.status",
            {"intent_id": intent_id},
            lambda: self._fake.status(intent_id),
        )


class AuditedFraudPort(_View):
    def __init__(self, fake: FraudPort, audit: AuditContext, *, allowed: frozenset[str]) -> None:
        super().__init__(audit, allowed)
        self._fake = fake

    def assess(
        self, *, idempotency_key: str, order_id: str, shopper_id: str, dry_run: bool = False
    ) -> Result[FraudAssessment]:
        inputs = {
            "idempotency_key": idempotency_key,
            "order_id": order_id,
            "shopper_id": shopper_id,
            "dry_run": dry_run,
        }
        return self._run(
            "assess",
            "fraud.assess",
            inputs,
            lambda: self._fake.assess(
                idempotency_key=idempotency_key,
                order_id=order_id,
                shopper_id=shopper_id,
                dry_run=dry_run,
            ),
        )

    def assessments_for(self, order_id: str) -> Result[tuple[FraudAssessment, ...]]:
        return self._run(
            "read",
            "fraud.read",
            {"order_id": order_id},
            lambda: self._fake.assessments_for(order_id),
        )


class AuditedCarrierPort(_View):
    def __init__(self, fake: CarrierPort, audit: AuditContext, *, allowed: frozenset[str]) -> None:
        super().__init__(audit, allowed)
        self._fake = fake

    def create_shipment(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        region: str,
        postal_code: str,
        service: str = "standard",
        dry_run: bool = False,
    ) -> Result[Shipment]:
        inputs = {
            "idempotency_key": idempotency_key,
            "order_id": order_id,
            "region": region,
            "postal_code": postal_code,
            "service": service,
            "dry_run": dry_run,
        }
        return self._run(
            "create_shipment",
            "carrier.create_shipment",
            inputs,
            lambda: self._fake.create_shipment(
                idempotency_key=idempotency_key,
                order_id=order_id,
                region=region,
                postal_code=postal_code,
                service=service,
                dry_run=dry_run,
            ),
        )

    def track(self, tracking_number: str) -> Result[Tracking]:
        return self._run(
            "track",
            "carrier.track",
            {"tracking_number": tracking_number},
            lambda: self._fake.track(tracking_number),
        )


class AuditedCatalogPort(_View):
    def __init__(self, fake: CatalogPort, audit: AuditContext, *, allowed: frozenset[str]) -> None:
        super().__init__(audit, allowed)
        self._fake = fake

    def price(self, variant_id: str, region: str) -> Result[PriceQuote]:
        return self._run(
            "price",
            "catalog.price",
            {"variant_id": variant_id, "region": region},
            lambda: self._fake.price(variant_id, region),
        )

    def stock(self, variant_id: str, region: str) -> Result[StockLevel]:
        return self._run(
            "stock",
            "catalog.stock",
            {"variant_id": variant_id, "region": region},
            lambda: self._fake.stock(variant_id, region),
        )


class AuditedInventoryPort(_View):
    """Row #25 (BE-04). Inputs by reference: the key, order, region, line count and `dry_run`;
    never the postal code or the lines themselves."""

    def __init__(
        self, fake: InventoryPort, audit: AuditContext, *, allowed: frozenset[str]
    ) -> None:
        super().__init__(audit, allowed)
        self._fake = fake

    def reserve(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        region: str,
        postal_code: str,
        lines: Sequence[Line],
        dry_run: bool = False,
    ) -> Result[Reservation]:
        inputs = {
            "idempotency_key": idempotency_key,
            "order_id": order_id,
            "region": region,
            "lines": len(lines),
            "dry_run": dry_run,
        }
        return self._run(
            "reserve",
            "inventory.reserve",
            inputs,
            lambda: self._fake.reserve(
                idempotency_key=idempotency_key,
                order_id=order_id,
                region=region,
                postal_code=postal_code,
                lines=lines,
                dry_run=dry_run,
            ),
        )

    def commit(self, *, idempotency_key: str) -> Result[Reservation]:
        return self._run(
            "commit",
            "inventory.commit",
            {"idempotency_key": idempotency_key},
            lambda: self._fake.commit(idempotency_key=idempotency_key),
        )

    def release(self, *, idempotency_key: str) -> Result[Reservation]:
        return self._run(
            "release",
            "inventory.release",
            {"idempotency_key": idempotency_key},
            lambda: self._fake.release(idempotency_key=idempotency_key),
        )

    def available(self, variant_id: str, node: str) -> Result[int]:
        return self._run(
            "available",
            "inventory.available",
            {"variant_id": variant_id},
            lambda: self._fake.available(variant_id, node),
        )


class AuditedOrderStore(_View):
    def __init__(
        self, fake: OrderStorePort, audit: AuditContext, *, allowed: frozenset[str]
    ) -> None:
        super().__init__(audit, allowed)
        self._fake = fake

    def get(self, order_id: str) -> Result[Order]:
        return self._run(
            "get", "order.get", {"order_id": order_id}, lambda: self._fake.get(order_id)
        )

    def use_address_on_file(
        self, *, idempotency_key: str, order_id: str, dry_run: bool = False
    ) -> Result[Order]:
        inputs = {"idempotency_key": idempotency_key, "order_id": order_id, "dry_run": dry_run}
        return self._run(
            "use_address_on_file",
            "order.address_on_file",
            inputs,
            lambda: self._fake.use_address_on_file(
                idempotency_key=idempotency_key, order_id=order_id, dry_run=dry_run
            ),
        )

    def confirm(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        intent_id: str,
        amount: Money,
        dry_run: bool = False,
    ) -> Result[Order]:
        inputs = {
            "idempotency_key": idempotency_key,
            "order_id": order_id,
            "intent_id": intent_id,
            **_amount(amount),
            "dry_run": dry_run,
        }
        return self._run(
            "confirm",
            "order.confirm",
            inputs,
            lambda: self._fake.confirm(
                idempotency_key=idempotency_key,
                order_id=order_id,
                intent_id=intent_id,
                amount=amount,
                dry_run=dry_run,
            ),
        )

    def claim_payment(
        self,
        *,
        order_id: str,
        run_id: str,
        payment: PaymentIntent | None = None,
        dry_run: bool = False,
    ) -> Result[Order]:
        # `run_id` is a code-only identity: written as type and length, never as a value.
        inputs = {
            "order_id": order_id,
            "run_id": run_id,
            "intent_id": None if payment is None else payment.intent_id,
            "dry_run": dry_run,
        }
        return self._run(
            "claim_payment",
            "order.claim_payment",
            inputs,
            lambda: self._fake.claim_payment(
                order_id=order_id, run_id=run_id, payment=payment, dry_run=dry_run
            ),
        )

    def escalate(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        reason: str,
        context: Mapping[str, Any],
        dry_run: bool = False,
    ) -> Result[Order]:
        # The context goes to the store, not the audit: the audit has the reason and the events
        # the context was built from.
        inputs = {
            "idempotency_key": idempotency_key,
            "order_id": order_id,
            "reason": reason,
            "dry_run": dry_run,
        }
        return self._run(
            "escalate",
            "order.escalate",
            inputs,
            lambda: self._fake.escalate(
                idempotency_key=idempotency_key,
                order_id=order_id,
                reason=reason,
                context=context,
                dry_run=dry_run,
            ),
        )


class AuditedCaseQueue(_View):
    """The case queue (row #26). The audit gets the key, the order and the code-derived root cause;
    the summary is never written (its type and length are, in the outcome)."""

    def __init__(self, fake: CasePort, audit: AuditContext, *, allowed: frozenset[str]) -> None:
        super().__init__(audit, allowed)
        self._fake = fake

    def create_case(
        self, *, idempotency_key: str, case: CaseDraft, dry_run: bool = False
    ) -> Result[Case]:
        inputs = {
            "idempotency_key": idempotency_key,
            "order_id": case.order_id,
            "root_cause": case.root_cause,
            "flags": case.flags,
            "dry_run": dry_run,
        }
        return self._run(
            "create_case",
            "case.create",
            inputs,
            lambda: self._fake.create_case(
                idempotency_key=idempotency_key, case=case, dry_run=dry_run
            ),
        )
