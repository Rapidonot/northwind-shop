"""CHK-05 criteria (row #21): retries and duplicate messages never create a second order or charge;
replaying the same request 10 times yields one order. Gaps beside the existing CHK-05 file: a rerun
after a doubtful write, duplicates through other instances and runs, a duplicate after an
escalation, and the order store and payment port holding the rule on their own."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from northwind_backend.errors import Ok, ToolError
from northwind_backend.fakes._base import FaultKind
from northwind_backend.fakes.orders import OrderStoreFake
from northwind_backend.fakes.payment import SANDBOX_APPROVED, PaymentFake
from northwind_backend.ports import OrderStatus, PaymentStatus

from ._placement import World, first_of, total_for
from .test_be05_criteria import BEFORE_AUTHORIZE, drive
from .test_be05_place_scripted import HAPPY

pytestmark = pytest.mark.req("CHK-05")


def charges(world: World, order_id: str) -> int:
    return sum(
        1
        for i in world.payment.intents.values()
        if i.order_id == order_id and i.status is not PaymentStatus.VOIDED
    )


def test_a_rerun_after_a_doubtful_capture_yields_one_order_and_one_charge() -> None:
    world, row = World.make(), first_of("clean")
    oid, task = row["order_id"], world.task(row)
    ports = world.ports()
    ports.payment.faults.inject("capture", FaultKind.TIMEOUT_AFTER, times=1)  # type: ignore[attr-defined]
    _, _, first = drive(world, task, HAPPY, ports=ports, correlation_id="run-1")
    assert first is not None and first.outcome == "escalated"
    # The duplicate message: same task, a fresh run on fresh instances.
    _, _, again = drive(world, task, HAPPY, ports=world.ports(), correlation_id="run-2")
    assert again is not None
    ops = [e.operation for e in world.payment.effects]
    assert ops.count("authorize") == 1 and ops.count("capture") <= 1
    assert charges(world, oid) == 1
    assert [e.operation for e in world.orders.effects].count("confirm") <= 1
    assert sum(1 for i in world.payment.intents.values() if i.captured_minor) <= 1


def test_ten_runs_on_separate_instances_charge_and_confirm_once() -> None:
    world, row = World.make(), first_of("clean")
    task = world.task(row)
    for n in range(10):
        _, _, result = drive(world, task, HAPPY, correlation_id=f"run-{n}")
        assert result is not None and result.outcome == "placed"
    ops = [op for op, _ in world.effects()]
    assert ops.count("authorize") == 1 and ops.count("capture") == 1
    assert ops.count("confirm") == 1 and ops.count("assess") == 1
    order = world.orders.orders[row["order_id"]]
    assert order.status is OrderStatus.CONFIRMED
    assert (
        world.payment.intents[order.intent_id or ""].captured_minor == total_for(row).amount_minor
    )


def test_ten_duplicates_of_an_escalated_order_escalate_once_and_charge_nothing() -> None:
    world, row = World.make(), first_of("stock_out")
    task = world.task(row)
    for n in range(10):
        _, _, result = drive(
            world, task, ["validate_order", "escalate_order"], correlation_id=f"run-{n}"
        )
        assert result is not None and result.outcome == "escalated"
        assert result.reason == "out_of_stock" and result.context is not None
    assert [op for op, _ in world.effects()] == ["escalate"]
    assert world.payment.intents == {}


def test_ten_duplicates_of_one_order_in_threads_yield_one_order() -> None:
    world, row = World.make(), first_of("clean")
    task = world.task(row)

    def run(n: int) -> Any:
        return drive(world, task, HAPPY, correlation_id=f"run-{n}")[2]

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(run, range(10)))
    assert all(r is not None and r.outcome == "placed" for r in results)
    ops = [op for op, _ in world.effects()]
    assert ops.count("authorize") == 1 and ops.count("capture") == 1 and ops.count("confirm") == 1
    assert charges(world, row["order_id"]) == 1


def test_a_duplicate_with_a_fresh_confirmation_after_placement_charges_nothing_more() -> None:
    world, row = World.make(), first_of("clean")
    drive(world, world.task(row), HAPPY, correlation_id="run-1")
    before = list(world.effects())
    _, _, dup = drive(world, world.task(row), HAPPY, correlation_id="run-2")
    assert dup is not None and dup.outcome == "placed"
    assert list(world.effects()) == before
    assert charges(world, row["order_id"]) == 1


def test_a_duplicate_that_lost_its_keys_is_still_stopped_by_the_ports() -> None:
    """The ports hold the rule on their own: a second key, a second reference, a second
    instance, and still one payment and one confirmed order."""
    world, row = World.make(), first_of("clean")
    oid, amount = row["order_id"], total_for(row)
    ports = [PaymentFake(state=world.payment) for _ in range(3)]
    outcomes = [
        p.authorize(
            idempotency_key=f"other-key-{n}",
            payment_token=SANDBOX_APPROVED,
            amount=amount,
            order_id=oid,
            confirmation_ref=world.confirm(row),
        )
        for n, p in enumerate(ports)
    ]
    assert isinstance(outcomes[0], Ok)
    assert all(
        isinstance(o, ToolError) and o.code == "order_already_authorized" for o in outcomes[1:]
    )
    assert charges(world, oid) == 1
    stores = [OrderStoreFake(state=world.orders) for _ in range(3)]
    confirms = [
        s.confirm(idempotency_key=f"c{n}", order_id=oid, intent_id="pi_000001", amount=amount)
        for n, s in enumerate(stores)
    ]
    assert isinstance(confirms[0], Ok)
    assert all(isinstance(c, ToolError) and c.code == "already_confirmed" for c in confirms[1:])


def test_a_replay_of_the_early_steps_after_placement_adds_no_effect() -> None:
    world, row = World.make(), first_of("clean")
    task = world.task(row)
    drive(world, task, HAPPY, correlation_id="run-1")
    before = list(world.effects())
    drive(world, task, ["validate_order", *BEFORE_AUTHORIZE[1:]], correlation_id="run-2")
    assert list(world.effects()) == before
