"""PaymentPort fake (PAY-01 interface, hard rules 1 and 9).

It only ever holds tokens and payment intents. A token must match a strict pattern, and every string
argument is screened for card data before anything is kept.

Outcomes follow the sandbox token, so a test or a fixture picks one by choosing a token.

An order has at most one intent that isn't voided, so a retry that lost its key can't charge it
again (CHK-05). A voided intent can't be captured or refunded, so that also makes the cumulative
refund cap a per-order cap: a refund split into small parts can't pass it. The cap is per currency,
and a currency with no cap can't be refunded. It is the fake's own deterministic limit: the PAY-06
hook and its approval route are later work.

Hard rule 9's portal confirmation is not enforced here, and `authorize` and `capture` have no limit
beyond `MAX_AMOUNT_MINOR`. Whether that belongs on the port or on a named layer above it is open,
and has to be decided before the PAY-01 contract suite locks the behaviour in.

Scopes belong to the instance, and each instance has its own intents, so this can't yet give two
agents narrow views of one payment. The row that first wires an agent needs per-caller views, which
must share the intents, the idempotency store and the caps: the one-intent-per-order rule and the
refund cap hold only within one instance.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

from northwind_backend.errors import Ok, Result, ToolError, business, validation
from northwind_backend.fakes._base import (
    CARD_DATA_REFUSED,
    Effect,
    FakeService,
    card_data_in,
    check_money,
    check_order_id,
    matches,
)
from northwind_backend.ports import Money, PaymentIntent, PaymentStatus

PAYMENT_SCOPES = frozenset({"authorize", "capture", "void", "refund", "status"})
DEFAULT_REFUND_CAPS: Mapping[str, int] = MappingProxyType(
    {"USD": 15_000, "CAD": 20_000, "GBP": 12_000, "EUR": 14_000}
)

SANDBOX_APPROVED = "tok_sandbox_ok"
SANDBOX_SOFT_DECLINE = "tok_sandbox_soft_decline"
SANDBOX_HARD_DECLINE = "tok_sandbox_hard_decline"
DRY_RUN_INTENT_ID = "pi_dry_run"

_TOKEN = re.compile(r"(tok_[a-z]+(?:_[a-z]+)*?)(?:_\d{1,6})?", re.ASCII)
_INTENT = re.compile(r"pi_\d{6}", re.ASCII)

BAD_TOKEN = validation("invalid_token", "The payment token is malformed.")
UNKNOWN_TOKEN = validation("unknown_token", "The payment token is not a known sandbox token.")
BAD_INTENT = validation("invalid_intent", "The payment intent ID is malformed.")
UNKNOWN_INTENT = validation("unknown_intent", "There is no such payment intent.")
CURRENCY_MISMATCH = validation("currency_mismatch", "The currency differs from the payment's.")
SOFT_DECLINE = business("soft_decline", "The issuer declined this attempt. Try another method.")
HARD_DECLINE = business("hard_decline", "The payment method was declined.")
ALREADY_CAPTURED = business("already_captured", "This payment has already been captured.")
INTENT_VOIDED = business("intent_voided", "This payment has been voided.")
NOT_CAPTURED = business("not_captured", "This payment has not been captured.")
OVER_AUTHORIZED = business("over_authorized", "The amount is more than was authorized.")
OVER_CAPTURED = business("refund_over_captured", "The refund is more than was captured.")
ORDER_ALREADY_AUTHORIZED = business(
    "order_already_authorized", "This order already has a payment, so no new one was started."
)
OVER_CAP = business("refund_over_cap", "The refund is over the automatic cap and needs approval.")
NO_CAP = business("no_refund_cap", "No refund cap is set for this currency.")


class PaymentFake(FakeService):
    def __init__(
        self,
        *,
        refund_caps: Mapping[str, int] = DEFAULT_REFUND_CAPS,
        scopes: frozenset[str] = PAYMENT_SCOPES,
    ) -> None:
        super().__init__(scopes)
        self._caps = MappingProxyType(dict(refund_caps))
        self._intents: dict[str, PaymentIntent] = {}

    def authorize(
        self,
        *,
        idempotency_key: str,
        payment_token: str,
        amount: Money,
        order_id: str,
        dry_run: bool = False,
    ) -> Result[PaymentIntent]:
        refused = (
            self._admit("authorize", idempotency_key, payment_token, order_id)
            or _check_token(payment_token)
            or check_order_id(order_id)
            or check_money(amount)
        )
        if refused is not None:
            return refused

        def effect(dry: bool) -> Result[PaymentIntent]:
            declined = _decline_for(payment_token)
            if declined is not None:
                return declined
            if self._has_live_intent(order_id):
                return ORDER_ALREADY_AUTHORIZED
            intent_id = DRY_RUN_INTENT_ID if dry else f"pi_{len(self._intents) + 1:06d}"
            intent = PaymentIntent(
                intent_id=intent_id,
                order_id=order_id,
                status=PaymentStatus.AUTHORIZED,
                currency=amount.currency,
                authorized_minor=amount.amount_minor,
                dry_run=dry,
            )
            if dry:
                return Ok(intent)
            return self._save("authorize", intent, amount.amount_minor)

        request = {
            "payment_token": payment_token,
            "order_id": order_id,
            "amount_minor": amount.amount_minor,
            "currency": amount.currency,
        }
        return self._commit("authorize", idempotency_key, request, dry_run, effect)

    def capture(
        self,
        *,
        idempotency_key: str,
        intent_id: str,
        amount: Money | None = None,
        dry_run: bool = False,
    ) -> Result[PaymentIntent]:
        refused = (
            self._admit("capture", idempotency_key, intent_id)
            or _check_intent(intent_id)
            or (None if amount is None else check_money(amount))
        )
        if refused is not None:
            return refused

        def effect(dry: bool) -> Result[PaymentIntent]:
            intent = self._intents.get(intent_id)
            if intent is None:
                return UNKNOWN_INTENT
            if intent.status is PaymentStatus.VOIDED:
                return INTENT_VOIDED
            # Whatever the key: a second key must not be able to capture this intent again.
            if intent.status is not PaymentStatus.AUTHORIZED:
                return ALREADY_CAPTURED
            captured = intent.authorized_minor if amount is None else amount.amount_minor
            if amount is not None and amount.currency != intent.currency:
                return CURRENCY_MISMATCH
            if captured > intent.authorized_minor:
                return OVER_AUTHORIZED
            updated = replace(intent, status=PaymentStatus.CAPTURED, captured_minor=captured)
            return self._apply("capture", updated, captured, dry)

        request = {
            "intent_id": intent_id,
            "amount_minor": None if amount is None else amount.amount_minor,
            "currency": None if amount is None else amount.currency,
        }
        return self._commit("capture", idempotency_key, request, dry_run, effect)

    def void(
        self, *, idempotency_key: str, intent_id: str, dry_run: bool = False
    ) -> Result[PaymentIntent]:
        refused = self._admit("void", idempotency_key, intent_id) or _check_intent(intent_id)
        if refused is not None:
            return refused

        def effect(dry: bool) -> Result[PaymentIntent]:
            intent = self._intents.get(intent_id)
            if intent is None:
                return UNKNOWN_INTENT
            if intent.status is PaymentStatus.VOIDED:
                return INTENT_VOIDED
            if intent.status is not PaymentStatus.AUTHORIZED:
                return ALREADY_CAPTURED
            return self._apply("void", replace(intent, status=PaymentStatus.VOIDED), 0, dry)

        return self._commit("void", idempotency_key, {"intent_id": intent_id}, dry_run, effect)

    def refund(
        self, *, idempotency_key: str, intent_id: str, amount: Money, dry_run: bool = False
    ) -> Result[PaymentIntent]:
        refused = (
            self._admit("refund", idempotency_key, intent_id)
            or _check_intent(intent_id)
            or check_money(amount)
        )
        if refused is not None:
            return refused

        def effect(dry: bool) -> Result[PaymentIntent]:
            intent = self._intents.get(intent_id)
            if intent is None:
                return UNKNOWN_INTENT
            if intent.status in (PaymentStatus.AUTHORIZED, PaymentStatus.VOIDED):
                return NOT_CAPTURED
            if amount.currency != intent.currency:
                return CURRENCY_MISMATCH
            total = intent.refunded_minor + amount.amount_minor
            if total > intent.captured_minor:
                return OVER_CAPTURED
            cap = self._caps.get(intent.currency)
            if cap is None:
                return NO_CAP
            if total > cap:
                return OVER_CAP
            status = (
                PaymentStatus.REFUNDED
                if total == intent.captured_minor
                else PaymentStatus.PARTIALLY_REFUNDED
            )
            updated = replace(intent, status=status, refunded_minor=total)
            return self._apply("refund", updated, amount.amount_minor, dry)

        request = {
            "intent_id": intent_id,
            "amount_minor": amount.amount_minor,
            "currency": amount.currency,
        }
        return self._commit("refund", idempotency_key, request, dry_run, effect)

    def status(self, intent_id: str) -> Result[PaymentIntent]:
        refused = self._reachable("status")
        if refused is not None:
            return refused
        if card_data_in(intent_id):
            return CARD_DATA_REFUSED
        bad = _check_intent(intent_id)
        if bad is not None:
            return bad
        intent = self._intents.get(intent_id)
        return UNKNOWN_INTENT if intent is None else Ok(intent)

    def _has_live_intent(self, order_id: str) -> bool:
        return any(
            intent.order_id == order_id and intent.status is not PaymentStatus.VOIDED
            for intent in self._intents.values()
        )

    def _apply(
        self, operation: str, intent: PaymentIntent, amount_minor: int, dry: bool
    ) -> Result[PaymentIntent]:
        if dry:
            return Ok(replace(intent, dry_run=True))
        return self._save(operation, intent, amount_minor)

    def _save(self, operation: str, intent: PaymentIntent, amount_minor: int) -> Ok[PaymentIntent]:
        self._intents[intent.intent_id] = intent
        self._effects.append(Effect(operation, intent.intent_id, amount_minor))
        return Ok(intent)


def _check_token(token: object) -> ToolError | None:
    return None if matches(_TOKEN, token) else BAD_TOKEN


def _check_intent(intent_id: object) -> ToolError | None:
    return None if matches(_INTENT, intent_id) else BAD_INTENT


def _decline_for(token: str) -> ToolError | None:
    match = _TOKEN.fullmatch(token)
    base = match.group(1) if match else ""
    if base == SANDBOX_APPROVED:
        return None
    if base == SANDBOX_SOFT_DECLINE:
        return SOFT_DECLINE
    return HARD_DECLINE if base == SANDBOX_HARD_DECLINE else UNKNOWN_TOKEN
