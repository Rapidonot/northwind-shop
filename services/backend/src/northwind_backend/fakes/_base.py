"""What every fake shares: scopes, a settable outage, idempotency and input checks."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from northwind_backend.errors import Result, ToolError, permission, transient, validation
from northwind_backend.idempotency import IdempotencyStore, fingerprint
from northwind_backend.ports import Money
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


class FakeService:
    def __init__(self, scopes: frozenset[str]) -> None:
        self._scopes = scopes
        self._store = IdempotencyStore()
        self._effects: list[Effect] = []
        self.outage = False

    @property
    def effects(self) -> tuple[Effect, ...]:
        return tuple(self._effects)

    def _reachable(self, operation: str) -> ToolError | None:
        if operation not in self._scopes:
            return NOT_PERMITTED
        return OUTAGE if self.outage else None

    def _admit(self, operation: str, key: object, *values: object) -> ToolError | None:
        """The checks every state-changing call starts with. Card data is refused before anything
        is stored, hashed or compared."""
        refused = self._reachable(operation)
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
            return self._store.exclusive(lambda: effect(True))
        return self._store.run(key, fingerprint(operation, request), lambda: effect(False))
