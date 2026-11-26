"""BE-04 (row #25): one contract, two implementations. The in-memory fake and `PostgresInventory`
must behave the same for reservation, idempotency, dry runs, all-or-nothing, the 15-minute hold,
commit, release and the two error kinds. Time moves only by setting the injected clock: no sleep,
no wall clock. The Postgres param is an integration test (needs `make up`); it gets a throwaway
schema, never the shared tables. The concurrency race tests live in `test_be04_race.py`."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from northwind_backend.errors import Ok, Result, ToolError
from northwind_backend.inventory import (
    HOLD,
    HoldStatus,
    InventoryFake,
    InventoryPort,
    InventoryState,
    Line,
    Reservation,
    StockOut,
)
from northwind_backend.inventory.postgres import PostgresInventory, apply_schema, load_stock
from northwind_rag.cli import DEFAULT_DATABASE_URL

pytestmark = pytest.mark.req("BE-04")

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
A = "P0001-AAA"
B = "P0002-BBB"
EAST, WEST = "DC-EAST", "DC-WEST"
REGIONS = {"US": (EAST, WEST)}
ROWS: list[dict[str, Any]] = [
    {"variant_id": A, "node": EAST, "region": "US", "on_hand": 5},
    {"variant_id": A, "node": WEST, "region": "US", "on_hand": 3},
    {"variant_id": B, "node": EAST, "region": "US", "on_hand": 2},
    {"variant_id": B, "node": WEST, "region": "US", "on_hand": 2},
]
EAST_POSTAL = "10001"


class SetClock:
    """A settable clock returning an aware UTC time."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@dataclass
class Harness:
    inv: InventoryPort
    clock: SetClock
    take_down: Callable[[], None]
    bring_up: Callable[[], None]


Build = Callable[[Sequence[Mapping[str, Any]]], Harness]


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def _fake(rows: Sequence[Mapping[str, Any]]) -> Harness:
    clock = SetClock()
    state = InventoryState.from_rows(rows)
    inv = InventoryFake(clock, state=state, regions=REGIONS)

    def down(value: bool) -> Callable[[], None]:
        def set_down() -> None:
            state.down = value

        return set_down

    return Harness(inv, clock, down(True), down(False))


@pytest.fixture(
    params=[
        pytest.param("fake", id="fake"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.integration),
    ]
)
def build(request: pytest.FixtureRequest) -> Iterator[Build]:
    """`build(rows)` gives a fresh inventory seeded with `rows`. Postgres gets its own schema."""
    if request.param == "fake":
        yield _fake
        return
    schema = f"be04_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(_database_url(), autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    opened: list[psycopg.Connection[Any]] = []

    def connect() -> psycopg.Connection[Any]:
        conn: psycopg.Connection[Any] = psycopg.connect(
            _database_url(), autocommit=True, options=f"-csearch_path={schema},public"
        )
        opened.append(conn)
        return conn

    def make(rows: Sequence[Mapping[str, Any]]) -> Harness:
        with connect() as seed:
            apply_schema(seed)
            load_stock(seed, rows)
        clock = SetClock()
        inv = PostgresInventory(connect, clock, regions=REGIONS)

        def take_down() -> None:
            def refuse() -> psycopg.Connection[Any]:
                raise psycopg.OperationalError("the server is gone")

            inv.connect = refuse

        def bring_up() -> None:
            inv.connect = connect

        return Harness(inv, clock, take_down, bring_up)

    try:
        yield make
    finally:
        for conn in opened:
            if not conn.closed:
                conn.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


@pytest.fixture
def h(build: Build) -> Harness:
    return build(ROWS)


# ---- helpers -------------------------------------------------------------------------------


def ok[T](result: Result[T]) -> T:
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    return result.value


def err(result: Result[Any], code: str, category: str) -> ToolError:
    assert isinstance(result, ToolError), f"expected {code}, got {result!r}"
    assert (result.code, result.error_category.value) == (code, category)
    return result


def reserve(
    h: Harness,
    key: str = "key-1",
    lines: Sequence[Line] = (Line(A, 1),),
    *,
    order_id: str = "O000001",
    postal: str = EAST_POSTAL,
    dry_run: bool = False,
) -> Result[Reservation]:
    return h.inv.reserve(
        idempotency_key=key,
        order_id=order_id,
        region="US",
        postal_code=postal,
        lines=lines,
        dry_run=dry_run,
    )


def avail(h: Harness, variant: str = A, node: str = EAST) -> int:
    return ok(h.inv.available(variant, node))


def total(h: Harness, variant: str = A) -> int:
    return avail(h, variant, EAST) + avail(h, variant, WEST)


# ---- the hold itself -----------------------------------------------------------------------


def test_the_hold_is_fifteen_minutes() -> None:
    assert HOLD == timedelta(minutes=15)


def test_reserve_holds_units_at_one_node_for_the_hold(h: Harness) -> None:
    held = ok(reserve(h, lines=[Line(A, 2), Line(B, 1)]))
    assert held.status is HoldStatus.HELD and not held.dry_run
    assert held.expires_at == T0 + HOLD
    assert {(a.variant_id, a.quantity) for a in held.allocations} == {(A, 2), (B, 1)}
    assert avail(h, A, EAST) == 3 and avail(h, B, EAST) == 1
    assert avail(h, A, WEST) == 3 and avail(h, B, WEST) == 2  # the other node is untouched


def test_available_of_an_unknown_variant_or_node_is_an_empty_ok_not_an_error(h: Harness) -> None:
    assert ok(h.inv.available("P9999-ZZZ", EAST)) == 0
    assert ok(h.inv.available(A, "DC-NOWHERE")) == 0


# ---- idempotency ---------------------------------------------------------------------------


def test_a_repeat_with_the_same_key_returns_the_first_result_and_takes_units_once(
    h: Harness,
) -> None:
    first = ok(reserve(h, lines=[Line(A, 2)]))
    again = ok(reserve(h, lines=[Line(A, 2)]))
    assert again == first
    assert avail(h, A, EAST) == 3, "the repeat took the units a second time"


def test_a_repeat_may_list_the_lines_in_another_order(h: Harness) -> None:
    first = ok(reserve(h, lines=[Line(A, 1), Line(B, 1)]))
    again = ok(reserve(h, lines=[Line(B, 1), Line(A, 1)]))
    assert again == first
    assert avail(h, A, EAST) == 4 and avail(h, B, EAST) == 1


@pytest.mark.parametrize(
    ("other_lines", "other_order", "other_postal"),
    [
        ([Line(A, 3)], "O000001", EAST_POSTAL),  # another quantity
        ([Line(B, 1)], "O000001", EAST_POSTAL),  # another variant
        ([Line(A, 1)], "O000002", EAST_POSTAL),  # another order
        ([Line(A, 1)], "O000001", "90001"),  # another postal code
    ],
)
def test_the_same_key_for_another_request_is_a_validation_conflict_and_changes_nothing(
    h: Harness, other_lines: list[Line], other_order: str, other_postal: str
) -> None:
    ok(reserve(h, lines=[Line(A, 1)]))
    err(
        reserve(h, lines=other_lines, order_id=other_order, postal=other_postal),
        "idempotency_conflict",
        "validation",
    )
    assert total(h, A) == 7 and total(h, B) == 4


# ---- dry run -------------------------------------------------------------------------------


def test_a_dry_run_holds_nothing_and_says_where_the_units_would_come_from(h: Harness) -> None:
    planned = ok(reserve(h, lines=[Line(A, 2)], dry_run=True))
    assert planned.dry_run and planned.nodes() == (EAST,)
    assert total(h, A) == 8, "a dry run held units"


def test_a_dry_run_records_no_key(h: Harness) -> None:
    ok(reserve(h, key="key-d", lines=[Line(A, 2)], dry_run=True))
    # The key is free: a real reservation under it, for a different request, is not a conflict.
    real = ok(reserve(h, key="key-d", lines=[Line(B, 1)]))
    assert not real.dry_run and avail(h, B, EAST) == 1
    # And a dry run never reads the key either: it ignores the real reservation under it.
    again = ok(reserve(h, key="key-d", lines=[Line(A, 1)], dry_run=True))
    assert again.dry_run


def test_a_dry_run_that_cannot_be_filled_is_out_of_stock_and_still_holds_nothing(
    h: Harness,
) -> None:
    refused = reserve(h, lines=[Line(A, 99)], dry_run=True)
    err(refused, "out_of_stock", "business")
    assert total(h, A) == 8


def test_a_dry_run_sees_live_holds_but_not_expired_ones(h: Harness) -> None:
    ok(reserve(h, key="k-a", lines=[Line(A, 5)], order_id="O000001"))
    ok(reserve(h, key="k-b", lines=[Line(A, 3)], order_id="O000002", postal="90001"))
    err(
        reserve(h, key="k-c", lines=[Line(A, 1)], order_id="O000003", dry_run=True),
        "out_of_stock",
        "business",
    )
    h.clock.advance(HOLD)
    assert ok(reserve(h, key="k-c", lines=[Line(A, 1)], order_id="O000003", dry_run=True)).dry_run


# ---- all or nothing ------------------------------------------------------------------------


def test_a_multi_line_order_with_one_short_line_reserves_nothing(h: Harness) -> None:
    refused = reserve(h, lines=[Line(A, 1), Line(B, 99)])
    stock_out = err(refused, "out_of_stock", "business")
    assert isinstance(stock_out, StockOut)
    assert [(s.variant_id, s.wanted, s.available) for s in stock_out.short] == [(B, 99, 2)]
    assert total(h, A) == 8 and total(h, B) == 4, "a line was held although the order failed"
    assert stock_out.is_retryable is False


def test_every_short_line_is_reported(h: Harness) -> None:
    refused = reserve(h, lines=[Line(A, 6), Line(B, 3)])
    stock_out = err(refused, "out_of_stock", "business")
    assert isinstance(stock_out, StockOut)
    assert {s.variant_id: (s.wanted, s.available) for s in stock_out.short} == {
        A: (6, 5),
        B: (3, 2),
    }


def test_a_refused_key_is_not_burned_so_a_retry_with_it_can_succeed(h: Harness) -> None:
    err(reserve(h, key="key-r", lines=[Line(A, 6)]), "out_of_stock", "business")
    again = ok(reserve(h, key="key-r", lines=[Line(A, 5)]))
    assert again.status is HoldStatus.HELD


def test_the_last_unit_goes_to_exactly_one_of_two_orders(h: Harness) -> None:
    ok(reserve(h, key="k-1", lines=[Line(B, 2)], order_id="O000001"))
    ok(reserve(h, key="k-2", lines=[Line(B, 2)], order_id="O000002", postal="90001"))
    err(reserve(h, key="k-3", lines=[Line(B, 1)], order_id="O000003"), "out_of_stock", "business")
    assert total(h, B) == 0


def test_out_of_stock_carries_substitutes_keyed_by_the_short_variant(h: Harness) -> None:
    refused = err(reserve(h, lines=[Line(A, 99)]), "out_of_stock", "business")
    envelope = refused.to_envelope()
    assert envelope["isError"] is True and envelope["errorCategory"] == "business"
    assert envelope["isRetryable"] is False and envelope["code"] == "out_of_stock"
    assert envelope["short"] == [{"variant_id": A, "wanted": 99, "available": 5}]
    assert set(envelope["substitutes"]) == {A}


# ---- the expiry boundary -------------------------------------------------------------------


def test_a_hold_counts_up_to_the_instant_before_expiry_and_not_at_expires_at(h: Harness) -> None:
    held = ok(reserve(h, lines=[Line(A, 5)]))
    assert held.expires_at == T0 + HOLD
    h.clock.now = held.expires_at - timedelta(microseconds=1)
    assert avail(h, A, EAST) == 0
    h.clock.now = held.expires_at  # expires_at == now is expired
    assert avail(h, A, EAST) == 5
    h.clock.now = held.expires_at + timedelta(hours=1)
    assert avail(h, A, EAST) == 5


def test_an_expired_hold_does_not_block_another_order_from_taking_the_units(h: Harness) -> None:
    ok(reserve(h, key="k-1", lines=[Line(B, 2)], order_id="O000001"))
    ok(reserve(h, key="k-2", lines=[Line(B, 2)], order_id="O000002", postal="90001"))
    h.clock.advance(HOLD)
    taken = ok(reserve(h, key="k-3", lines=[Line(B, 2)], order_id="O000003"))
    assert taken.status is HoldStatus.HELD and taken.expires_at == T0 + HOLD + HOLD
    assert avail(h, B, EAST) + avail(h, B, WEST) == 2


def test_a_repeat_after_expiry_reports_the_reservation_as_expired(h: Harness) -> None:
    ok(reserve(h, lines=[Line(A, 1)]))
    h.clock.advance(HOLD)
    again = ok(reserve(h, lines=[Line(A, 1)]))
    assert again.status is HoldStatus.EXPIRED
    assert total(h, A) == 8, "an expired hold was counted again by its own repeat"


def test_a_naive_clock_is_refused(build: Build) -> None:
    h = build(ROWS)
    h.clock.now = datetime(2026, 11, 26, 12, 0)  # noqa: DTZ001 - the point of the test
    with pytest.raises(ValueError, match="aware"):
        reserve(h)


# ---- commit --------------------------------------------------------------------------------


def test_commit_of_a_live_hold_takes_the_units_out_of_on_hand_for_good(h: Harness) -> None:
    ok(reserve(h, lines=[Line(A, 2)]))
    done = ok(h.inv.commit(idempotency_key="key-1"))
    assert done.status is HoldStatus.COMMITTED
    assert avail(h, A, EAST) == 3
    h.clock.advance(HOLD * 4)  # the old expiry passes: nothing comes back, nothing is taken twice
    assert avail(h, A, EAST) == 3


def test_commit_twice_is_the_same_result_and_takes_the_units_once(h: Harness) -> None:
    ok(reserve(h, lines=[Line(A, 2)]))
    first = ok(h.inv.commit(idempotency_key="key-1"))
    second = ok(h.inv.commit(idempotency_key="key-1"))
    assert second == first and avail(h, A, EAST) == 3


def test_commit_after_expiry_takes_the_units_again_from_the_same_node(h: Harness) -> None:
    ok(reserve(h, lines=[Line(A, 2)]))
    h.clock.advance(HOLD + timedelta(minutes=1))
    assert avail(h, A, EAST) == 5
    done = ok(h.inv.commit(idempotency_key="key-1"))
    assert done.status is HoldStatus.COMMITTED
    assert [(a.node, a.quantity) for a in done.allocations] == [(EAST, 2)]
    assert avail(h, A, EAST) == 3 and avail(h, A, WEST) == 3


def test_commit_after_expiry_fails_as_hold_expired_when_the_units_are_gone(h: Harness) -> None:
    ok(reserve(h, key="k-1", lines=[Line(B, 2)], order_id="O000001"))
    h.clock.advance(HOLD)
    # Someone else takes both units at that node, and the other node's too.
    ok(reserve(h, key="k-2", lines=[Line(B, 2)], order_id="O000002"))
    ok(reserve(h, key="k-3", lines=[Line(B, 2)], order_id="O000003", postal="90001"))
    refused = err(h.inv.commit(idempotency_key="k-1"), "hold_expired", "business")
    assert refused.is_retryable is False
    assert avail(h, B, EAST) == 0 and avail(h, B, WEST) == 0, "the failed commit took units"
    # The other orders' holds are intact and still commit.
    assert ok(h.inv.commit(idempotency_key="k-2")).status is HoldStatus.COMMITTED


def test_a_partly_gone_expired_commit_is_all_or_nothing(h: Harness) -> None:
    ok(reserve(h, key="k-1", lines=[Line(A, 1), Line(B, 2)], order_id="O000001"))
    h.clock.advance(HOLD)
    ok(reserve(h, key="k-2", lines=[Line(B, 2)], order_id="O000002"))  # B at EAST is gone
    err(h.inv.commit(idempotency_key="k-1"), "hold_expired", "business")
    assert avail(h, A, EAST) == 5, "the line that still fit was taken by a failed commit"


# ---- release -------------------------------------------------------------------------------


def test_release_gives_the_units_back_and_is_idempotent(h: Harness) -> None:
    ok(reserve(h, lines=[Line(A, 2)]))
    first = ok(h.inv.release(idempotency_key="key-1"))
    assert first.status is HoldStatus.RELEASED and avail(h, A, EAST) == 5
    second = ok(h.inv.release(idempotency_key="key-1"))
    assert second.status is HoldStatus.RELEASED and avail(h, A, EAST) == 5


def test_commit_after_release_is_hold_released(h: Harness) -> None:
    ok(reserve(h, lines=[Line(A, 2)]))
    ok(h.inv.release(idempotency_key="key-1"))
    err(h.inv.commit(idempotency_key="key-1"), "hold_released", "business")
    assert avail(h, A, EAST) == 5
    h.clock.advance(HOLD)  # the same after expiry: a release is final
    err(h.inv.commit(idempotency_key="key-1"), "hold_released", "business")
    assert avail(h, A, EAST) == 5


def test_release_after_commit_is_a_no_op(h: Harness) -> None:
    ok(reserve(h, lines=[Line(A, 2)]))
    ok(h.inv.commit(idempotency_key="key-1"))
    after = ok(h.inv.release(idempotency_key="key-1"))
    assert after.status is HoldStatus.COMMITTED
    assert avail(h, A, EAST) == 3, "release after commit gave committed units back"


def test_release_after_expiry_is_a_no_op_and_gives_nothing_back_twice(h: Harness) -> None:
    ok(reserve(h, lines=[Line(A, 2)]))
    h.clock.advance(HOLD)
    after = ok(h.inv.release(idempotency_key="key-1"))
    assert after.status is HoldStatus.EXPIRED
    assert avail(h, A, EAST) == 5
    ok(reserve(h, key="k-2", lines=[Line(A, 5)], order_id="O000002"))
    ok(h.inv.release(idempotency_key="key-1"))
    assert avail(h, A, EAST) == 0, "a second release of an expired hold freed another order's units"


@pytest.mark.parametrize("call", ["commit", "release"])
def test_an_unknown_key_is_a_validation_error(h: Harness, call: str) -> None:
    result = getattr(h.inv, call)(idempotency_key="no-such-key")
    err(result, "reservation_not_found", "validation")


# ---- refused input -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lines",
    [
        [Line(A, 0)],
        [Line(A, -1)],
        [Line(A, 1), Line(B, -3)],
        [Line(A, True)],  # a bool is not a quantity
        [Line(A, 1), Line(A, 1)],  # a variant on two lines
        [Line(A, 2), Line(A, 3)],  # each fits, the sum (5) would too: still two lines
        [],
    ],
    ids=["zero", "negative", "negative-second", "bool", "duplicate", "duplicate-fits", "empty"],
)
@pytest.mark.parametrize("dry_run", [False, True])
def test_a_bad_line_list_is_refused_as_validation_and_holds_nothing(
    h: Harness, lines: list[Line], dry_run: bool
) -> None:
    refused = reserve(h, lines=lines, dry_run=dry_run)
    err(refused, "invalid_lines", "validation")
    assert total(h, A) == 8 and total(h, B) == 4


def test_a_duplicate_that_would_overflow_when_summed_is_refused_not_out_of_stock(
    h: Harness,
) -> None:
    refused = reserve(h, lines=[Line(A, 4), Line(A, 4)])  # each fits EAST (5); 8 does not
    err(refused, "invalid_lines", "validation")
    assert total(h, A) == 8


@pytest.mark.parametrize("key", ["", "has space", "x" * 129, "k;drop"])
def test_a_malformed_key_is_refused_on_every_write(h: Harness, key: str) -> None:
    for result in (
        reserve(h, key=key),
        h.inv.commit(idempotency_key=key),
        h.inv.release(idempotency_key=key),
    ):
        err(result, "invalid_idempotency_key", "validation")


def test_a_malformed_region_order_or_postal_is_validation(h: Harness) -> None:
    err(
        h.inv.reserve(
            idempotency_key="k",
            order_id="O000001",
            region="MARS",
            postal_code="1",
            lines=[Line(A, 1)],
        ),
        "invalid_region",
        "validation",
    )
    err(reserve(h, order_id="not-an-order"), "invalid_order_id", "validation")
    err(reserve(h, postal="1;drop table"), "invalid_postal_code", "validation")


# ---- outage versus out of stock (hard rule 10) ---------------------------------------------


def test_an_outage_is_a_retryable_transient_error_on_every_call(h: Harness) -> None:
    ok(reserve(h, key="key-held", lines=[Line(A, 1)]))
    h.take_down()
    for result in (
        reserve(h, key="key-new"),
        reserve(h, key="key-dry", dry_run=True),
        h.inv.commit(idempotency_key="key-held"),
        h.inv.release(idempotency_key="key-held"),
        h.inv.available(A, EAST),
    ):
        down = err(result, "inventory_unavailable", "transient")
        assert down.is_retryable is True and down.to_envelope()["isError"] is True


def test_an_outage_changes_nothing_and_a_retry_with_the_same_key_then_works(h: Harness) -> None:
    ok(reserve(h, key="key-held", lines=[Line(A, 1)]))
    h.take_down()
    err(reserve(h, key="key-new", lines=[Line(A, 2)]), "inventory_unavailable", "transient")
    err(h.inv.commit(idempotency_key="key-held"), "inventory_unavailable", "transient")
    h.bring_up()
    assert total(h, A) == 7, "the outage lost or invented a hold"
    assert ok(reserve(h, key="key-new", lines=[Line(A, 2)])).status is HoldStatus.HELD
    assert ok(h.inv.commit(idempotency_key="key-held")).status is HoldStatus.COMMITTED


def test_out_of_stock_and_an_outage_are_told_apart_by_code_category_and_retry(h: Harness) -> None:
    short = err(reserve(h, key="key-a", lines=[Line(A, 99)]), "out_of_stock", "business")
    h.take_down()
    down = err(reserve(h, key="key-b", lines=[Line(A, 99)]), "inventory_unavailable", "transient")
    assert (short.code, short.error_category, short.is_retryable) != (
        down.code,
        down.error_category,
        down.is_retryable,
    )
    assert short.is_retryable is False and down.is_retryable is True
    assert json.dumps(short.to_envelope()) != json.dumps(down.to_envelope())
    assert not isinstance(down, StockOut)
