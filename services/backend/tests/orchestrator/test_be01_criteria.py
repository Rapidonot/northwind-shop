"""BE-01 criteria not covered by the unit and scripted files (row #24): two orders at once, the
concern cap, valid empty results, a foreign tracking number, one envelope per run, the answer
without model text, and an audit sink that fails mid-run. Scripted model only; no cassette."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.agents.returns_track import TRACK_TOOL_NAME
from northwind_backend.audit import (
    AuditContext,
    AuditSink,
    InMemoryAuditSink,
    verify_chain,
)
from northwind_backend.carriers import tracking_number_for
from northwind_backend.errors import AgentResult, RunStatus, StepResult
from northwind_backend.orchestrator.__main__ import fixture_ports
from northwind_backend.orchestrator.plan import MAX_CONCERNS, TOPICS, Concern, Kind, check_plan
from northwind_backend.orchestrator.run import Runner, run_concerns
from northwind_llm_replay.scripted import ScriptedLLM

from ._driver import (
    DATA,
    RETURNS,
    World,
    fixed_clock,
    full_plan,
    load_jsonl,
    model_text,
    run,
    scanned_clean_order,
    script_orders,
    script_planner,
    script_returns,
)

pytestmark = pytest.mark.req("BE-01")

SECOND = "O000002"


def ok_result(cid: str, step: str = "payment_status", value: Any = None) -> AgentResult:
    return AgentResult(RunStatus.COMPLETE, (StepResult(step, True, False, value),), (), cid)


class FlakySink:
    """An audit sink that refuses the drafts `fails` picks, with text that must go nowhere."""

    def __init__(self, fails: Any) -> None:
        self.inner = InMemoryAuditSink()
        self.fails = fails

    def append(self, draft: Mapping[str, Any]) -> Any:
        if self.fails(draft):
            raise RuntimeError("sink-secret-text 4111 1111 1111 1111")
        return self.inner.append(draft)

    def read_all(self) -> Any:
        return self.inner.read_all()


def audit_for(sink: AuditSink, run_id: str = "run-crit") -> AuditContext:
    return AuditContext(sink, correlation_id=run_id, actor="orchestrator", clock=fixed_clock)


# --- Two orders, and the cap -------------------------------------------------------------------


def test_two_orders_in_one_request_run_in_parallel_and_answer_as_one(tmp_path: Path) -> None:
    world = World.make()
    first = world.order_id
    started: set[str] = set()
    both = asyncio.Event()

    async def barrier(concern: Concern, cid: str, audit: AuditContext) -> AgentResult:
        started.add(cid)
        if len(started) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=2.0)  # run in turn, this would time out
        value = {"intent_id": "pi_000001", "order_id": concern.order_id, "status": "captured"}
        return ok_result(cid, value=value)

    with ScriptedLLM() as llm:
        script_planner(
            llm,
            [
                {"kind": "payment_fraud", "order_id": first},
                {"kind": "payment_fraud", "order_id": SECOND},
            ],
        )
        result = run(
            world,
            f"Was {first} charged, and {SECOND} too?",
            llm,
            tmp_path,
            run_id="run-two",
            runners={Kind.PAYMENT_FRAUD: barrier},
        )
    assert result.status is RunStatus.COMPLETE
    assert [s.concern.order_id for s in result.sections] == [first, SECOND]
    assert started == {"run-two.t1", "run-two.t2"}
    assert result.answer.startswith("Status: complete (2 of 2 concerns done).")
    assert f"for order {first}" in result.answer and f"for order {SECOND}" in result.answer


def test_exactly_the_maximum_of_concerns_is_accepted_and_all_of_them_run() -> None:
    plan = {
        "concerns": [
            {"kind": "payment_fraud", "order_id": "O000001"},
            {"kind": "tracking", "order_id": "O000001"},
            {"kind": "payment_fraud", "order_id": SECOND},
            {"kind": "tracking", "order_id": SECOND},
        ]
    }
    request = f"O000001 and {SECOND}: charged and where?"
    assert len(plan["concerns"]) == MAX_CONCERNS
    concerns = check_plan(plan, request)
    assert isinstance(concerns, tuple) and len(concerns) == MAX_CONCERNS

    async def fine(concern: Concern, cid: str, audit: AuditContext) -> AgentResult:
        return ok_result(cid)

    runners: dict[Kind, Runner] = {Kind.PAYMENT_FRAUD: fine, Kind.TRACKING: fine}
    sections = asyncio.run(
        run_concerns(
            concerns,
            run_id="run-max",
            audit=audit_for(InMemoryAuditSink(), "run-max"),
            runners=runners,
        )
    )
    assert [s.correlation_id for s in sections] == [f"run-max.t{n}" for n in range(1, 5)]
    assert all(s.result.status is RunStatus.COMPLETE for s in sections)


def test_one_more_than_the_maximum_is_refused_with_a_reason_string() -> None:
    extra = [{"kind": "tracking", "order_id": f"O00000{n}"} for n in range(1, MAX_CONCERNS + 2)]
    request = " ".join(c["order_id"] for c in extra)
    assert isinstance(check_plan({"concerns": extra}, request), str)


@pytest.mark.parametrize("topic", sorted(TOPICS))
def test_every_unsupported_topic_is_annotated_never_dropped_or_run(topic: str) -> None:
    concern = Concern(Kind.UNSUPPORTED, "O000001", topic=topic)
    sink = InMemoryAuditSink()
    [section] = asyncio.run(
        run_concerns((concern,), run_id="run-un", audit=audit_for(sink, "run-un"), runners={})
    )
    assert section.result.status is RunStatus.FAILED
    [gap] = section.result.failures
    assert gap.code == "not_supported" and gap.alternatives == ("escalate_to_care",)
    assert not gap.is_retryable


# --- Valid empty results are complete -----------------------------------------------------------


def test_a_parcel_with_no_scans_is_a_complete_valid_empty_result(tmp_path: Path) -> None:
    unscanned = {r["order_id"] for r in load_jsonl("tracking.jsonl") if not r["events"]}
    order = next(
        r
        for r in load_jsonl("orders.jsonl")
        if r["exception"] is None and r["order_id"] in unscanned
    )
    world = World.make(order)
    with ScriptedLLM() as llm:
        script_planner(llm, [{"kind": "tracking", "order_id": world.order_id}])
        script_returns(llm, world)
        result = run(world, f"Where is the parcel of {world.order_id}?", llm, tmp_path)
    assert result.status is RunStatus.COMPLETE
    [section] = result.sections
    assert section.result.status is RunStatus.COMPLETE and section.result.failures == ()
    assert section.result.results[0].empty is True
    assert "no scans yet" in result.answer


@pytest.mark.req("BE-02")
def test_an_order_with_no_fraud_assessment_is_complete_not_failed(tmp_path: Path) -> None:
    world = World(fixture_ports(DATA), scanned_clean_order(), "")  # nothing assessed or paid
    with ScriptedLLM() as llm:
        script_planner(llm, [{"kind": "payment_fraud", "order_id": world.order_id}])
        script_orders(llm, world, with_intent=False)
        result = run(world, f"Was {world.order_id} flagged?", llm, tmp_path)
    assert result.status is RunStatus.COMPLETE, result.answer
    [section] = result.sections
    fraud = next(r for r in section.result.results if r.step == "fraud_assessments")
    assert fraud.ok and fraud.empty
    assert "no fraud assessment yet" in result.answer


# --- Tracking is scoped to the order's own parcel ------------------------------------------------


@pytest.mark.req("BE-02")
def test_another_orders_tracking_number_is_refused_and_never_reaches_the_carrier(
    tmp_path: Path,
) -> None:
    world = World.make()
    seen: list[str] = []
    carrier: Any = world.ports.carrier
    real = carrier.track

    def spy(tracking_number: str) -> Any:
        seen.append(tracking_number)
        return real(tracking_number)

    carrier.track = spy
    foreign = tracking_number_for("O000999")
    assert foreign != world.tracking_number
    with ScriptedLLM() as llm:
        script_planner(llm, [{"kind": "tracking", "order_id": world.order_id}])
        route = llm.route(RETURNS)
        route.reply_tool_use(TRACK_TOOL_NAME, {"tracking_number": foreign}, tool_id="toolu_x")
        route.reply("Looked it up.")
        result = run(world, f"Where is the parcel of {world.order_id}?", llm, tmp_path)
    assert seen == []
    assert result.status is RunStatus.FAILED
    [section] = result.sections
    assert section.result.status is RunStatus.FAILED
    # The task's own parcel was never looked up, so its step is `not_run`; the refusal itself is
    # in the audit as `not_permitted`.
    assert {f.code for f in section.result.failures} == {"not_run"}
    assert foreign not in json.dumps(result.to_envelope())
    refusals = [
        e.outcome
        for e in world.events()
        if e.action == "carrier.track" and e.outcome and e.outcome.get("code") == "not_permitted"
    ]
    assert len(refusals) == 1 and refusals[0]["errorCategory"] == "permission"


@pytest.mark.req("BE-02")
def test_a_subagent_that_never_calls_its_tool_is_not_a_silent_success(tmp_path: Path) -> None:
    world = World.make()
    with ScriptedLLM() as llm:
        script_planner(llm, [{"kind": "tracking", "order_id": world.order_id}])
        llm.route(RETURNS).reply("All good, the parcel is fine.")
        result = run(world, f"Where is the parcel of {world.order_id}?", llm, tmp_path)
    assert result.status is not RunStatus.COMPLETE
    [section] = result.sections
    assert section.result.status is not RunStatus.COMPLETE and section.result.failures


# --- One result, no model text -----------------------------------------------------------------


def test_the_answer_never_contains_subagent_or_planner_model_text(tmp_path: Path) -> None:
    world = World.make()
    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world))
        script_orders(llm, world)
        script_returns(llm, world)
        result = run(world, world.request(), llm, tmp_path)
    assert result.status is RunStatus.COMPLETE
    summaries = [s.result.summary for s in result.sections]
    assert all(summaries)  # the model did speak ...
    for text in (*summaries, "planned"):
        assert text not in result.answer  # ... and none of it is the answer


def test_one_envelope_per_run_and_every_section_id_is_a_child_of_the_runs(tmp_path: Path) -> None:
    world = World.make()
    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world, refund=True))
        script_orders(llm, world)
        script_returns(llm, world)
        result = run(world, world.request(refund=True), llm, tmp_path, run_id="run-env")
    envelope = json.loads(json.dumps(result.to_envelope()))  # serialisable as it is
    assert envelope["correlationId"] == "run-env"
    assert set(envelope) == {"status", "sections", "failures", "correlationId", "answer"}
    assert [s["correlationId"] for s in envelope["sections"]] == [
        "run-env.t1",
        "run-env.t2",
        "run-env.t3",
    ]
    assert all(s["correlationId"].startswith("run-env.") for s in envelope["sections"])
    assert len({s["correlationId"] for s in envelope["sections"]}) == len(envelope["sections"])


def test_no_run_id_time_or_deadline_reaches_a_model(tmp_path: Path) -> None:
    world = World.make()
    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world))
        script_orders(llm, world)
        script_returns(llm, world)
        run(
            world,
            world.request(),
            llm,
            tmp_path,
            run_id="run-zzzzzzzzzzzz",
            deadline_seconds=137.25,
        )
        bodies = model_text(llm.requests)  # all but the CLI's metadata (row #27)
    assert llm.requests
    for banned in ("run-zzzzzzzzzzzz", "137.25", "2026-11-23", ".t1", ".plan"):
        assert banned not in bodies


# --- BE-08 -------------------------------------------------------------------------------------


@pytest.mark.req("BE-08")
def test_one_runs_audit_chain_verifies_and_every_child_names_the_run_as_parent(
    tmp_path: Path,
) -> None:
    world = World.make()
    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world))
        script_orders(llm, world)
        script_returns(llm, world)
        run(world, world.request(), llm, tmp_path, run_id="run-chain")
    events = world.events()
    assert verify_chain(events) == []
    ids = [(e.action_id, e.phase) for e in events]
    assert len(ids) == len(set(ids))
    for event in events:
        if event.correlation_id == "run-chain":
            assert event.parent_id is None
        else:
            assert event.parent_id == "run-chain"
    subagents = {e.correlation_id for e in events if e.actor.endswith("-subagent")}
    assert subagents == {"run-chain.t1", "run-chain.t2"}


@pytest.mark.req("BE-08")
def test_a_refused_request_is_audited_without_its_text(tmp_path: Path) -> None:
    world = World.make()
    request = "zebra-marmalade " * 200  # over the length limit
    with ScriptedLLM() as llm:
        result = run(world, request, llm, tmp_path, run_id="run-long")
        assert llm.requests == []
    assert result.status is RunStatus.FAILED
    assert [f.code for f in result.failures] == ["invalid_request"]
    assert "zebra-marmalade" not in json.dumps([e.to_dict() for e in world.events()])
    assert "zebra-marmalade" not in json.dumps(result.to_envelope())


# --- An audit sink that fails mid-run ------------------------------------------------------------


@pytest.mark.req("BE-02")
def test_a_sink_that_is_down_for_a_task_gives_an_annotated_failure_and_the_task_is_not_run() -> (
    None
):
    ran: list[str] = []

    async def runner(concern: Concern, cid: str, audit: AuditContext) -> AgentResult:
        ran.append(cid)
        return ok_result(cid)

    sink = FlakySink(lambda d: d["action"] == "orchestrator.task")
    sections = asyncio.run(
        run_concerns(
            (Concern(Kind.PAYMENT_FRAUD, "O000001"), Concern(Kind.TRACKING, "O000001")),
            run_id="run-down",
            audit=audit_for(sink, "run-down"),
            runners={Kind.PAYMENT_FRAUD: runner, Kind.TRACKING: runner},
        )
    )
    assert ran == []  # an unaudited action does not happen
    for section in sections:
        assert section.result.status is RunStatus.FAILED
        [gap] = section.result.failures
        assert gap.code == "audit_unavailable" and gap.is_retryable
    assert "sink-secret-text" not in json.dumps([s.to_envelope() for s in sections])


@pytest.mark.req("BE-02")
def test_a_task_outcome_the_audit_lost_makes_that_section_partial_with_its_values_kept() -> None:
    async def runner(concern: Concern, cid: str, audit: AuditContext) -> AgentResult:
        return ok_result(cid, value={"status": "captured"})

    sink = FlakySink(lambda d: d["action"] == "orchestrator.task" and d["phase"] == "outcome")
    [section] = asyncio.run(
        run_concerns(
            (Concern(Kind.PAYMENT_FRAUD, "O000001"),),
            run_id="run-lost",
            audit=audit_for(sink, "run-lost"),
            runners={Kind.PAYMENT_FRAUD: runner},
        )
    )
    assert section.result.status is RunStatus.PARTIAL
    assert section.result.results[0].value == {"status": "captured"}
    assert [f.code for f in section.result.failures] == ["audit_incomplete_run"]


@pytest.mark.req("BE-02")
def test_a_run_outcome_the_audit_lost_is_partial_and_annotated_not_a_crash(
    tmp_path: Path,
) -> None:
    world = World.make()
    world.sink = FlakySink(  # type: ignore[assignment]
        lambda d: d["action"] == "orchestrator.run" and d["phase"] == "outcome"
    )

    async def fine(concern: Concern, cid: str, audit: AuditContext) -> AgentResult:
        return ok_result(cid)

    with ScriptedLLM() as llm:
        script_planner(llm, [{"kind": "payment_fraud", "order_id": world.order_id}])
        result = run(
            world,
            f"Was {world.order_id} charged?",
            llm,
            tmp_path,
            runners={Kind.PAYMENT_FRAUD: fine},
        )
    assert result.status is RunStatus.PARTIAL
    assert [f.code for f in result.failures] == ["audit_incomplete_run"]
    assert "sink-secret-text" not in json.dumps(result.to_envelope())


@pytest.mark.req("BE-02")
def test_a_sink_that_is_down_from_the_start_runs_nothing_and_says_so(tmp_path: Path) -> None:
    world = World.make()
    world.sink = FlakySink(lambda d: True)  # type: ignore[assignment]
    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world))
        result = run(world, world.request(), llm, tmp_path)
        assert llm.requests == []  # no model call, no subagent run
    assert result.status is RunStatus.FAILED
    assert [f.code for f in result.failures] == ["audit_unavailable"]
    assert result.sections == ()
