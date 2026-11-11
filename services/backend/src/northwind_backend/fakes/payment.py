"""PaymentPort fake (PAY-01 interface, hard rules 1 and 9).

It only ever holds tokens and payment intents. A token must match a strict pattern, and every string
argument is screened for card data before anything is kept.

Outcomes follow the sandbox token, so a test or a fixture picks one by choosing a token.

An order has at most one intent that isn't voided, so a retry that lost its key can't charge it
again (CHK-05). A voided intent can't be captured or refunded, so that also makes the cumulative
refund cap a per-order cap: a refund split into small parts can't pass it. The cap is per currency,
and a currency with no cap can't be refunded. It is the fake's own deterministic limit: the PAY-06
hook and its approval route are later work.

Hard rule 9 (row #21, decision 0014): `authorize` and `capture` need a confirmation reference from
the portal control (`northwind_backend.confirmations`, whose rule is stated there), and
`authorize` has a deterministic per-order limit per currency (`DEFAULT_ORDER_LIMITS`, placeholders
accepted by the user). Both are checked inside the idempotent effect.

Scopes belong to the instance; everything else lives on a `PaymentState`, which several instances
can share (P3): the intents, the idempotency store and its lock, the effects, the caps, the limits
and the confirmation book. `PaymentFake()` makes a private state.

Soft declines: by default a soft-decline token declines every attempt. With
`soft_decline_retry_approves`, the issuer approves the next attempt for the same order under a new
key, which is how the Orders agent's routine retry succeeds on the fakes (row #21, Q1).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

from northwind_backend.confirmations import ConfirmationBook, check_reference
from northwind_backend.errors import Ok, Result, ToolError, business, validation
from northwind_backend.fakes._base import (
    CARD_DATA_REFUSED,
    Effect,
    FakeService,
    FakeState,
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
# Above every generated order's live-price total (row #21 plan, Q2): placeholders, not final values.
DEFAULT_ORDER_LIMITS: Mapping[str, int] = MappingProxyType(
    {"USD": 200_000, "CAD": 270_000, "GBP": 160_000, "EUR": 210_000}
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
OVER_ORDER_LIMIT = business(
    "over_order_limit", "The amount is over the per-order limit and needs a person to approve it."
)
NO_ORDER_LIMIT = business("no_order_limit", "No per-order limit is set for this currency.")


class PaymentState(FakeState):
    """The provider's side of the sandbox, shared by every `PaymentFake` built on it."""

    def __init__(
        self,
        *,
        refund_caps: Mapping[str, int] = DEFAULT_REFUND_CAPS,
        order_limits: Mapping[str, int] = DEFAULT_ORDER_LIMITS,
        soft_decline_retry_approves: bool = False,
    ) -> None:
        super().__init__()
        self.caps = MappingProxyType(dict(refund_caps))
        self.order_limits = MappingProxyType(dict(order_limits))
        self.soft_decline_retry_approves = soft_decline_retry_approves
        self.intents: dict[str, PaymentIntent] = {}
        self.confirmations = ConfirmationBook()
        self.authorized_with: dict[str, str] = {}  # intent ID -> the reference that authorized it
        self.soft_declined: set[str] = set()  # order IDs with a recorded soft decline

    def confirmation_of(self, intent_id: str) -> str | None:
        """The issuer's side and tests only; never handed to an agent or a model."""
        return self.authorized_with.get(intent_id)


class PaymentFake(FakeService):
    def __init__(
        self,
        *,
        state: PaymentState | None = None,
        refund_caps: Mapping[str, int] | None = None,
        scopes: frozenset[str] = PAYMENT_SCOPES,
    ) -> None:
        if state is not None and refund_caps is not None:
            raise ValueError("refund caps belong to the state; set them there")
        if state is None:
            state = PaymentState(refund_caps=refund_caps or DEFAULT_REFUND_CAPS)
        super().__init__(scopes, state)
        self.state = state
        self._caps = state.caps
        self._intents = state.intents

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
        refused = (
            self._admit("authorize", idempotency_key, payment_token, order_id, confirmation_ref)
            or _check_token(payment_token)
            or check_order_id(order_id)
            or check_money(amount)
            or check_reference(confirmation_ref)
        )
        if refused is not None:
            return refused

        def effect(dry: bool) -> Result[PaymentIntent]:
            limit = self.state.order_limits.get(amount.currency)
            if limit is None:
                return NO_ORDER_LIMIT
            if amount.amount_minor > limit:
                return OVER_ORDER_LIMIT
            unconfirmed = self.state.confirmations.check_authorize(
                confirmation_ref, order_id, amount
            )
            if unconfirmed is not None:
                return unconfirmed
            declined = self._decline(payment_token, order_id, dry)
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
            self.state.confirmations.use(confirmation_ref)
            self.state.authorized_with[intent_id] = confirmation_ref
            return self._save("authorize", intent, amount.amount_minor)

        request = {
            "payment_token": payment_token,
            "order_id": order_id,
            "amount_minor": amount.amount_minor,
            "currency": amount.currency,
            "confirmation_ref": confirmation_ref,
        }
        return self._commit("authorize", idempotency_key, request, dry_run, effect)

    def capture(
        self,
        *,
        idempotency_key: str,
        intent_id: str,
        confirmation_ref: str,
        amount: Money | None = None,
        dry_run: bool = False,
    ) -> Result[PaymentIntent]:
        refused = (
            self._admit("capture", idempotency_key, intent_id, confirmation_ref)
            or _check_intent(intent_id)
            or (None if amount is None else check_money(amount))
            or check_reference(confirmation_ref)
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
            unconfirmed = self.state.confirmations.check_capture(
                confirmation_ref,
                self.state.authorized_with.get(intent_id),
                captured,
                intent.currency,
            )
            if unconfirmed is not None:
                return unconfirmed
            updated = replace(intent, status=PaymentStatus.CAPTURED, captured_minor=captured)
            return self._apply("capture", updated, captured, dry)

        request = {
            "intent_id": intent_id,
            "amount_minor": None if amount is None else amount.amount_minor,
            "currency": None if amount is None else amount.currency,
            "confirmation_ref": confirmation_ref,
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

    def _decline(self, token: str, order_id: str, dry: bool) -> ToolError | None:
        declined = _decline_for(token)
        if declined is not SOFT_DECLINE:
            return declined
        # A new key reached the effect, so this is a new attempt, not a replay.
        if self.state.soft_decline_retry_approves and order_id in self.state.soft_declined:
            return None
        if not dry:
            self.state.soft_declined.add(order_id)
        return declined

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
