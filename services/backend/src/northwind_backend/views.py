"""Audited, scoped per-caller views over one shared fake (BE-08, hard rule 8; row #20).

Each view binds one caller's `AuditContext` and an allow-list of operations. Every protocol method
goes through `_run`, which writes the attempt, runs the call (or refuses it with `NOT_PERMITTED`),
and writes the one outcome. If the attempt can't be written the call doesn't run
(`AUDIT_UNAVAILABLE`); if the outcome can't be written the caller gets `AUDIT_INCOMPLETE`, and a
same-key retry settles it.

Views share the fake instance they wrap, so the one-payment-per-order rule, the idempotency store
and the refund cap hold across callers. This closes FOUND-3 open medium (1).

Agents only ever get the read-only sets below; pinned in `test_be08_views.py`, with the AST pin that
keeps every money method name out of `src` except here and in `fakes/` (L2, N3). This is the only
module that maps port method names to audit action names (R7).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from northwind_backend.audit import AuditContext, AuditWriteError, outcome_ref
from northwind_backend.errors import AUDIT_INCOMPLETE, AUDIT_UNAVAILABLE, UNEXPECTED, Result
from northwind_backend.fakes._base import NOT_PERMITTED
from northwind_backend.ports import (
    CarrierPort,
    FraudAssessment,
    FraudPort,
    Money,
    PaymentIntent,
    PaymentPort,
    Shipment,
    Tracking,
)

log = logging.getLogger(__name__)

PAYMENT_READ_ONLY = frozenset({"status"})
FRAUD_READ_ONLY = frozenset({"read"})
CARRIER_READ_ONLY = frozenset({"track"})


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
        dry_run: bool = False,
    ) -> Result[PaymentIntent]:
        inputs = {
            "idempotency_key": idempotency_key,
            "order_id": order_id,
            "payment_token": payment_token,
            **_amount(amount),
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
                dry_run=dry_run,
            ),
        )

    def capture(
        self,
        *,
        idempotency_key: str,
        intent_id: str,
        amount: Money | None = None,
        dry_run: bool = False,
    ) -> Result[PaymentIntent]:
        inputs = {
            "idempotency_key": idempotency_key,
            "intent_id": intent_id,
            **_amount(amount),
            "dry_run": dry_run,
        }
        return self._run(
            "capture",
            "payment.capture",
            inputs,
            lambda: self._fake.capture(
                idempotency_key=idempotency_key, intent_id=intent_id, amount=amount, dry_run=dry_run
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
