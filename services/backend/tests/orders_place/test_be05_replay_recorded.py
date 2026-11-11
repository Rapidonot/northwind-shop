"""The 1,000-order backend replay on Haiku (row #21: BE-05, CHK-05; PRD §10 "Backend replay").

Recorded once in the user's `backend-replay` window (`make record SUITE=backend-replay`), replayed
after by `make test-integration` and `make ci`. `BACKEND_REPLAY_SLICE=<digits>` runs the first n
orders (window 2's slice); the default is all of them. The checks are in `_replay.py`, fixed in
the plan before recording: a mismatch after recording is stop 7, never a re-record loop.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.agents.orders_place import (
    OrderPlacementTask,
    PlacementResult,
    run_order_placement,
)
from northwind_common.llm import agent_runtime
from northwind_llm_replay.pytest_plugin import LLMProxyHandle

from ._placement import KINDS, World, first_of, orders
from ._replay import assert_money, check_cassettes, check_replay, labels, replay_slice, run_all

pytestmark = [
    pytest.mark.req("BE-05"),
    pytest.mark.req("CHK-05"),
    pytest.mark.llm_suite("backend-replay"),
    pytest.mark.integration,
]


def _placer(root: Path) -> Any:
    async def place(world: World, row: dict[str, Any], ref: str, cid: str) -> PlacementResult:
        return await run_order_placement(
            OrderPlacementTask(row["order_id"], ref),
            ports=world.ports(),
            audit=world.audit(),
            correlation_id=cid,
            runtime=agent_runtime(root=root / cid),
        )

    return place


def test_backend_replay(llm_proxy: LLMProxyHandle, tmp_path: Path) -> None:
    rows = list(orders()[: replay_slice()])
    world = World.make()
    started = time.monotonic()
    results = asyncio.run(run_all(world, rows, _placer(tmp_path)))
    check_replay(world, rows, results, time.monotonic() - started)
    check_cassettes()


def test_ten_replays_yield_one_order(llm_proxy: LLMProxyHandle, tmp_path: Path) -> None:
    """CHK-05 on the recorded model: one order of each kind, each placed ten times in a row."""
    world = World.make()
    rows = [first_of(kind) for kind in KINDS]
    expected = labels()
    results = asyncio.run(run_all(world, rows, _placer(tmp_path), runs_per_order=10))
    for row in rows:
        oid = row["order_id"]
        outcomes = {r.outcome for r in results[oid]}
        # Advisor finding 10: in a row, each kind yields its labelled outcome every time (one
        # order for the kinds that place), not merely one outcome.
        assert outcomes == {expected[oid]["expected_outcome"]}, (oid, outcomes)
        assert_money(world, row, placed=outcomes == {"placed"})
        assert len([i for i in world.payment.intents.values() if i.order_id == oid]) <= 1, oid
        escalations = [
            e for e in world.orders.effects if e.operation == "escalate" and e.reference == oid
        ]
        assert len(escalations) <= 1, oid
