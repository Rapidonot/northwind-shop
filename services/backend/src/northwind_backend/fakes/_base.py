"""What every fake shares: scopes, a settable outage, idempotency, input checks, and injected faults
(BE-02, row #20), and a `FakeState` that several instances can share (row #21, P3).

A `FaultPlan` injects, per operation, n times or always:
  outage                 a transient outage before anything runs;
  timeout_before_effect  `TIMEOUT` before anything runs;
  timeout_after_effect   on a state-changing call, the effect runs and is recorded under the key,
                         then the caller sees `TIMEOUT` (a same-key replay sees it again while the
                         fault lasts); on a read it is the same as `timeout_before_effect`;
  hang                   before the effect only, and before the idempotency lock: it waits on the
                         call's `CANCEL` event, then returns `TIMEOUT` with no effect and records
                         nothing under the key. A safety bound on the wait ends it the same way and
                         sets `bound_expired`, so a late completion can't come back.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from northwind_backend.errors import TIMEOUT, Result, ToolError, permission, transient, validation
from northwind_backend.idempotency import IdempotencyStore, fingerprint
from northwind_backend.ports import Money
from northwind_backend.recovery import CANCEL
from northwind_common.cards import contains_cvv, contains_pan

MAX_AMOUNT_MINOR = 1_000_000_000  # also keeps a card number out of an amount field
_KEY = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
# ASCII-only: `\d` alone takes any Unicode digit, so one ID could have two spellings, two dict keys.
_ORDER_ID = re.compile(r"O\d{6}", re.ASCII)
_CURRENCY = re.compile(r"[A-Z]{3}")
# Luhn or not: an argument here is an ID, so a long run of digits has no business being one. Not
# ASCII-only like the ID patterns: this one has to see a card number in fullwidth digits too.
_LONG_DIGITS = re.compile(r"\d(?:[\W_]?\d){12}")

CARD_DATA_REFUSED = validation(
    "card_data_refused", "Card data is not accepted here. Use a payment token."
)
OUTAGE = transient("service_unavailable", "The service could not be reached. Nothing was changed.")
NOT_PERMITTED = permission("not_permitted", "This credential is not allowed to do that.")
BAD_KEY = validation("invalid_idempotency_key", "The idempotency key is missing or malformed.")
BAD_ORDER = validation("invalid_order_id", "The order ID is malformed.")
BAD_AMOUNT = validation(
    "invalid_amount", "The amount must be a positive whole number of minor units."
)
BAD_CURRENCY = validation("invalid_currency", "The currency must be a three-letter code.")


@dataclass(frozen=True, slots=True)
class Effect:
    """One thing that really happened. Tests count these to prove a replay did nothing."""

    operation: str
    reference: str
    amount_minor: int = 0


def card_data_in(*values: object) -> bool:
    return any(
        isinstance(value, str)
        and (contains_pan(value) or contains_cvv(value) or _LONG_DIGITS.search(value) is not None)
        for value in values
    )


def matches(pattern: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def check_order_id(order_id: object) -> ToolError | None:
    return None if matches(_ORDER_ID, order_id) else BAD_ORDER


def check_money(amount: object) -> ToolError | None:
    if not isinstance(amount, Money):
        return BAD_AMOUNT
    # `type` and not `isinstance`, because True is an int.
    if type(amount.amount_minor) is not int or not 0 < amount.amount_minor <= MAX_AMOUNT_MINOR:
        return BAD_AMOUNT
    return None if matches(_CURRENCY, amount.currency) else BAD_CURRENCY


class FaultKind(StrEnum):
    OUTAGE = "outage"
    TIMEOUT_BEFORE = "timeout_before_effect"
    TIMEOUT_AFTER = "timeout_after_effect"
    HANG = "hang"


class FaultPlan:
    def __init__(self) -> None:
        self._rules: dict[tuple[str, FaultKind], int | None] = {}
        self._lock = threading.Lock()
        self.bound_seconds = 5.0
        self.bound_expired = False
        # Set when a hang starts waiting, so a test can act while it is hung.
        self.waiting = threading.Event()

    def inject(self, operation: str, kind: FaultKind, times: int | None = None) -> None:
        """`times=None` fires on every call; `times=1` on the first call only."""
        with self._lock:
            self._rules[(operation, kind)] = times

    def take(self, operation: str, kind: FaultKind) -> bool:
        with self._lock:
            key = (operation, kind)
            if key not in self._rules:
                return False
            left = self._rules[key]
            if left is not None:
                if left <= 1:
                    del self._rules[key]
                else:
                    self._rules[key] = left - 1
            return True

    def hang(self) -> ToolError:
        cancel = CANCEL.get() or threading.Event()
        self.waiting.set()
        if not cancel.wait(self.bound_seconds):
            self.bound_expired = True
        return TIMEOUT


class FakeState:
    """What a service holds, as opposed to what one credential may do with it (row #21, P3).

    Every fake instance built on one state shares its idempotency store (and so its lock), its
    effects and its domain records, so the one-payment rule, the refund cap, one assessment and
    one shipment per order hold across instances, not per instance. Scopes, the outage switch and
    injected faults stay per instance: they belong to a credential and its network path."""

    def __init__(self) -> None:
        self.store = IdempotencyStore()
        self.effects: list[Effect] = []


class FakeService:
    def __init__(self, scopes: frozenset[str], state: FakeState) -> None:
        self._scopes = scopes
        self._state = state
        # Aliases of the shared objects, never copies.
        self._store = state.store
        self._effects = state.effects
        self.outage = False
        self.faults = FaultPlan()

    @property
    def effects(self) -> tuple[Effect, ...]:
        return tuple(self._effects)

    def _reachable(self, operation: str, *, read: bool = True) -> ToolError | None:
        if operation not in self._scopes:
            return NOT_PERMITTED
        if self.outage or self.faults.take(operation, FaultKind.OUTAGE):
            return OUTAGE
        if self.faults.take(operation, FaultKind.TIMEOUT_BEFORE):
            return TIMEOUT
        if read and self.faults.take(operation, FaultKind.TIMEOUT_AFTER):
            return TIMEOUT
        if self.faults.take(operation, FaultKind.HANG):
            return self.faults.hang()
        return None

    def _admit(self, operation: str, key: object, *values: object) -> ToolError | None:
        """The checks every state-changing call starts with. Card data is refused before anything
        is stored, hashed or compared."""
        refused = self._reachable(operation, read=False)
        if refused is not None:
            return refused
        if card_data_in(key, *values):
            return CARD_DATA_REFUSED
        return None if matches(_KEY, key) else BAD_KEY

    def _commit[T](
        self,
        operation: str,
        key: str,
        request: Mapping[str, Any],
        dry_run: bool,
        effect: Callable[[bool], Result[T]],
    ) -> Result[T]:
        if dry_run:
            result = self._store.exclusive(lambda: effect(True))
        else:
            result = self._store.run(key, fingerprint(operation, request), lambda: effect(False))
        return TIMEOUT if self.faults.take(operation, FaultKind.TIMEOUT_AFTER) else result
