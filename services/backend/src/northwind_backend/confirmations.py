"""Confirmation references for money movement (hard rule 9; row #21, decision 0014).

A shopper confirms an order summary through a portal UI control, never through chat. That control
issues a confirmation reference bound to one order and one amount. The payment port refuses
`authorize` and `capture` without the right one, so no agent can move money on its own say-so.

The rule (the user's, 2026-11-11):
- `authorize` must present an unused reference bound to its order, currency and exact amount, and
  the first successful `authorize` uses it up;
- `capture` must present the same reference that authorized this intent: accepted although used;
  any other reference is refused, a fresh unused one for the same order included;
- after a void, a new authorization needs a new confirmation.

Until CHK-02 builds the portal control, only a test fixture calls `issue` (an AST pin keeps every
`issue(` call out of `src`). A reference is `cf_` and 16 capital letters: random, never in model
input, never a run of digits, and written by the audit as type and length only.

The book lives on the shared `PaymentState`, so it holds across every port instance (P3). Its
methods run under the state's idempotency lock, inside the port's effect.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from northwind_backend.errors import ToolError, business, validation
from northwind_backend.ports import Money

REFERENCE = re.compile(r"cf_[A-Z]{16}", re.ASCII)
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

CONFIRMATION_REQUIRED = validation(
    "confirmation_required", "A confirmation from the shopper is required for this payment."
)
CONFIRMATION_MISMATCH = validation(
    "confirmation_mismatch", "The confirmation does not match this order, payment or amount."
)
CONFIRMATION_USED = business(
    "confirmation_used", "This confirmation was already used. A new one is needed."
)


@dataclass(frozen=True, slots=True)
class Confirmation:
    ref: str
    order_id: str
    amount: Money


class ConfirmationBook:
    def __init__(self) -> None:
        self._issued: dict[str, Confirmation] = {}
        self._used: set[str] = set()

    def issue(self, order_id: str, amount: Money) -> str:
        """The portal control's side. Called only by tests until CHK-02 (AST-pinned)."""
        while True:
            ref = "cf_" + "".join(secrets.choice(_LETTERS) for _ in range(16))
            if ref not in self._issued:
                break
        self._issued[ref] = Confirmation(ref, order_id, amount)
        return ref

    def check_authorize(self, ref: str, order_id: str, amount: Money) -> ToolError | None:
        found = self._issued.get(ref)
        if found is None or found.order_id != order_id or found.amount != amount:
            return CONFIRMATION_MISMATCH
        return CONFIRMATION_USED if ref in self._used else None

    def use(self, ref: str) -> None:
        self._used.add(ref)

    def check_capture(
        self, ref: str, authorized_with: str | None, amount_minor: int, currency: str
    ) -> ToolError | None:
        """Only the reference that authorized the intent, and never more than it confirmed."""
        found = self._issued.get(ref)
        if found is None or authorized_with is None or ref != authorized_with:
            return CONFIRMATION_MISMATCH
        if found.amount.currency != currency or amount_minor > found.amount.amount_minor:
            return CONFIRMATION_MISMATCH
        return None


def check_reference(ref: object) -> ToolError | None:
    return None if isinstance(ref, str) and REFERENCE.fullmatch(ref) else CONFIRMATION_REQUIRED
