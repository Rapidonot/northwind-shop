"""All 1,000 orders through the placement handlers, driven by a fixed rule instead of a model
(row #21). It proves the replay harness and its checks before any window, and that the handlers
alone meet BE-05's targets when the steps are followed. Not evidence for the model: that is
`test_be05_replay_recorded.py`."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from northwind_backend.agents.orders_place import (
    OrderPlacementTask,
    PlacementResult,
    build_handlers,
    finish,
)
from northwind_backend.errors import AgentResult, RunStatus

from ._placement import World, orders
from ._replay import check_replay, run_all

pytestmark = [pytest.mark.req("BE-05"), pytest.mark.req("CHK-05"), pytest.mark.integration]


def _body(payload: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(payload["content"][0]["text"])
    return body


async def follow_the_steps(
    world: World, row: dict[str, Any], ref: str, cid: str
) -> PlacementResult:
    """The system prompt's steps, as a rule."""
    task = OrderPlacementTask(row["order_id"], ref)
    h = build_handlers(task, world.ports(), world.audit(), correlation_id=cid)
    arg = {"order_id": task.order_id}

    async def escalate() -> PlacementResult:
        await h.escalate_order(arg)
        return await finish(h, AgentResult(RunStatus.COMPLETE, (), (), cid))

    checked = _body(await h.validate_order(arg))
    if checked.get("value", {}).get("issues") == ["address_undeliverable"]:
        await h.use_address_on_file(arg)
        checked = _body(await h.validate_order(arg))
    if checked["isError"] or checked["value"]["issues"]:
        return await escalate()
    scored = _body(await h.assess_fraud(arg))
    if scored["isError"] or scored["value"]["decision"] != "allow":
        return await escalate()
    paid = _body(await h.authorize_payment(arg))
    if paid["isError"] and paid.get("code") == "soft_decline":
        paid = _body(await h.authorize_payment(arg))
    if paid["isError"]:
        return await escalate()
    for step in (h.capture_payment, h.confirm_order):
        if _body(await step(arg))["isError"]:
            return await escalate()
    return await finish(h, AgentResult(RunStatus.COMPLETE, (), (), cid))


def test_the_replay_checks_pass_when_the_steps_are_followed() -> None:
    world = World.make()
    rows = list(orders())
    started = time.monotonic()
    results = asyncio.run(run_all(world, rows, follow_the_steps))
    report = check_replay(world, rows, results, time.monotonic() - started)
    assert report["placed"] == 950 and report["label_agreement"] == 1.0, report
