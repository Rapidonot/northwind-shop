"""BE-04's acceptance criterion: race tests on the last unit never oversell (row #25).

Real concurrent requests against Postgres (the user's constraint 1): each thread has its own
connection, and all of them start on one barrier. Time comes from a settable clock, never from
the wall clock or a sleep (change 2).

`test_a_read_then_write_reserve_oversells_under_the_same_race` shows the race can fail: a reserve
that reads before it writes, with no lock, no conditional update and no CHECK, oversells under the
same barrier. So a pass by the real `PostgresInventory` means something.

Needs the local stack (`make up`); run with `make test-integration`.
"""

from __future__ import annotations

import os
import random
import threading
import uuid
import zlib
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import psycopg
import pytest

from northwind_backend.errors import Ok, Result
from northwind_backend.inventory import HOLD, HoldStatus, Line, Reservation, StockOut
from northwind_backend.inventory.postgres import PostgresInventory, apply_schema, load_stock

pytestmark = [pytest.mark.req("BE-04"), pytest.mark.integration]

THREADS = 50
ROUNDS = 20
START = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
CA = "P0001-BLK-REG-L"  # sold in CA from one node, DC-CA
US_A = "P0001-BLK-REG-M"
US_B = "P0001-BLK-REG-S"


class SettableClock:
    def __init__(self, now: datetime = START) -> None:
        self._now = now
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, by: timedelta) -> None:
        with self._lock:
            self._now += by


@dataclass
class Db:
    url: str
    schema: str

    def connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.url, options=f"-csearch_path={self.schema}")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def reset(self, rows: Sequence[tuple[str, str, str, int]]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM inventory_holds")
            conn.execute("DELETE FROM inventory_reservations")
            conn.execute("DELETE FROM inventory_stock")
            load_stock(
                conn,
                [{"variant_id": v, "node": n, "region": r, "on_hand": q} for v, n, r, q in rows],
            )

    def row(self, variant: str, node: str) -> tuple[int, int]:
        found = self.execute(
            "SELECT on_hand, held FROM inventory_stock WHERE variant_id = %s AND node = %s",
            (variant, node),
        )
        return int(found[0][0]), int(found[0][1])


def _database_url() -> str:
    from northwind_rag.cli import DEFAULT_DATABASE_URL

    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


@pytest.fixture
def db() -> Iterator[Db]:
    """A throwaway schema with the inventory tables, dropped afterwards (no conftest.py: that is on
    the control surface)."""
    url = _database_url()
    schema = f"be04_race_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    found = Db(url, schema)
    try:
        with found.connect() as conn:
            apply_schema(conn)
        yield found
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def together[T](calls: Sequence[Callable[[], T]]) -> list[T]:
    """Run every call on its own thread, all released by one barrier."""
    barrier = threading.Barrier(len(calls))

    def run(call: Callable[[], T]) -> T:
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(run, calls))


def reserve(
    inventory: PostgresInventory,
    key: str,
    lines: Sequence[Line],
    *,
    region: str = "CA",
    postal_code: str = "M5V 2T6",
) -> Callable[[], Result[Reservation]]:
    order = f"O{zlib.crc32(key.encode()) % 1_000_000:06d}"
    return lambda: inventory.reserve(
        idempotency_key=key, order_id=order, region=region, postal_code=postal_code, lines=lines
    )


def granted(results: Sequence[Result[Reservation]]) -> list[Reservation]:
    return [r.value for r in results if isinstance(r, Ok)]


def test_the_last_unit_is_reserved_exactly_once_in_every_round(db: Db) -> None:
    inventory = PostgresInventory(db.connect, SettableClock())
    for round_ in range(ROUNDS):
        db.reset([(CA, "DC-CA", "CA", 1)])
        results = together(
            [reserve(inventory, f"r{round_}-k{i}", [Line(CA, 1)]) for i in range(THREADS)]
        )
        assert len(granted(results)) == 1, f"round {round_}"
        refused = [r for r in results if not isinstance(r, Ok)]
        assert all(isinstance(r, StockOut) and r.code == "out_of_stock" for r in refused)
        assert db.row(CA, "DC-CA") == (1, 1)
        assert db.execute("SELECT count(*) FROM inventory_holds") == [(1,)]


def test_mixed_quantities_over_two_nodes_never_hold_more_than_on_hand(db: Db) -> None:
    db.reset([(US_A, "DC-EAST", "US", 2), (US_A, "DC-WEST", "US", 3)])
    inventory = PostgresInventory(db.connect, SettableClock())
    rng = random.Random(25)  # noqa: S311 - a seeded test mix, not a secret
    calls = [
        reserve(
            inventory,
            f"mix-{i}",
            [Line(US_A, rng.randint(1, 3))],
            region="US",
            postal_code=rng.choice(["10001", "94105"]),
        )
        for i in range(40)
    ]
    results = together(calls)
    east, west = db.row(US_A, "DC-EAST"), db.row(US_A, "DC-WEST")
    assert east[1] <= east[0] and west[1] <= west[0]
    taken = sum(a.quantity for r in granted(results) for a in r.allocations)
    assert taken == east[1] + west[1]
    for result in granted(results):
        assert len(result.allocations) == 1  # one line, one node: never split


def test_one_key_sent_ten_times_at_once_holds_once(db: Db) -> None:
    db.reset([(CA, "DC-CA", "CA", 5)])
    inventory = PostgresInventory(db.connect, SettableClock())
    results = together([reserve(inventory, "same-key", [Line(CA, 2)]) for _ in range(10)])
    assert len(granted(results)) == 10
    assert len({r.allocations for r in granted(results)}) == 1
    assert db.row(CA, "DC-CA") == (5, 2)


def test_orders_naming_lines_in_opposite_orders_do_not_deadlock(db: Db) -> None:
    db.reset([(US_A, "DC-EAST", "US", 10), (US_B, "DC-EAST", "US", 10)])
    inventory = PostgresInventory(db.connect, SettableClock())
    calls = []
    for i in range(30):
        lines = [Line(US_A, 1), Line(US_B, 1)]
        calls.append(
            reserve(
                inventory,
                f"dl-{i}",
                lines if i % 2 else lines[::-1],
                region="US",
                postal_code="10001",
            )
        )
    results = together(calls)  # a deadlock would raise psycopg.errors.DeadlockDetected here
    assert len(granted(results)) == 10
    assert db.row(US_A, "DC-EAST") == (10, 10)
    assert db.row(US_B, "DC-EAST") == (10, 10)


def test_release_and_commit_alongside_reserve_keep_held_within_bounds(db: Db) -> None:
    db.reset([(CA, "DC-CA", "CA", 10)])
    inventory = PostgresInventory(db.connect, SettableClock())
    for i in range(10):
        assert isinstance(reserve(inventory, f"pre-{i}", [Line(CA, 1)])(), Ok)
    calls: list[Callable[[], Result[Reservation]]] = []
    calls += [partial(inventory.release, idempotency_key=f"pre-{i}") for i in range(5)]
    calls += [partial(inventory.commit, idempotency_key=f"pre-{i}") for i in range(5, 10)]
    calls += [reserve(inventory, f"new-{i}", [Line(CA, 1)]) for i in range(20)]
    results = together(calls)
    assert all(isinstance(r, Ok) for r in results[:10])
    newly = granted(results[10:])
    on_hand, held = db.row(CA, "DC-CA")
    assert on_hand == 5  # five committed units left with their orders
    assert held == len(newly) <= 5  # only the five released units could be taken again


def test_an_expired_hold_frees_its_unit_to_exactly_one_new_taker(db: Db) -> None:
    db.reset([(CA, "DC-CA", "CA", 1)])
    clock = SettableClock()
    inventory = PostgresInventory(db.connect, clock)
    assert isinstance(reserve(inventory, "first", [Line(CA, 1)])(), Ok)
    blocked = together([reserve(inventory, f"early-{i}", [Line(CA, 1)]) for i in range(10)])
    assert granted(blocked) == []
    clock.advance(HOLD)  # expires_at == now: expired
    results = together([reserve(inventory, f"late-{i}", [Line(CA, 1)]) for i in range(THREADS)])
    assert len(granted(results)) == 1
    assert db.row(CA, "DC-CA") == (1, 1)
    first = inventory.release(idempotency_key="first")
    assert isinstance(first, Ok) and first.value.status is HoldStatus.RELEASED
    assert db.row(CA, "DC-CA") == (1, 1)  # releasing the expired hold gives nothing back twice


# ---- the race can fail -----------------------------------------------------------------------


def test_a_read_then_write_reserve_oversells_under_the_same_race(db: Db) -> None:
    """Failing first: without the lock, the conditional update and the CHECK, the same barrier
    lets more than one thread take the last unit."""
    db.reset([(CA, "DC-CA", "CA", 1)])
    db.execute("ALTER TABLE inventory_stock DROP CONSTRAINT inventory_stock_held")
    all_read = threading.Barrier(THREADS)

    def naive() -> bool:
        with db.connect() as conn:
            on_hand, held = conn.execute(
                "SELECT on_hand, held FROM inventory_stock WHERE variant_id = %s", (CA,)
            ).fetchone() or (0, 0)
            all_read.wait()  # every thread reads before any writes: the race, made certain
            if on_hand - held < 1:
                return False
            conn.execute("UPDATE inventory_stock SET held = held + 1 WHERE variant_id = %s", (CA,))
            return True

    taken = together([naive for _ in range(THREADS)])
    on_hand, held = db.row(CA, "DC-CA")
    assert sum(taken) > 1 and held > on_hand  # the oversell the real reserve must never allow
