"""BE-01 end to end on a scripted model through the real CLI (row #24). No cassette, no spend.

The planner (Sonnet's role) and both subagents (Haiku's role) run as real agent runs; the scripted
model answers each by the system prompt it sees, so the subagents can run at the same time."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import Response

from northwind_backend.agents.orders_check import FRAUD_TOOL_NAME, PAYMENT_TOOL_NAME
from northwind_backend.agents.returns_track import TRACK_TOOL_NAME
from northwind_backend.errors import FailureType, RunStatus
from northwind_backend.fakes._base import FaultKind
from northwind_backend.orchestrator.plan import PLAN_TOOL_NAME
from northwind_backend.reconstruct import reconstruct_order
from northwind_llm_replay.scripted import ScriptedLLM, system_text
from northwind_llm_replay.scrub import match_key, request_hash, scrub

from ._driver import (
    ORDERS,
    PLANNER,
    RETURNS,
    World,
    full_plan,
    model_text,
    run,
    script_orders,
    script_planner,
    script_returns,
)

pytestmark = pytest.mark.req("BE-01")


def by_agent(llm: ScriptedLLM) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {PLANNER: [], ORDERS: [], RETURNS: []}
    for request in llm.requests:
        text = system_text(request["body"])
        marker = next((m for m in out if m in text), None)
        assert marker is not None, "a request no agent made"
        out[marker].append(request)
    return out


def tool_names(request: dict[str, Any]) -> set[str]:
    return {t["name"] for t in request["body"].get("tools", [])}


def test_a_three_concern_request_is_split_run_in_parallel_and_answered_as_one(
    tmp_path: Path,
) -> None:
    world = World.make()
    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world, refund=True))
        script_orders(llm, world)
        script_returns(llm, world)
        result = run(world, world.request(refund=True), llm, tmp_path, run_id="run-e2e")
    assert result.status is RunStatus.PARTIAL  # the refund is annotated, not handled
    assert [s.concern.kind.value for s in result.sections] == [
        "payment_fraud",
        "tracking",
        "unsupported",
    ]
    assert [s.result.status for s in result.sections] == [
        RunStatus.COMPLETE,
        RunStatus.COMPLETE,
        RunStatus.FAILED,
    ]
    assert result.failures == () and result.correlation_id == "run-e2e"
    payment = next(r for r in result.sections[0].result.results if r.step == "payment_status")
    assert payment.value["status"] == "captured" and payment.value["captured_minor"] == 5000
    tracking = result.sections[1].result.results[0]
    assert tracking.value["tracking_number"] == world.tracking_number and tracking.value["scans"]
    assert f"payment {world.intent_id} is captured" in result.answer
    assert f"parcel {world.tracking_number}" in result.answer
    assert "refund_request for order" in result.answer and "escalate_to_care" in result.answer


class HeldFirstReplies(ScriptedLLM):
    """Holds each subagent's first reply until both subagents' first requests have arrived.

    If both arrive, both subagents were waiting on the model at the same moment, which a sequential
    run can't do, whatever order the requests come in. If one doesn't arrive within `HOLD_SECONDS`
    (well under the orchestrator's 180 s deadline), the hold is released so the run can finish,
    and the marker is recorded in `timed_out`, which fails the test."""

    HOLD_SECONDS = 60.0

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, str]] = []
        self.timed_out: list[str] = []
        self._arrived: set[str] = set()

    async def _handle(self, request: Request) -> Response:
        body = await request.json() if await request.body() else {}
        text = system_text(body)
        marker = next((m for m in (ORDERS, RETURNS) if m in text), None)
        if marker is not None and marker not in self._arrived:
            # The server's event loop runs every handler, so this set needs no lock.
            self._arrived.add(marker)
            self.events.append(("arrived", marker))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.HOLD_SECONDS
            while self._arrived != {ORDERS, RETURNS}:
                if loop.time() > deadline:
                    self.timed_out.append(marker)
                    break
                await asyncio.sleep(0.01)
            self.events.append(("released", marker))
        return await super()._handle(request)


def test_the_two_subagents_overlap_in_time(tmp_path: Path) -> None:
    """Both subagents' first model calls are in flight at the same moment: they ran together.

    Deterministic: the scripted model holds each first reply until the other subagent's first
    request has arrived, so the test no longer depends on request arrival order or timing."""
    world = World.make()
    llm = HeldFirstReplies()
    with llm:
        script_planner(llm, full_plan(world))
        script_orders(llm, world)
        script_returns(llm, world)
        result = run(world, world.request(), llm, tmp_path)
    assert llm.timed_out == []
    assert result.status is RunStatus.COMPLETE
    arrivals = [m for kind, m in llm.events[:2] if kind == "arrived"]
    assert sorted(arrivals) == sorted([ORDERS, RETURNS])  # both arrived before either released
    assert [kind for kind, _ in llm.events] == ["arrived", "arrived", "released", "released"]


def test_each_agent_gets_only_its_own_tools(tmp_path: Path) -> None:
    """Hard rule 8: the planner has no data tool; each subagent only its scoped reads."""
    world = World.make()
    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world))
        script_orders(llm, world)
        script_returns(llm, world)
        run(world, world.request(), llm, tmp_path)
        agents = by_agent(llm)
    assert {frozenset(tool_names(r)) for r in agents[PLANNER]} == {frozenset({PLAN_TOOL_NAME})}
    assert {frozenset(tool_names(r)) for r in agents[ORDERS]} == {
        frozenset({FRAUD_TOOL_NAME, PAYMENT_TOOL_NAME})
    }
    assert {frozenset(tool_names(r)) for r in agents[RETURNS]} == {frozenset({TRACK_TOOL_NAME})}


def test_the_same_request_twice_makes_the_same_model_input(tmp_path: Path) -> None:
    """The user's condition 2: no correlation ID, time or deadline reaches a model, so each
    agent's request hashes are identical across runs with different run IDs."""

    def hashes(run_id: str) -> tuple[dict[str, list[str]], str]:
        world = World.make()
        with ScriptedLLM() as llm:
            script_planner(llm, full_plan(world, refund=True))
            script_orders(llm, world)
            script_returns(llm, world)
            run(world, world.request(refund=True), llm, tmp_path / run_id, run_id=run_id)
            agents = by_agent(llm)
            bodies = model_text(llm.requests)  # all but the CLI's metadata (row #27)
        out = {
            marker: [
                request_hash(match_key("POST", r["path"], r["headers"], scrub(r["body"])))
                for r in requests
            ]
            for marker, requests in agents.items()
        }
        return out, bodies

    first, first_bodies = hashes("run-aaaaaaaaaaaa")
    second, second_bodies = hashes("run-bbbbbbbbbbbb")
    assert first == second
    assert all(first[m] for m in (PLANNER, ORDERS, RETURNS))
    for run_id, bodies in (("run-aaaaaaaaaaaa", first_bodies), ("run-bbbbbbbbbbbb", second_bodies)):
        assert run_id not in bodies and "2026-11-23T00:00:00" not in bodies


@pytest.mark.req("BE-02")
def test_an_injected_carrier_timeout_gives_a_partial_result_with_the_rest_intact(
    tmp_path: Path,
) -> None:
    world = World.make()
    world.ports.carrier.faults.inject("track", FaultKind.TIMEOUT_BEFORE)  # type: ignore[attr-defined]
    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world))
        script_orders(llm, world)
        script_returns(llm, world)
        result = run(world, world.request(), llm, tmp_path)
    assert result.status is RunStatus.PARTIAL
    orders, parcel = result.sections
    assert orders.result.status is RunStatus.COMPLETE and len(orders.result.results) == 2
    assert parcel.result.status is RunStatus.FAILED
    [gap] = parcel.result.failures
    assert gap.failure_type is FailureType.TIMEOUT and gap.attempted_action == "carrier.track"
    assert gap.attempted_inputs == {"tracking_number": world.tracking_number}
    assert gap.attempts == 3 and gap.alternatives == ("retry_later_same_key", "escalate_to_care")
    assert "carrier.track not done (timeout)" in result.answer


@pytest.mark.req("BE-02")
def test_a_hanging_carrier_is_cut_by_the_reads_deadline_not_left_running(tmp_path: Path) -> None:
    world = World.make()
    world.ports.carrier.faults.inject("track", FaultKind.HANG)  # type: ignore[attr-defined]
    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world))
        script_orders(llm, world)
        script_returns(llm, world)
        result = run(world, world.request(), llm, tmp_path)
    assert result.status is RunStatus.PARTIAL
    assert result.sections[0].result.status is RunStatus.COMPLETE
    assert {f.code for f in result.sections[1].result.failures} == {"timeout"}


@pytest.mark.req("BE-02")
def test_a_planner_that_cannot_reach_the_model_fails_annotated_and_runs_nothing(
    tmp_path: Path,
) -> None:
    world = World.make()
    with ScriptedLLM() as llm:  # nothing scripted: every model call is refused
        result = run(world, world.request(), llm, tmp_path)
        asked = by_agent(llm)
    assert result.status is RunStatus.FAILED and result.sections == ()
    assert result.failures and all(f.alternatives for f in result.failures)
    assert asked[ORDERS] == [] and asked[RETURNS] == []
    assert result.answer.startswith("Status: failed (0 of 0 concerns done).")


def test_a_plan_with_an_invented_id_is_refused_and_a_corrected_one_runs(tmp_path: Path) -> None:
    world = World.make()
    with ScriptedLLM() as llm:
        planner = llm.route(PLANNER)
        planner.reply_tool_use(
            PLAN_TOOL_NAME,
            {"concerns": [{"kind": "tracking", "order_id": "O999999"}]},
            tool_id="toolu_bad",
        )
        planner.reply_tool_use(PLAN_TOOL_NAME, {"concerns": full_plan(world)}, tool_id="toolu_good")
        planner.reply("planned")
        script_orders(llm, world)
        script_returns(llm, world)
        result = run(world, world.request(), llm, tmp_path)
        second_turn = by_agent(llm)[PLANNER][1]["body"]
    assert result.status is RunStatus.COMPLETE
    assert [s.concern.kind.value for s in result.sections] == ["payment_fraud", "tracking"]
    refusal = json.dumps(second_turn["messages"][-1])
    assert "invalid_plan" in refusal and "Use only IDs from the request" in refusal
    assert "O999999" not in refusal.split("invalid_plan", 1)[1]  # nothing echoed back


@pytest.mark.parametrize(
    ("concern", "code"),
    [
        # Schema-valid, but the ID is not in the request: refused by the plan's check.
        ({"kind": "tracking", "order_id": "O999999"}, "invalid_plan"),
        # Not even schema-valid: the SDK refuses it before the handler, so the step never ran.
        ({"kind": "refund_now", "order_id": "O000001"}, "not_run"),
    ],
)
def test_a_planner_that_only_submits_bad_plans_runs_nothing(
    tmp_path: Path, concern: dict[str, str], code: str
) -> None:
    world = World.make()
    with ScriptedLLM() as llm:
        planner = llm.route(PLANNER)
        planner.reply_tool_use(PLAN_TOOL_NAME, {"concerns": [concern]})
        planner.reply("planned")
        result = run(world, world.request(), llm, tmp_path)
        asked = by_agent(llm)
    assert result.status is RunStatus.FAILED and result.sections == ()
    assert [f.code for f in result.failures] == [code]
    assert all(f.alternatives for f in result.failures)
    assert asked[ORDERS] == [] and asked[RETURNS] == []


def test_a_request_with_card_data_never_reaches_a_model(tmp_path: Path) -> None:
    """Hard rule 1."""
    world = World.make()
    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world))
        result = run(world, f"{world.request()} Card 4111 1111 1111 1111.", llm, tmp_path)
        assert llm.requests == []
    assert result.status is RunStatus.FAILED
    [gap] = result.failures
    assert gap.code == "invalid_request" and gap.attempted_inputs == {"reason": "card_data"}
    assert "4111" not in json.dumps(result.to_envelope())
    assert "4111" not in json.dumps([e.to_dict() for e in world.events()])


@pytest.mark.req("BE-08")
def test_one_run_is_audited_end_to_end_without_the_request_text(tmp_path: Path) -> None:
    world = World.make()
    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world, refund=True))
        script_orders(llm, world)
        script_returns(llm, world)
        run(world, world.request(refund=True), llm, tmp_path, run_id="run-audit")
    events = [e for e in world.events() if e.correlation_id.startswith("run-audit")]
    top = [e for e in events if e.correlation_id == "run-audit"]
    assert (top[0].action, top[0].phase) == ("orchestrator.run", "attempt")
    assert (top[-1].action, top[-1].phase) == ("orchestrator.run", "outcome")
    assert top[-1].outcome == {
        "ok": True,
        "status": "partial",
        "concerns": 3,
        "kinds": ["payment_fraud", "tracking", "unsupported"],
        "failures": [],
    }
    assert top[0].inputs == {"request": {"type": "str", "length": len(world.request(refund=True))}}
    tasks = [e for e in top if e.action == "orchestrator.task" and e.phase == "outcome"]
    assert sorted(e.inputs["task"] for e in tasks) == [
        "run-audit.t1",
        "run-audit.t2",
        "run-audit.t3",
    ]
    children = {e.correlation_id: e for e in events if e.correlation_id != "run-audit"}
    assert set(children) == {"run-audit.plan", "run-audit.t1", "run-audit.t2"}
    assert {e.parent_id for e in events if e.correlation_id != "run-audit"} == {"run-audit"}
    assert {e.actor for e in events} == {
        "orchestrator",
        "orchestrator-planner",
        "orders-subagent",
        "returns-subagent",
    }
    text = json.dumps([e.to_dict() for e in world.events()])
    assert "did the fraud" not in text and "refund it" not in text
    record = reconstruct_order(world.events(), world.order_id)
    assert {"run-audit.t1", "run-audit.t2"} <= set(record.agent_runs)
