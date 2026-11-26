"""Row #25 (BE-04) wired into placement, as code only: reserve before payment, commit before
capture, release on escalation. No new tool, prompt, description or schema (the user's change 1).

The $0 pin: every order in `data/backend/orders.jsonl` runs through the placement handlers, with the
replay harness's concurrency, twice, once against the fixtures' real stock and once against stock so
deep that a reservation can never be refused (which behaves as no reservation). Every tool result
the model would read must be byte-identical between the two runs, and no reservation may be
refused. So a successful reservation changes nothing the model sees, and with the fitted data
(`BACKEND_DATA_VERSION` 3) no recorded order is refused. The recorded replays of `backend-replay`,
`backend-audit` and `backend-orchestrator` (0 misses) are the model-side half of the proof.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from northwind_backend.agents.escalation import INVALID_LINE, RECOMMENDED
from northwind_backend.agents.orders_place import (
    OrderPlacementTask,
    PlacementResult,
    build_handlers,
    finish,
)
from northwind_backend.errors import AgentResult, Ok, RunStatus
from northwind_backend.inventory import HoldStatus, InventoryState
from northwind_backend.ports import OrderStatus

from ._placement import World, first_of, orders
from ._replay import run_all

pytestmark = pytest.mark.req("BE-04")

DEEP = 1_000_000


def _deep_stock() -> InventoryState:
    state = InventoryState.from_fixtures()
    for row in state.rows.values():
        row.on_hand = DEEP
    return state


def _body(payload: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(payload["content"][0]["text"])
    return body


Seen = dict[str, list[str]]


def recording(
    seen: Seen,
) -> Callable[[World, dict[str, Any], str, str], Awaitable[PlacementResult]]:
    """The system prompt's steps as a rule (as `test_be05_replay_rule_driven.py`), recording the
    exact text of every tool result, which is what the model would read."""

    async def place(world: World, row: dict[str, Any], ref: str, cid: str) -> PlacementResult:
        task = OrderPlacementTask(row["order_id"], ref)
        h = build_handlers(task, world.ports(), world.audit(), correlation_id=cid)
        arg = {"order_id": task.order_id}
        texts = seen.setdefault(task.order_id, [])

        async def call(step: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> Any:
            payload = await step(arg)
            texts.append(json.dumps(payload, sort_keys=True))
            return _body(payload)

        async def escalate() -> PlacementResult:
            await call(h.escalate_order)
            return await finish(h, AgentResult(RunStatus.COMPLETE, (), (), cid))

        checked = await call(h.validate_order)
        if checked.get("value", {}).get("issues") == ["address_undeliverable"]:
            await call(h.use_address_on_file)
            checked = await call(h.validate_order)
        if checked["isError"] or checked["value"]["issues"]:
            return await escalate()
        scored = await call(h.assess_fraud)
        if scored["isError"] or scored["value"]["decision"] != "allow":
            return await escalate()
        paid = await call(h.authorize_payment)
        if paid["isError"] and paid.get("code") == "soft_decline":
            paid = await call(h.authorize_payment)
        if paid["isError"]:
            return await escalate()
        for step in (h.capture_payment, h.confirm_order):
            if (await call(step))["isError"]:
                return await escalate()
        return await finish(h, AgentResult(RunStatus.COMPLETE, (), (), cid))

    return place


def _run(world: World) -> tuple[Seen, dict[str, list[PlacementResult]]]:
    seen: Seen = {}
    results = asyncio.run(run_all(world, list(orders()), recording(seen)))
    return seen, results


def test_every_tool_result_is_byte_identical_with_real_stock_and_with_stock_no_hold_can_lack() -> (
    None
):
    real_world = World.make()
    real, real_results = _run(real_world)
    deep_world = World.make()
    deep_world.inventory = _deep_stock()
    deep, _ = _run(deep_world)
    assert len(real) == len(orders()) == 1000
    changed = sorted(oid for oid in real if real[oid] != deep[oid])
    assert changed == [], f"{len(changed)} orders' tool results changed: {changed[:10]}"
    refused = [
        e
        for e in real_world.sink.read_all()
        if e.action == "inventory.reserve" and e.phase == "outcome" and not (e.outcome or {})["ok"]
    ]
    assert refused == []
    placed = [oid for oid, runs in real_results.items() if runs[-1].outcome == "placed"]
    assert len(placed) == 950


def test_every_placed_order_committed_its_stock_and_every_escalated_one_holds_none() -> None:
    world = World.make()
    _, results = _run(world)
    records = world.inventory.records
    for oid, runs in results.items():
        record = records.get(f"{oid}:inventory.reserve")
        if runs[-1].outcome == "placed":
            assert record is not None and record.status is HoldStatus.COMMITTED, oid
        elif record is not None:
            assert record.status is HoldStatus.RELEASED, oid
    for (variant, node), row in world.inventory.rows.items():
        assert row.held == 0, (variant, node)  # nothing left held after the replay


# ---- behaviour ---------------------------------------------------------------------------------


def _handlers(world: World, row: dict[str, Any]) -> Any:
    return build_handlers(world.task(row), world.ports(), world.audit(), correlation_id="c-1")


async def _to_authorize(h: Any, arg: dict[str, str]) -> None:
    assert _body(await h.validate_order(arg))["value"]["issues"] == []
    assert _body(await h.assess_fraud(arg))["value"]["decision"] == "allow"


def test_stock_is_reserved_after_the_claim_and_before_the_first_authorization() -> None:
    world, row = World.make(), first_of("clean")
    h = _handlers(world, row)
    arg = {"order_id": row["order_id"]}

    async def go() -> None:
        await _to_authorize(h, arg)
        await h.authorize_payment(arg)

    asyncio.run(go())
    actions = [e.action for e in world.sink.read_all() if e.phase == "attempt"]
    claim = actions.index("order.claim_payment")
    reserve = actions.index("inventory.reserve")
    authorize = actions.index("payment.authorize")
    assert claim < reserve < authorize


def test_a_refused_reservation_escalates_as_out_of_stock_and_never_authorizes() -> None:
    world, row = World.make(), first_of("clean")
    for line in row["lines"]:  # another order took every unit after validation read the stock
        for (variant, _node), stock in world.inventory.rows.items():
            if variant == line["variant_id"]:
                stock.on_hand = stock.held = 0
    h = _handlers(world, row)
    arg = {"order_id": row["order_id"]}

    async def go() -> dict[str, Any]:
        await _to_authorize(h, arg)
        return _body(await h.authorize_payment(arg))

    body = asyncio.run(go())
    assert body["isError"] and body["code"] == "order_escalated"
    assert world.payment.intents == {}
    stored = world.orders.orders[row["order_id"]]
    assert stored.status is OrderStatus.ESCALATED and stored.escalation == "out_of_stock"


def test_a_hard_decline_escalation_releases_the_hold() -> None:
    world = World.make(soft_decline_retry_approves=False)
    row = first_of("soft_decline")
    h = _handlers(world, row)
    arg = {"order_id": row["order_id"]}

    async def go() -> None:
        await _to_authorize(h, arg)
        await h.authorize_payment(arg)
        await h.authorize_payment(arg)
        await h.escalate_order(arg)

    asyncio.run(go())
    record = world.inventory.records[f"{row['order_id']}:inventory.reserve"]
    assert record.status is HoldStatus.RELEASED
    assert all(r.held == 0 for r in world.inventory.rows.values())


def test_a_soft_decline_retry_keeps_the_one_hold() -> None:
    world, row = World.make(), first_of("soft_decline")
    h = _handlers(world, row)
    arg = {"order_id": row["order_id"]}

    async def go() -> None:
        await _to_authorize(h, arg)
        await h.authorize_payment(arg)
        await h.authorize_payment(arg)

    asyncio.run(go())
    reserves = [
        e for e in world.sink.read_all() if e.action == "inventory.reserve" and e.phase == "attempt"
    ]
    assert len(reserves) == 1
    held = sum(r.held for r in world.inventory.rows.values())
    assert held == sum(line["quantity"] for line in row["lines"])


def test_the_hold_is_committed_before_capture_so_no_money_is_captured_without_it() -> None:
    world, row = World.make(), first_of("clean")
    h = _handlers(world, row)
    arg = {"order_id": row["order_id"]}

    async def go() -> dict[str, Any]:
        await _to_authorize(h, arg)
        await h.authorize_payment(arg)
        key = f"{row['order_id']}:inventory.reserve"
        assert isinstance(world.ports().inventory.release(idempotency_key=key), Ok)
        return _body(await h.capture_payment(arg))  # its hold is gone: commit refuses

    body = asyncio.run(go())
    assert body["isError"]
    assert all(i.captured_minor == 0 for i in world.payment.intents.values())
    actions = [e.action for e in world.sink.read_all() if e.phase == "attempt"]
    assert "payment.capture" not in actions


def test_a_placed_order_takes_its_units_out_of_on_hand() -> None:
    world, row = World.make(), first_of("clean")
    before = dict((k, r.on_hand) for k, r in world.inventory.rows.items())
    h = _handlers(world, row)
    arg = {"order_id": row["order_id"]}

    async def go() -> None:
        await _to_authorize(h, arg)
        for step in (h.authorize_payment, h.capture_payment, h.confirm_order):
            assert not _body(await step(arg))["isError"]

    asyncio.run(go())
    taken = sum(before[k] - r.on_hand for k, r in world.inventory.rows.items())
    assert taken == sum(line["quantity"] for line in row["lines"])
    assert all(r.held == 0 for r in world.inventory.rows.values())


def test_the_inventory_view_audits_inputs_by_reference_without_the_postal_code() -> None:
    world, row = World.make(), first_of("clean")
    h = _handlers(world, row)
    arg = {"order_id": row["order_id"]}

    async def go() -> None:
        await _to_authorize(h, arg)
        await h.authorize_payment(arg)

    asyncio.run(go())
    event = next(e for e in world.sink.read_all() if e.action == "inventory.reserve")
    assert set(event.inputs) == {"idempotency_key", "order_id", "region", "lines", "dry_run"}
    assert row["shipping_postal_code"] not in json.dumps(event.to_dict())


# ---- M12's recommended action (the user's condition for the BE-07 pin change) ------------------


def test_invalid_line_has_its_own_recommended_action() -> None:
    assert INVALID_LINE == "invalid_line"
    assert RECOMMENDED[INVALID_LINE] == "correct the order lines with the shopper, or cancel"
    assert RECOMMENDED[INVALID_LINE] not in {v for k, v in RECOMMENDED.items() if k != INVALID_LINE}
