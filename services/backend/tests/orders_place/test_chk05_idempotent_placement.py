"""CHK-05 (row #21): idempotent order placement. Retries and duplicate messages never create a
second order or charge; replaying the same request 10 times yields one order.

Most runs here drive the placement handlers directly, in the order a model calls them, so ten
replays in sequence and ten at once are cheap. One test runs the whole agent loop ten times on a
scripted model. The recorded model's ten replays are in `test_be05_replay_recorded.py`."""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.agents.orders_place import (
    OrderPlacementTask,
    PlacementResult,
    build_handlers,
    finish,
)
from northwind_backend.errors import AgentResult, RunStatus
from northwind_backend.fakes._base import FaultKind
from northwind_backend.recovery import RetryPolicy
from northwind_llm_replay.scripted import ScriptedLLM

from ._placement import World, first_of
from .test_be05_place_scripted import HAPPY, place, script

pytestmark = [pytest.mark.req("CHK-05"), pytest.mark.req("BE-05")]

FAST = RetryPolicy(max_attempts=3, deadline_seconds=5.0, backoff_seconds=())
FAST_WRITE = RetryPolicy(
    max_attempts=3,
    deadline_seconds=5.0,
    backoff_seconds=(),
    retry_codes=frozenset({"service_unavailable", "audit_unavailable"}),
)
CALLS = {
    "clean": HAPPY,
    "address_error": ["validate_order", "use_address_on_file", *HAPPY],
    "soft_decline": [
        "validate_order",
        "assess_fraud",
        "authorize_payment",
        "authorize_payment",
        "capture_payment",
        "confirm_order",
    ],
    "stock_out": ["validate_order", "escalate_order"],
    "fraud_flag": ["validate_order", "assess_fraud", "escalate_order"],
}
OUTCOME = {
    "clean": "placed",
    "address_error": "placed",
    "soft_decline": "placed",
    "stock_out": "escalated",
    "fraud_flag": "escalated",
}


async def drive(world: World, task: OrderPlacementTask, kind: str, run: int) -> PlacementResult:
    handlers = build_handlers(
        task,
        world.ports(),
        world.audit(),
        correlation_id=f"run-{run}",
        read_policy=FAST,
        write_policy=FAST_WRITE,
    )
    for call in CALLS[kind]:
        await getattr(handlers, call)({"order_id": task.order_id})
    stub = AgentResult(RunStatus.COMPLETE, (), (), handlers.audit.correlation_id)
    return await finish(handlers, stub)


def effects(world: World, order_id: str) -> Counter[str]:
    intents = {i for i, intent in world.payment.intents.items() if intent.order_id == order_id}
    seen: Counter[str] = Counter()
    for state in (world.payment, world.fraud, world.orders):
        for effect in state.effects:
            if effect.reference == order_id or effect.reference in intents:
                seen[effect.operation] += 1
    return seen


def assert_one_order(world: World, row: dict[str, Any], kind: str) -> None:
    oid = row["order_id"]
    seen = effects(world, oid)
    live = [
        i
        for i in world.payment.intents.values()
        if i.order_id == oid and i.status.value != "voided"
    ]
    if OUTCOME[kind] == "placed":
        assert seen["confirm"] == 1 and seen["authorize"] == 1 and seen["capture"] == 1, seen
        assert len(live) == 1 and live[0].captured_minor == world.orders.orders[oid].amount_minor
    else:
        assert seen["escalate"] == 1 and seen["authorize"] == 0 and seen["capture"] == 0, seen
        assert live == []
    assert seen["assess"] <= 1 and seen["use_address_on_file"] <= 1


@pytest.mark.parametrize("kind", sorted(CALLS))
def test_ten_replays_in_a_row_yield_one_order(kind: str) -> None:
    world, row = World.make(), first_of(kind)
    task = world.task(row)

    async def ten() -> list[PlacementResult]:
        return [await drive(world, task, kind, n) for n in range(10)]

    results = asyncio.run(ten())
    assert {r.outcome for r in results} == {OUTCOME[kind]}
    assert_one_order(world, row, kind)


@pytest.mark.parametrize("kind", sorted(CALLS))
def test_ten_replays_at_once_yield_one_order(kind: str) -> None:
    world, row = World.make(), first_of(kind)
    task = world.task(row)

    async def ten() -> list[PlacementResult]:
        return list(await asyncio.gather(*(drive(world, task, kind, n) for n in range(10))))

    results = asyncio.run(ten())
    assert {r.outcome for r in results} == {OUTCOME[kind]}
    assert_one_order(world, row, kind)


def test_two_interleaved_duplicates_use_the_same_keys_and_write_once() -> None:
    """Two runs of one task, step by step in turn: the second presents the first's keys and gets
    its first results, and once the order is confirmed it writes nothing more (security review
    F1: the store, not the run, says whether the order is open)."""
    world, row = World.make(), first_of("soft_decline")
    task = world.task(row)
    runs = [
        build_handlers(
            task,
            world.ports(),
            world.audit(),
            correlation_id=f"run-{n}",
            read_policy=FAST,
            write_policy=FAST_WRITE,
        )
        for n in (1, 2)
    ]

    async def interleave() -> None:
        for call in CALLS["soft_decline"]:
            for h in runs:
                await getattr(h, call)({"order_id": task.order_id})

    asyncio.run(interleave())
    per_run: dict[str, list[str]] = {}
    for e in world.sink.read_all():
        key = e.inputs.get("idempotency_key")
        if e.phase == "attempt" and isinstance(key, str):
            per_run.setdefault(e.correlation_id, []).append(key)
    confirm = f"{task.order_id}:order.confirm"
    assert per_run["run-2"] == [k for k in per_run["run-1"] if k != confirm]
    assert_one_order(world, row, "soft_decline")


def test_a_duplicate_message_with_a_fresh_confirmation_charges_nothing_more() -> None:
    """A second checkout message for the same order, confirmed again: no second intent, capture or
    order. It reports the order as placed, since the store holds it."""
    world, row = World.make(), first_of("clean")

    async def both() -> tuple[PlacementResult, PlacementResult]:
        first = await drive(world, world.task(row), "clean", 1)
        second = await drive(world, world.task(row), "clean", 2)  # a fresh reference
        return first, second

    first, second = asyncio.run(both())
    assert first.outcome == second.outcome == "placed"
    assert_one_order(world, row, "clean")
    # Security review F1: the duplicate never reaches the payment port at all.
    run_two = [e for e in world.sink.read_all() if e.correlation_id == "run-2"]
    assert not [e for e in run_two if e.action.startswith("payment.")]
    refused = [(e.outcome or {}).get("code") for e in run_two if e.action == "agent.refused"]
    assert "order_already_placed" in refused


def test_a_transient_retry_inside_a_run_gives_one_effect() -> None:
    world, row = World.make(), first_of("clean")
    task = world.task(row)
    handlers = build_handlers(
        task,
        world.ports(),
        world.audit(),
        correlation_id="run-1",
        read_policy=FAST,
        write_policy=FAST_WRITE,
    )
    handlers.payment._fake.faults.inject("capture", FaultKind.OUTAGE, times=1)  # type: ignore[attr-defined]

    async def run() -> PlacementResult:
        for call in HAPPY:
            await getattr(handlers, call)({"order_id": task.order_id})
        return await finish(handlers, AgentResult(RunStatus.COMPLETE, (), (), "run-1"))

    assert asyncio.run(run()).outcome == "placed"
    assert_one_order(world, row, "clean")


def test_ten_scripted_agent_runs_yield_one_order(tmp_path: Path) -> None:
    """The whole agent loop, ten times, one task."""
    world, row = World.make(), first_of("clean")
    task = world.task(row)
    for n in range(10):
        with ScriptedLLM() as llm:
            script(llm, row["order_id"], HAPPY)
            result = place(llm, tmp_path / str(n), world, task, correlation_id=f"run-{n}")
        assert result.outcome == "placed"
    assert_one_order(world, row, "clean")
