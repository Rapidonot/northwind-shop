"""BE-04's shared model: the port, its values, its errors and its input checks (row #25).

A reservation holds units of stock at one fulfilment node per line, for one order, for a fixed
`HOLD`. It moves no money. `commit` turns a live hold into a decrement of on-hand stock (the units
leave with the order); `release` gives a hold back. A hold whose `expires_at` has passed is
*expired* and never counts against stock, on any read (the user, 2026-11-26, change 2).

Time comes only from an injected `Clock`: no wall-clock call and no database `now()` (change 2),
so a test moves time by setting the clock, never by sleeping.

Both implementations, `InventoryFake` (memory) and `PostgresInventory`, follow one contract, pinned
by `tests/inventory/test_be04_contract.py`. Only the Postgres one is the guarantee against oversell
across processes (the user's constraint 1); the fake is the test double for the unit tests.

Errors are structured (hard rule 10): `out_of_stock` is a business result carrying the shortfall and
code-built substitutes; `inventory_unavailable` is transient, so "access failed" and "none left" are
never confused.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from northwind_backend.errors import (
    ErrorCategory,
    Result,
    ToolError,
    business,
    transient,
    validation,
)
from northwind_backend.fakes._base import (
    _KEY,
    BAD_KEY,
    CARD_DATA_REFUSED,
    card_data_in,
    check_order_id,
    matches,
)

HOLD = timedelta(minutes=15)
MAX_LINES = 50
MAX_QUANTITY = 1_000

type Clock = Callable[[], datetime]

_VARIANT = re.compile(r"P\d{4}(?:-[A-Z0-9]{1,16}){1,4}", re.ASCII)
_REGION = re.compile(r"US|CA|UK|EU", re.ASCII)
_POSTAL = re.compile(r"[A-Za-z0-9 -]{0,12}", re.ASCII)

BAD_LINES = validation(
    "invalid_lines",
    "Each line needs a known variant and a whole quantity of at least 1, and no variant twice.",
)
BAD_REGION = validation("invalid_region", "The region is not one Northwind sells in.")
BAD_POSTAL = validation("invalid_postal_code", "The postal code is malformed.")
# Security review L3: its own code, so placement never reads it as a payment confirmation problem.
KEY_CONFLICT = validation(
    "reservation_conflict", "This idempotency key was already used for a different reservation."
)
NOT_FOUND = validation("reservation_not_found", "No reservation has this idempotency key.")
UNAVAILABLE = transient(
    # Security review L4: a lost connection at COMMIT may follow a write that took effect, so this
    # never says nothing changed. A same-key retry returns the stored result.
    "inventory_unavailable",
    "The inventory store could not be reached.",
)
RELEASED = business("hold_released", "This reservation was released, so it can't be committed.")
EXPIRED = business("hold_expired", "This reservation expired and its stock is no longer available.")
_OUT_OF_STOCK_MESSAGE = "Not enough stock to reserve every line. Nothing was reserved."


class HoldStatus(StrEnum):
    HELD = "held"
    RELEASED = "released"
    COMMITTED = "committed"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class Line:
    variant_id: str
    quantity: int


@dataclass(frozen=True, slots=True, order=True)
class Allocation:
    variant_id: str
    node: str
    quantity: int


@dataclass(frozen=True, slots=True)
class Reservation:
    """One order's reservation. `status` is as of the read that returned it: a `held` reservation
    whose `expires_at` is past is reported `expired`."""

    idempotency_key: str
    order_id: str
    region: str
    allocations: tuple[Allocation, ...]
    status: HoldStatus
    expires_at: datetime
    dry_run: bool = False

    def nodes(self) -> tuple[str, ...]:
        return tuple(sorted({a.node for a in self.allocations}))


@dataclass(frozen=True, slots=True)
class Shortfall:
    variant_id: str
    wanted: int
    available: int  # the most any single node of the region can give, live holds subtracted


@dataclass(frozen=True, slots=True)
class Substitute:
    """A candidate built by code (the user's constraint 2): never a price, never model text."""

    variant_id: str
    product_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class StockOut(ToolError):
    """`out_of_stock`: a business result, not a failure to read (hard rule 10). Carries what was
    short and the substitutes for each short line."""

    short: tuple[Shortfall, ...] = ()
    substitutes: tuple[tuple[str, tuple[Substitute, ...]], ...] = ()

    def to_envelope(self) -> dict[str, Any]:
        return {
            **ToolError.to_envelope(self),
            "short": [
                {"variant_id": s.variant_id, "wanted": s.wanted, "available": s.available}
                for s in self.short
            ],
            "substitutes": {
                variant: [
                    {"variant_id": c.variant_id, "product_id": c.product_id, "reason": c.reason}
                    for c in found
                ]
                for variant, found in self.substitutes
            },
        }


def stock_out(
    short: Sequence[Shortfall], substitutes: Sequence[tuple[str, tuple[Substitute, ...]]]
) -> StockOut:
    return StockOut(
        ErrorCategory.BUSINESS,
        False,
        "out_of_stock",
        _OUT_OF_STOCK_MESSAGE,
        short=tuple(short),
        substitutes=tuple(substitutes),
    )


@runtime_checkable
class InventoryPort(Protocol):
    """Every write takes an idempotency key (hard rule 9); `reserve` also takes `dry_run`."""

    def reserve(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        region: str,
        postal_code: str,
        lines: Sequence[Line],
        dry_run: bool = False,
    ) -> Result[Reservation]: ...

    def release(self, *, idempotency_key: str) -> Result[Reservation]: ...

    def commit(self, *, idempotency_key: str) -> Result[Reservation]: ...

    def available(self, variant_id: str, node: str) -> Result[int]: ...


@dataclass(frozen=True, slots=True)
class Request:
    """A checked `reserve` request. `fingerprint` is what a same-key repeat must match."""

    idempotency_key: str
    order_id: str
    region: str
    postal_code: str
    lines: tuple[Line, ...] = field(default_factory=tuple)

    @property
    def fingerprint(self) -> str:
        body = {
            "order_id": self.order_id,
            "region": self.region,
            "postal_code": self.postal_code,
            "lines": sorted([line.variant_id, line.quantity] for line in self.lines),
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def check_key(key: object) -> ToolError | None:
    if card_data_in(key):
        return CARD_DATA_REFUSED
    return None if matches(_KEY, key) else BAD_KEY


def check_request(
    idempotency_key: object,
    order_id: object,
    region: object,
    postal_code: object,
    lines: object,
) -> Request | ToolError:
    """Card data first, then each value (as every fake does). No error echoes an argument. A line
    with a quantity of 0 or less, or a variant named twice, is refused (M12's rule, here too)."""
    if card_data_in(idempotency_key, order_id, region, postal_code):
        return CARD_DATA_REFUSED
    refused = check_key(idempotency_key) or check_order_id(order_id)
    if refused is not None:
        return refused
    if not matches(_REGION, region):
        return BAD_REGION
    if not matches(_POSTAL, postal_code):
        return BAD_POSTAL
    if not isinstance(lines, (list, tuple)) or not 1 <= len(lines) <= MAX_LINES:
        return BAD_LINES
    seen: set[str] = set()
    checked: list[Line] = []
    for line in lines:
        if not isinstance(line, Line) or card_data_in(line.variant_id):
            return BAD_LINES
        quantity = line.quantity
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            return BAD_LINES
        if not matches(_VARIANT, line.variant_id) or not 1 <= quantity <= MAX_QUANTITY:
            return BAD_LINES
        if line.variant_id in seen:
            return BAD_LINES
        seen.add(line.variant_id)
        checked.append(line)
    return Request(
        str(idempotency_key), str(order_id), str(region), str(postal_code), tuple(checked)
    )


def checked_now(clock: Clock) -> datetime:
    """The clock must give an aware time: a naive one would compare wrongly with `expires_at`."""
    now = clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("the inventory clock must return an aware datetime")
    return now


def live(status: HoldStatus, expires_at: datetime, now: datetime) -> bool:
    """A hold counts against stock only while held and before its expiry (`expires_at == now` is
    expired)."""
    return status is HoldStatus.HELD and expires_at > now


def reported(status: HoldStatus, expires_at: datetime, now: datetime) -> HoldStatus:
    if status is HoldStatus.HELD and expires_at <= now:
        return HoldStatus.EXPIRED
    return status
