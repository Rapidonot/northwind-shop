"""`PostgresInventory`: BE-04's inventory store, the guarantee against oversell (row #25, the user's
constraint 1). Same contract as `InventoryFake` (see `memory.py`).

How it can't oversell, across any number of processes:
- `reserve` locks every stock row it could take from (`SELECT … ORDER BY variant_id, node FOR
  UPDATE`), in one sorted order, so two orders can't deadlock. Under those locks it sweeps their
  expired holds, picks nodes, and takes units with a conditional
  `UPDATE … SET held = held + q WHERE … AND on_hand - held >= q`.
  A row that returns nothing aborts the whole reservation.
- `CHECK (held >= 0 AND held <= on_hand)` is the last guard, whatever the code above it does.
- The idempotency key is the reservation's primary key. It is inserted first with
  `ON CONFLICT DO NOTHING`, so a concurrent same-key call waits on the first one's transaction and
  then returns its result, and the units are taken once.
- `release` and `commit` lock their own reservation row first, then its stock rows in the same
  sorted order.

Time is the injected clock's, passed as a parameter; SQL never calls `now()` (change 2). Only
`psycopg.OperationalError` (connection or server down) becomes `inventory_unavailable`; any other
database error is a bug and is raised.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

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

SCHEMA = Path(__file__).with_name("schema.sql")

_LOCK_ROWS = """
SELECT variant_id, node, on_hand, held FROM inventory_stock
WHERE variant_id = ANY(%(variants)s) AND node = ANY(%(nodes)s)
ORDER BY variant_id, node
FOR UPDATE
"""
_SWEEP = """
UPDATE inventory_holds h SET swept = true
FROM inventory_reservations r
WHERE r.idempotency_key = h.idempotency_key AND r.status = 'held' AND r.expires_at <= %(now)s
  AND NOT h.swept AND h.variant_id = ANY(%(variants)s) AND h.node = ANY(%(nodes)s)
RETURNING h.variant_id, h.node, h.quantity
"""
# Security review L5: a lock wait or a slow statement fails fast instead of stalling a run.
_TIMEOUTS = "SET LOCAL lock_timeout = '2s'; SET LOCAL statement_timeout = '5s'"
_GIVE_BACK = "UPDATE inventory_stock SET held = held - %s WHERE variant_id = %s AND node = %s"
_TAKE = """
UPDATE inventory_stock SET held = held + %(q)s
WHERE variant_id = %(variant)s AND node = %(node)s AND on_hand - held >= %(q)s
RETURNING held
"""
# Live availability without locks: a hold counts only while its reservation is held and unexpired.
_AVAILABLE = """
SELECT s.variant_id, s.node, s.on_hand - COALESCE(SUM(h.quantity) FILTER (
    WHERE r.status = 'held' AND r.expires_at > %(now)s), 0)
FROM inventory_stock s
LEFT JOIN inventory_holds h
  ON h.variant_id = s.variant_id AND h.node = s.node AND NOT h.swept
LEFT JOIN inventory_reservations r ON r.idempotency_key = h.idempotency_key
WHERE s.node = ANY(%(nodes)s) AND (%(variants)s::text[] IS NULL OR s.variant_id = ANY(%(variants)s))
GROUP BY s.variant_id, s.node
"""


class _Abort(Exception):  # noqa: N818 - control flow, not an error: rolls the transaction back
    def __init__(self, result: Result[Reservation]) -> None:
        super().__init__("abort")
        self.result = result


def apply_schema(conn: psycopg.Connection[Any]) -> None:
    conn.execute(SCHEMA.read_text(encoding="utf-8"))


def load_stock(conn: psycopg.Connection[Any], rows: Iterable[Mapping[str, Any]]) -> int:
    """Seed or reset on-hand stock (`data/fixtures/stock.jsonl` rows). Returns the rows written."""
    count = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                "INSERT INTO inventory_stock (variant_id, node, region, on_hand) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (variant_id, node) "
                "DO UPDATE SET on_hand = EXCLUDED.on_hand, region = EXCLUDED.region",
                (row["variant_id"], row["node"], row["region"], int(row["on_hand"])),
            )
            count += 1
    return count


def fixture_rows(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or FIXTURES / "stock.jsonl"
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class PostgresInventory:
    def __init__(
        self,
        connect: Callable[[], psycopg.Connection[Any]],
        clock: Clock,
        *,
        catalog: subs.SubstituteCatalog | None = None,
        regions: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.connect = connect
        self.clock = clock
        self.catalog = catalog
        self.regions = regions

    def _nodes(self, region: str, postal_code: str) -> tuple[str, ...]:
        listed = None if self.regions is None else self.regions.get(region, ())
        return node_choice.preference(region, postal_code, listed)

    # ---- reads ---------------------------------------------------------------------------------

    def available(self, variant_id: str, node: str) -> Result[int]:
        now = checked_now(self.clock)
        try:
            with self.connect() as conn, conn.transaction():
                conn.execute(_TIMEOUTS)
                found = _available(conn, now, [node], [variant_id])
        except psycopg.OperationalError:
            return UNAVAILABLE
        return Ok(found.get((variant_id, node), 0))

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
        now = checked_now(self.clock)
        try:
            with self.connect() as conn:
                try:
                    with conn.transaction():
                        conn.execute(_TIMEOUTS)
                        return self._reserve(conn, checked, now, dry_run)
                except _Abort as abort:
                    return abort.result
        except psycopg.OperationalError:
            return UNAVAILABLE

    def _reserve(
        self, conn: psycopg.Connection[Any], request: Request, now: datetime, dry_run: bool
    ) -> Result[Reservation]:
        expires_at = now + HOLD
        if not dry_run:
            inserted = conn.execute(
                "INSERT INTO inventory_reservations "
                "(idempotency_key, fingerprint, order_id, region, status, expires_at) "
                "VALUES (%s, %s, %s, %s, 'held', %s) ON CONFLICT (idempotency_key) DO NOTHING "
                "RETURNING idempotency_key",
                (
                    request.idempotency_key,
                    request.fingerprint,
                    request.order_id,
                    request.region,
                    expires_at,
                ),
            ).fetchone()
            if inserted is None:
                stored = conn.execute(
                    "SELECT fingerprint FROM inventory_reservations WHERE idempotency_key = %s",
                    (request.idempotency_key,),
                ).fetchone()
                if stored is None or stored[0] != request.fingerprint:
                    return KEY_CONFLICT
                view = _view(conn, request.idempotency_key, now)
                return NOT_FOUND if view is None else Ok(view)
        nodes = self._nodes(request.region, request.postal_code)
        variants = sorted(line.variant_id for line in request.lines)
        locked = _lock(conn, now, variants, list(nodes))
        allocations, short = node_choice.choose(
            request.lines, nodes, lambda v, n: locked.get((v, n), 0)
        )
        if short:
            raise _Abort(stock_out(short, self._substitutes(conn, request, short, nodes, now)))
        if dry_run:
            raise _Abort(
                Ok(_reservation(request, allocations, HoldStatus.HELD, expires_at, dry_run=True))
            )
        for allocation in allocations:
            took = conn.execute(
                _TAKE,
                {
                    "q": allocation.quantity,
                    "variant": allocation.variant_id,
                    "node": allocation.node,
                },
            ).fetchone()
            if took is None:  # never under the row locks; the conditional update still decides
                raise _Abort(
                    stock_out([Shortfall(allocation.variant_id, allocation.quantity, 0)], [])
                )
            conn.execute(
                "INSERT INTO inventory_holds (idempotency_key, variant_id, node, quantity) "
                "VALUES (%s, %s, %s, %s)",
                (
                    request.idempotency_key,
                    allocation.variant_id,
                    allocation.node,
                    allocation.quantity,
                ),
            )
        return Ok(_reservation(request, allocations, HoldStatus.HELD, expires_at, dry_run=False))

    def release(self, *, idempotency_key: str) -> Result[Reservation]:
        return self._settle(idempotency_key, commit=False)

    def commit(self, *, idempotency_key: str) -> Result[Reservation]:
        return self._settle(idempotency_key, commit=True)

    def _settle(self, key: str, *, commit: bool) -> Result[Reservation]:
        refused = check_key(key)
        if refused is not None:
            return refused
        now = checked_now(self.clock)
        try:
            with self.connect() as conn, conn.transaction():
                conn.execute(_TIMEOUTS)
                return self._settle_in(conn, key, now, commit)
        except psycopg.OperationalError:
            return UNAVAILABLE

    def _settle_in(
        self, conn: psycopg.Connection[Any], key: str, now: datetime, commit: bool
    ) -> Result[Reservation]:
        row = conn.execute(
            "SELECT status, expires_at FROM inventory_reservations "
            "WHERE idempotency_key = %s FOR UPDATE",
            (key,),
        ).fetchone()
        if row is None:
            return NOT_FOUND
        status, expires_at = HoldStatus(row[0]), row[1]
        if status is HoldStatus.RELEASED and commit:
            return RELEASED
        if status is not HoldStatus.HELD:
            view = _view(conn, key, now)
            return NOT_FOUND if view is None else Ok(view)
        planned = conn.execute(
            "SELECT DISTINCT variant_id, node FROM inventory_holds WHERE idempotency_key = %s",
            (key,),
        ).fetchall()
        variants = sorted({v for v, _ in planned})
        nodes = sorted({n for _, n in planned})
        free = _lock(conn, now, variants, nodes)  # also sweeps every expired hold on these rows
        # Security review M1: read the holds again under the stock locks. Another call, with a later
        # clock, may have swept some of them while this one waited; a swept hold has already given
        # its units back, so it is never given back again, and the reservation counts as expired.
        holds = conn.execute(
            "SELECT variant_id, node, quantity, swept FROM inventory_holds "
            "WHERE idempotency_key = %s ORDER BY variant_id, node FOR UPDATE",
            (key,),
        ).fetchall()
        expired = expires_at <= now or any(swept for *_, swept in holds)
        if expired:
            for variant, node, quantity, swept in holds:
                if not swept:  # a later clock swept part of it: give back the rest, once
                    conn.execute(_GIVE_BACK, (quantity, variant, node))
                    free[(variant, node)] = free.get((variant, node), 0) + quantity
            conn.execute(
                "UPDATE inventory_holds SET swept = true WHERE idempotency_key = %s", (key,)
            )
            if not commit:
                # Security review L2: an expired hold that is released stays released.
                conn.execute(
                    "UPDATE inventory_reservations SET status = 'released' "
                    "WHERE idempotency_key = %s",
                    (key,),
                )
            else:
                # Take the units again from the same nodes, if they are still free.
                if any(free.get((v, n), 0) < q for v, n, q, _ in holds):
                    # Re-review N1: spent, so a same-key repeat never reports it as held.
                    conn.execute(
                        "UPDATE inventory_reservations SET expires_at = LEAST(expires_at, %s) "
                        "WHERE idempotency_key = %s",
                        (now, key),
                    )
                    return EXPIRED
                for variant, node, quantity, _swept in holds:
                    conn.execute(
                        "UPDATE inventory_stock SET on_hand = on_hand - %s "
                        "WHERE variant_id = %s AND node = %s",
                        (quantity, variant, node),
                    )
                conn.execute(
                    "UPDATE inventory_reservations SET status = 'committed' "
                    "WHERE idempotency_key = %s",
                    (key,),
                )
        else:
            for variant, node, quantity, _swept in holds:
                if commit:
                    conn.execute(
                        "UPDATE inventory_stock SET held = held - %(q)s, on_hand = on_hand - %(q)s "
                        "WHERE variant_id = %(variant)s AND node = %(node)s",
                        {"q": quantity, "variant": variant, "node": node},
                    )
                else:
                    conn.execute(_GIVE_BACK, (quantity, variant, node))
            conn.execute(
                "UPDATE inventory_holds SET swept = true WHERE idempotency_key = %s", (key,)
            )
            new = HoldStatus.COMMITTED if commit else HoldStatus.RELEASED
            conn.execute(
                "UPDATE inventory_reservations SET status = %s WHERE idempotency_key = %s",
                (new.value, key),
            )
        view = _view(conn, key, now)
        return NOT_FOUND if view is None else Ok(view)

    def _substitutes(
        self,
        conn: psycopg.Connection[Any],
        request: Request,
        short: Sequence[Shortfall],
        nodes: Sequence[str],
        now: datetime,
    ) -> list[tuple[str, tuple[Substitute, ...]]]:
        found = _available(conn, now, list(nodes), None)
        best: dict[str, int] = {}
        for (variant, _node), units in found.items():
            best[variant] = max(best.get(variant, 0), units)
        wanted = {line.variant_id: line.quantity for line in request.lines}
        return [
            (
                s.variant_id,
                subs.find(
                    s.variant_id,
                    request.region,
                    wanted[s.variant_id],
                    lambda v: best.get(v, 0),
                    catalog=self.catalog,
                ),
            )
            for s in short
        ]


def _lock(
    conn: psycopg.Connection[Any], now: datetime, variants: list[str], nodes: list[str]
) -> dict[tuple[str, str], int]:
    """Lock the rows in sorted order, sweep their expired holds, and return what each can give."""
    rows = conn.execute(_LOCK_ROWS, {"variants": variants, "nodes": nodes}).fetchall()
    free = {(v, n): on_hand - held for v, n, on_hand, held in rows}
    swept = conn.execute(_SWEEP, {"now": now, "variants": variants, "nodes": nodes}).fetchall()
    for variant, node, quantity in swept:
        conn.execute(_GIVE_BACK, (quantity, variant, node))
        free[(variant, node)] += quantity
    return free


def _available(
    conn: psycopg.Connection[Any], now: datetime, nodes: list[str], variants: list[str] | None
) -> dict[tuple[str, str], int]:
    rows = conn.execute(_AVAILABLE, {"now": now, "nodes": nodes, "variants": variants}).fetchall()
    return {(v, n): int(units) for v, n, units in rows}


def _reservation(
    request: Request,
    allocations: Sequence[Allocation],
    status: HoldStatus,
    expires_at: datetime,
    *,
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


def _view(conn: psycopg.Connection[Any], key: str, now: datetime) -> Reservation | None:
    row = conn.execute(
        "SELECT order_id, region, status, expires_at FROM inventory_reservations "
        "WHERE idempotency_key = %s",
        (key,),
    ).fetchone()
    if row is None:
        return None
    holds = conn.execute(
        "SELECT variant_id, node, quantity FROM inventory_holds WHERE idempotency_key = %s",
        (key,),
    ).fetchall()
    allocations = tuple(sorted(Allocation(v, n, int(q)) for v, n, q in holds))
    return Reservation(
        key, row[0], row[1], allocations, reported(HoldStatus(row[2]), row[3], now), row[3]
    )
