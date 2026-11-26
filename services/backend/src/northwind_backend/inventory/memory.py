"""`InventoryFake`: BE-04's in-memory inventory, the test double for unit tests and the replay
harness (row #25). One lock serialises every call on a shared `InventoryState`, so it never
oversells within a process. Across processes only `PostgresInventory` holds (constraint 1).

Same contract as `PostgresInventory` (`tests/inventory/test_be04_contract.py`):
- `reserve` is all or nothing. A same-key repeat returns the first result; the same key for another
  request is `reservation_conflict`. A refusal stores nothing under the key, so a later retry
  with the same key tries again. `dry_run` checks against current stock, changes nothing, and
  neither reads nor records the key (hard rule 9).
- Each hold expires `HOLD` after its reservation. An expired hold never counts against stock on any
  read; `reserve` also sweeps the expired holds of every row it touches, so the `held` counter
  matches the live holds there.
- `release` is idempotent and a no-op for a released, committed or expired reservation.
- `commit` moves a live hold's units out of on-hand stock. An expired one is taken again from the
  same nodes if they still have the units; otherwise `hold_expired`.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from northwind_backend.errors import Ok, Result, ToolError
from northwind_backend.fakes.catalog import FIXTURES
from northwind_backend.inventory import nodes as node_choice
from northwind_backend.inventory import substitutes as subs
from northwind_backend.inventory.model import (
    EXPIRED,
    HOLD,
    KEY_CONFLICT,
    NOT_FOUND,
    RELEASED,
    UNAVAILABLE,
    Allocation,
    Clock,
    HoldStatus,
    Line,
    Request,
    Reservation,
    Shortfall,
    Substitute,
    check_key,
    check_request,
    checked_now,
    reported,
    stock_out,
)


@dataclass
class _Row:
    region: str
    on_hand: int
    held: int = 0


@dataclass
class _Hold:
    allocation: Allocation
    swept: bool = False  # an expired hold whose units were given back to `held`


@dataclass
class _Record:
    fingerprint: str
    order_id: str
    region: str
    expires_at: datetime
    holds: list[_Hold]
    status: HoldStatus = HoldStatus.HELD


@dataclass
class InventoryState:
    rows: dict[tuple[str, str], _Row] = field(default_factory=dict)
    records: dict[str, _Record] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    down: bool = False  # an injected outage: every call returns `inventory_unavailable`

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, object]]) -> InventoryState:
        state = cls()
        for row in rows:
            key = (str(row["variant_id"]), str(row["node"]))
            on_hand = row["on_hand"]
            if not isinstance(on_hand, int) or isinstance(on_hand, bool) or on_hand < 0:
                raise ValueError("on_hand must be a whole number of at least 0")
            state.rows[key] = _Row(str(row["region"]), on_hand)
        return state

    @classmethod
    def from_fixtures(cls, path: Path = FIXTURES / "stock.jsonl") -> InventoryState:
        with path.open(encoding="utf-8") as handle:
            return cls.from_rows(json.loads(line) for line in handle if line.strip())


class InventoryFake:
    def __init__(
        self,
        clock: Clock,
        *,
        state: InventoryState | None = None,
        catalog: subs.SubstituteCatalog | None = None,
        regions: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.clock = clock
        self.state = state if state is not None else InventoryState.from_fixtures()
        self.catalog = catalog
        self.regions = regions

    # ---- reads ---------------------------------------------------------------------------------

    def available(self, variant_id: str, node: str) -> Result[int]:
        if self.state.down:
            return UNAVAILABLE
        now = checked_now(self.clock)
        with self.state.lock:
            return Ok(self._available(variant_id, node, now))

    def _available(self, variant_id: str, node: str, now: datetime) -> int:
        row = self.state.rows.get((variant_id, node))
        if row is None:
            return 0
        held = sum(
            hold.allocation.quantity
            for record in self.state.records.values()
            if record.status is HoldStatus.HELD and record.expires_at > now
            for hold in record.holds
            if not hold.swept
            and (hold.allocation.variant_id, hold.allocation.node)
            == (
                variant_id,
                node,
            )
        )
        return row.on_hand - held

    def _nodes(self, region: str, postal_code: str) -> tuple[str, ...]:
        listed = None if self.regions is None else self.regions.get(region, ())
        return node_choice.preference(region, postal_code, listed)

    # ---- writes --------------------------------------------------------------------------------

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
        checked = check_request(idempotency_key, order_id, region, postal_code, lines)
        if isinstance(checked, ToolError):
            return checked
        if self.state.down:
            return UNAVAILABLE
        now = checked_now(self.clock)
        with self.state.lock:
            if not dry_run:
                existing = self.state.records.get(checked.idempotency_key)
                if existing is not None:
                    if existing.fingerprint != checked.fingerprint:
                        return KEY_CONFLICT
                    return Ok(_view(checked.idempotency_key, existing, now))
            nodes = self._nodes(checked.region, checked.postal_code)
            if not dry_run:
                self._sweep({line.variant_id for line in checked.lines}, now)
            allocations, short = node_choice.choose(
                checked.lines, nodes, lambda v, n: self._available(v, n, now)
            )
            if short:
                return stock_out(short, self._substitutes(checked, short, nodes, now))
            expires_at = now + HOLD
            if dry_run:
                return Ok(_reservation(checked, allocations, HoldStatus.HELD, expires_at, True))
            for allocation in allocations:
                row = self.state.rows[(allocation.variant_id, allocation.node)]
                row.held += allocation.quantity
                if not 0 <= row.held <= row.on_hand:  # the database's CHECK, mirrored
                    raise AssertionError("held outside 0..on_hand")
            self.state.records[checked.idempotency_key] = _Record(
                checked.fingerprint,
                checked.order_id,
                checked.region,
                expires_at,
                [_Hold(a) for a in allocations],
            )
            return Ok(_reservation(checked, allocations, HoldStatus.HELD, expires_at, False))

    def release(self, *, idempotency_key: str) -> Result[Reservation]:
        refused = check_key(idempotency_key)
        if refused is not None:
            return refused
        if self.state.down:
            return UNAVAILABLE
        now = checked_now(self.clock)
        with self.state.lock:
            record = self.state.records.get(idempotency_key)
            if record is None:
                return NOT_FOUND
            if record.status is HoldStatus.HELD:
                # Gives back only holds not swept yet; an expired one that is released stays
                # released (security review L2).
                self._sweep_record(record)
                record.status = HoldStatus.RELEASED
            return Ok(_view(idempotency_key, record, now))

    def commit(self, *, idempotency_key: str) -> Result[Reservation]:
        refused = check_key(idempotency_key)
        if refused is not None:
            return refused
        if self.state.down:
            return UNAVAILABLE
        now = checked_now(self.clock)
        with self.state.lock:
            record = self.state.records.get(idempotency_key)
            if record is None:
                return NOT_FOUND
            if record.status is HoldStatus.RELEASED:
                return RELEASED
            if record.status is HoldStatus.COMMITTED:
                return Ok(_view(idempotency_key, record, now))
            # Security review M1: a hold another clock already swept has given its units back, so
            # the reservation counts as expired and nothing is given back twice.
            if record.expires_at <= now or any(hold.swept for hold in record.holds):
                self._sweep_record(record)
                wanted = [hold.allocation for hold in record.holds]
                if any(self._available(a.variant_id, a.node, now) < a.quantity for a in wanted):
                    record.expires_at = min(record.expires_at, now)  # re-review N1: spent
                    return EXPIRED
                for allocation in wanted:
                    self.state.rows[
                        (allocation.variant_id, allocation.node)
                    ].on_hand -= allocation.quantity
            else:
                for hold in record.holds:
                    row = self.state.rows[(hold.allocation.variant_id, hold.allocation.node)]
                    row.held -= hold.allocation.quantity
                    row.on_hand -= hold.allocation.quantity
                    hold.swept = True
            record.status = HoldStatus.COMMITTED
            return Ok(_view(idempotency_key, record, now))

    # ---- plumbing ------------------------------------------------------------------------------

    def _sweep(self, variants: set[str], now: datetime) -> None:
        for record in self.state.records.values():
            if record.status is not HoldStatus.HELD or record.expires_at > now:
                continue
            for hold in record.holds:
                if not hold.swept and hold.allocation.variant_id in variants:
                    self._sweep_hold(hold)

    def _sweep_record(self, record: _Record) -> None:
        for hold in record.holds:
            if not hold.swept:
                self._sweep_hold(hold)

    def _sweep_hold(self, hold: _Hold) -> None:
        row = self.state.rows[(hold.allocation.variant_id, hold.allocation.node)]
        row.held -= hold.allocation.quantity
        hold.swept = True

    def _substitutes(
        self,
        request: Request,
        short: Sequence[Shortfall],
        nodes: Sequence[str],
        now: datetime,
    ) -> list[tuple[str, tuple[Substitute, ...]]]:
        def reservable(variant_id: str) -> int:
            return max((self._available(variant_id, n, now) for n in nodes), default=0)

        wanted = {line.variant_id: line.quantity for line in request.lines}
        found = []
        for shortfall in short:
            variant_id = shortfall.variant_id
            found.append(
                (
                    variant_id,
                    subs.find(
                        variant_id,
                        request.region,
                        wanted[variant_id],
                        reservable,
                        catalog=self.catalog,
                    ),
                )
            )
        return found


def _reservation(
    request: Request,
    allocations: Sequence[Allocation],
    status: HoldStatus,
    expires_at: datetime,
    dry_run: bool,
) -> Reservation:
    return Reservation(
        request.idempotency_key,
        request.order_id,
        request.region,
        tuple(allocations),
        status,
        expires_at,
        dry_run,
    )


def _view(key: str, record: _Record, now: datetime) -> Reservation:
    return Reservation(
        key,
        record.order_id,
        record.region,
        tuple(sorted(hold.allocation for hold in record.holds)),
        reported(record.status, record.expires_at, now),
        record.expires_at,
    )
