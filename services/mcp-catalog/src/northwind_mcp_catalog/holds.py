"""Live stock holds, read by the catalog's stock (row #18, step 0 (c); BE-04's holds, row #25).

BE-04 reserves stock at checkout: a reservation holds units at one node per line until it is
committed, released or past its `expires_at`. Units held live are not free to sell, so every stock
figure the catalog tools give (`check_stock`, the cart's lines, an add's check, its alternatives
and the `in_stock` filter) is the live store's stock less the units held live or sold in the
caller's region, never below zero. Sold units (a `committed` reservation) are subtracted because
BE-04's commit lowers its own `on_hand`, which this catalog does not read (security review M1).

A hold counts exactly when BE-04's own `available` counts it (`inventory/postgres.py`,
`_AVAILABLE`): not swept, its reservation `held`, and `expires_at` after now (`expires_at == now`
does not count).

`Holds` is the port. `NoHolds`, the default, holds nothing: tests and replay use it, so a recording
never depends on the database. A reader that fails must raise `unavailable()` (hard rule 10:
`transient`, retryable, never a quiet zero).

`PostgresHolds` reads BE-04's three tables, and nothing else. This is the one module of this package
allowed a database driver (the user's option B, 2026-11-27): `test_tools.py` checks that no other
module imports it and that every table this module's SQL names is an `inventory_*` table, so the
catalog still never touches the index. `app.build` wires it when `DATABASE_URL` is set.
- A database that can't be reached is `unavailable()`, never a zero.
- Tables that don't exist mean BE-04's schema isn't applied there, so no reservation can exist: no
  holds, a valid empty result. That keeps a server whose database has only the index (GitHub's
  browser job, for one) reading as before. The recording windows' guard
  (`northwind_backend.inventory.live_holds`) is stricter: there, absent tables block the window.
- It reads only. It never sweeps, releases or writes a hold.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

import psycopg
from psycopg import errors as pg_errors

from northwind_common.errors import ToolError

log = logging.getLogger("northwind_mcp_catalog")


def unavailable() -> ToolError:
    """A fresh error per raise: one shared instance would carry the last request's traceback and
    frames from call to call (security review L2)."""
    return ToolError(
        "transient",
        True,
        "inventory_unavailable",
        "Stock can't be checked right now. Try again shortly.",
    )


class Holds(Protocol):
    def live(self, region: str) -> Mapping[str, int]:
        """Units held live now at the nodes of `region`, by variant. Raises `unavailable()`."""
        ...


class NoHolds:
    def live(self, region: str) -> Mapping[str, int]:
        return {}


def subtract(on_hand: int | None, held: Mapping[str, int], variant_id: str) -> int:
    """The live store's stock less the units held live, never below zero."""
    return max(0, (on_hand or 0) - held.get(variant_id, 0))


# Units not free to sell, by variant, in a region: held live (exactly `postgres._AVAILABLE`'s
# predicate: not swept, the reservation `held`, before its expiry) or sold (the reservation
# `committed`: BE-04 lowered its own `on_hand`, which this catalog does not read, so the units are
# subtracted here; security review M1 of option B).
HELD_IN_REGION = """
SELECT h.variant_id, SUM(h.quantity)
FROM inventory_holds h
JOIN inventory_reservations r ON r.idempotency_key = h.idempotency_key
JOIN inventory_stock s ON s.variant_id = h.variant_id AND s.node = h.node
WHERE s.region = %(region)s
  AND (
    (NOT h.swept AND r.status = 'held' AND r.expires_at > %(now)s)
    OR r.status = 'committed'
  )
GROUP BY h.variant_id
"""
# BE-04's own bounds (`postgres._TIMEOUTS`): a lock or a slow statement is an error, never a hang.
TIMEOUTS = "SET LOCAL lock_timeout = '2s'; SET LOCAL statement_timeout = '5s'"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PostgresHolds:
    """BE-04's live holds, one short read-only connection per reading."""

    def __init__(
        self,
        url: str,
        *,
        clock: Callable[[], datetime] = _utc_now,
        connect: Callable[[], psycopg.Connection[Any]] | None = None,
    ) -> None:
        self._connect = connect or (lambda: psycopg.connect(url, connect_timeout=3))
        self._clock = clock
        self._warned = False  # absent tables are said once, naming the database only (L1)
        try:
            self._database = str(psycopg.conninfo.conninfo_to_dict(url).get("dbname", "?"))
        except psycopg.Error:
            self._database = "?"

    def live(self, region: str) -> Mapping[str, int]:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("the clock must give an aware time")
        try:
            with self._connect() as conn:
                conn.read_only = True
                with conn.transaction():
                    conn.execute(TIMEOUTS)
                    rows = conn.execute(HELD_IN_REGION, {"region": region, "now": now}).fetchall()
        except pg_errors.UndefinedTable:
            if not self._warned:
                self._warned = True
                log.warning("holds: BE-04's tables do not exist in database %r", self._database)
            return {}
        except psycopg.Error:
            raise unavailable() from None
        return {str(variant): int(units) for variant, units in rows}
