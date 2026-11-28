"""Row #18: the recording windows' hold guard counts exactly the holds BE-04 counts as live.

On a throwaway schema (no conftest.py: that is on the control surface), against
`PostgresInventory` itself: a reservation's hold is live until its expiry, and not at it or after,
and not once released or committed. Tables that don't exist count as none.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from northwind_backend.inventory import HOLD, Line
from northwind_backend.inventory.live_holds import DEFAULT_DATABASE_URL, count_live
from northwind_backend.inventory.postgres import PostgresInventory, apply_schema, load_stock

pytestmark = [pytest.mark.integration, pytest.mark.req("BE-04")]

START = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def schema() -> Iterator[tuple[str, str]]:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    name = f"be04_guard_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        yield url, name
    finally:
        admin.execute(f'DROP SCHEMA "{name}" CASCADE')
        admin.close()


def connector(url: str, name: str) -> Any:
    return lambda: psycopg.connect(url, options=f"-csearch_path={name}")


def test_absent_tables_count_as_none(schema: tuple[str, str]) -> None:
    assert count_live(connector(*schema), START) is None


def test_the_guard_counts_what_bes_store_holds(schema: tuple[str, str]) -> None:
    connect = connector(*schema)
    with connect() as conn:
        apply_schema(conn)
        load_stock(
            conn,
            [
                {"variant_id": "P0001-BLK-REG-M", "node": "DC-CA", "region": "CA", "on_hand": 5},
                {"variant_id": "P0001-BLK-REG-L", "node": "DC-CA", "region": "CA", "on_hand": 5},
            ],
        )
    clock = Clock()
    store = PostgresInventory(connect, clock)
    assert count_live(connect, clock.now) == 0

    def reserve(key: str, variant: str) -> None:
        done = store.reserve(
            idempotency_key=key,
            order_id="O900002",
            region="CA",
            postal_code="M5V 2T6",
            lines=[Line(variant, 1)],
        )
        assert getattr(done, "value", None) is not None, done

    reserve("guard-key-1", "P0001-BLK-REG-M")
    reserve("guard-key-2", "P0001-BLK-REG-L")
    assert count_live(connect, clock.now) == 2
    store.release(idempotency_key="guard-key-1")
    assert count_live(connect, clock.now) == 1
    assert count_live(connect, START + HOLD) == 0  # at its expiry: not live
    assert count_live(connect, START + HOLD - timedelta(seconds=1)) == 1
    store.commit(idempotency_key="guard-key-2")
    assert count_live(connect, clock.now) == 0


def test_a_naive_time_is_refused(schema: tuple[str, str]) -> None:
    with pytest.raises(ValueError):
        count_live(connector(*schema), datetime(2026, 11, 26, 12, 0))  # noqa: DTZ001


@pytest.mark.parametrize("status", ["released", "committed"])
def test_a_settled_reservation_is_not_live_even_unswept(
    schema: tuple[str, str], status: str
) -> None:
    """The store sweeps a hold when it settles, so this state needs SQL to reach. The guard must
    still read it as BE-04's `available` does (the status, not only the sweep, decides)."""
    connect = connector(*schema)
    with connect() as conn:
        apply_schema(conn)
        load_stock(
            conn,
            [{"variant_id": "P0001-BLK-REG-M", "node": "DC-CA", "region": "CA", "on_hand": 5}],
        )
    clock = Clock()
    store = PostgresInventory(connect, clock)
    done = store.reserve(
        idempotency_key="guard-key-3",
        order_id="O900003",
        region="CA",
        postal_code="M5V 2T6",
        lines=[Line("P0001-BLK-REG-M", 2)],
    )
    assert getattr(done, "value", None) is not None, done
    with connect() as conn:
        conn.execute(
            "UPDATE inventory_reservations SET status = %s WHERE idempotency_key = %s",
            (status, "guard-key-3"),
        )
    available = store.available("P0001-BLK-REG-M", "DC-CA")
    assert getattr(available, "value", None) == 5  # BE-04: not held
    assert count_live(connect, clock.now) == 0
