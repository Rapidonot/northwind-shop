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


def assert_at_most_one_of_everything(world: World, row: dict[str, Any], outcome: str) -> None:
    """CHK-05 whatever the interleaving: one intent, capture and confirmed order at most; nothing
    captured unless placed... or, if escalated with money on it, reported in the escalation."""
    oid = row["order_id"]
    seen = effects(world, oid)
    assert seen["authorize"] <= 1 and seen["capture"] <= 1 and seen["confirm"] <= 1, seen
    intents = [i for i in world.payment.intents.values() if i.order_id == oid]
    assert len(intents) <= 1
    if outcome == "placed":
        assert seen["confirm"] == 1 and intents[0].captured_minor > 0
    elif intents:
        stored = world.orders.escalations[oid]["payment"]
        assert stored is not None and stored["intent_id"] == intents[0].intent_id
        assert stored["captured_minor"] == intents[0].captured_minor


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
def test_ten_replays_at_once_never_charge_twice_and_report_money(kind: str) -> None:
    """Ten at once: never a second charge, and any money on an escalated order reported. It can
    pass with no order placed: under the payment claim (F1, the user's
    Option 1) a run that finds another run paying never authorizes and escalates instead, so an
    overlap ends escalated, fail closed, with any live payment reported in the escalation."""
    world, row = World.make(), first_of(kind)
    task = world.task(row)

    async def ten() -> list[PlacementResult]:
        return list(await asyncio.gather(*(drive(world, task, kind, n) for n in range(10))))

    results = asyncio.run(ten())
    outcomes = {r.outcome for r in results}
    assert len(outcomes) == 1 and outcomes <= {"placed", "escalated"}  # C6: never `failed`
    assert_at_most_one_of_everything(world, row, results[0].outcome)
    if OUTCOME[kind] == "escalated":  # kinds that never pay: nothing the claim changes (C6)
        assert outcomes == {"escalated"}
        assert_one_order(world, row, kind)


def test_two_duplicates_with_the_same_keys_never_both_get_the_claim() -> None:
    """The user's test for the claim (F1): two runs of one task, the same keys, step by step in
    turn. Only one claims; the other never authorizes and escalates as a duplicate in progress, and
    the stored escalation reports the claim owner's authorization."""
    world, row = World.make(), first_of("clean")
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
        for call in HAPPY:
            for h in runs:
                await getattr(h, call)({"order_id": task.order_id})

    asyncio.run(interleave())
    oid = task.order_id
    assert [step for order, step in world.orders.claims if order == oid].count("claimed") == 1
    keys: dict[str, list[str]] = {}
    for e in world.sink.read_all():
        key = e.inputs.get("idempotency_key")
        if e.phase == "attempt" and isinstance(key, str):
            keys.setdefault(e.correlation_id, []).append(key)
    # C5: both runs present the same order-ID keys for the steps they share.
    assert f"{oid}:fraud.assess" in keys["run-1"] and f"{oid}:fraud.assess" in keys["run-2"]
    shared = set(keys["run-2"]) - {f"{oid}:order.escalate"}
    assert shared <= set(keys["run-1"])
    authorizes = [e for e in world.payment.effects if e.operation == "authorize"]
    assert len(authorizes) == 1
    second = [e for e in world.sink.read_all() if e.correlation_id == "run-2"]
    assert not [e for e in second if e.action == "payment.authorize"]
    refused = [(e.outcome or {}).get("code") for e in second if e.action == "agent.refused"]
    assert "duplicate_in_progress" in refused
    order = world.orders.orders[oid]
    assert order.status.value == "escalated" and order.escalation == "duplicate_in_progress"
    stored = world.orders.escalations[oid]
    assert stored["payment"] is not None
    assert stored["payment"]["intent_id"] == authorizes[0].reference
    assert not [e for e in world.payment.effects if e.operation == "capture"]


def test_concurrent_claims_from_ten_runs_give_exactly_one_owner() -> None:
    """The store's compare-and-set alone, from ten threads: one owner, nine refusals."""
    from concurrent.futures import ThreadPoolExecutor

    world, row = World.make(), first_of("clean")
    store = world.ports().orders
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(
            pool.map(
                lambda n: store.claim_payment(
                    order_id=row["order_id"], run_id=f"run-identity-{n:02d}"
                ),
                range(10),
            )
        )
    codes = [getattr(r, "code", "ok") for r in results]
    assert codes.count("ok") == 1 and codes.count("claimed_by_another_run") == 9


def test_a_rerun_after_a_crash_finds_the_claim_and_escalates() -> None:
    """Fail closed: a run that claimed and died leaves the order paying; a rerun is not the owner,
    never authorizes, and escalates with the claim and its payment reported."""
    world, row = World.make(), first_of("clean")
    task = world.task(row)
    crashed = build_handlers(
        task,
        world.ports(),
        world.audit(),
        correlation_id="run-1",
        read_policy=FAST,
        write_policy=FAST_WRITE,
    )

    async def run() -> PlacementResult:
        for call in HAPPY[:3]:  # claimed and authorized, then nothing: no finish()
            await getattr(crashed, call)({"order_id": task.order_id})
        return await drive(world, task, "clean", 2)

    rerun = asyncio.run(run())
    assert rerun.outcome == "escalated" and rerun.reason == "duplicate_in_progress"
    assert len([e for e in world.payment.effects if e.operation == "authorize"]) == 1
    assert rerun.context is not None and rerun.context["payment"]["claim"] == "another_run"
    assert world.orders.escalations[task.order_id]["payment"]["intent_id"] is not None


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
