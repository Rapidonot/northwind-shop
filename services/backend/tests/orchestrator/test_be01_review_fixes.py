"""Row #24's security-review fixes, each pinned.

The review: docs/reviews/2026-11-23-BE-01-security-review.raw.md.

- M1: an intent is shown only for its own order, in the tool and again in the answer.
- M2: the planner's run failures travel with the result even when a plan exists.
- M3: every order and intent ID in the request must be in some concern.
- L1: a deadline that cancels a subagent mid-run still closes its `agent.run` in the audit.
- L2: the length limit holds after NFKC too.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Mapping
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.agents.orders_check import FRAUD_TOOL_NAME, PAYMENT_TOOL_NAME
from northwind_backend.audit import AuditEvent, InMemoryAuditSink
from northwind_backend.errors import TIMEOUT, AgentResult, FailureType, RunStatus, StepResult
from northwind_backend.fakes._base import FaultKind
from northwind_backend.orchestrator import orchestrate
from northwind_backend.orchestrator.aggregate import Section, aggregate
from northwind_backend.orchestrator.plan import (
    Concern,
    Kind,
    canonical,
    check_plan,
    ids_in,
    prompt_for,
    request_problem,
)
from northwind_backend.recovery import RetryPolicy
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM, system_text

from ._driver import (
    ORDERS,
    World,
    capture_payment,
    fixed_clock,
    full_plan,
    load_jsonl,
    run,
    script_orders,
    script_planner,
    script_returns,
)

pytestmark = pytest.mark.req("BE-01")


def other_clean_order(world: World) -> dict[str, Any]:
    return next(
        row
        for row in load_jsonl("orders.jsonl")
        if row["exception"] is None and row["order_id"] != world.order_id
    )


# --- M1 ----------------------------------------------------------------------------------------


@pytest.mark.req("BE-02")
def test_an_intent_of_another_order_is_refused_and_its_values_reach_neither_model_nor_answer(
    tmp_path: Path,
) -> None:
    world = World.make()
    foreign = capture_payment(world.ports, other_clean_order(world))
    request = f"Order {world.order_id}, payment {foreign}: was it charged and was it flagged?"
    with ScriptedLLM() as llm:
        script_planner(
            llm, [{"kind": "payment_fraud", "order_id": world.order_id, "intent_id": foreign}]
        )
        route = llm.route(ORDERS)
        route.reply_tool_use(FRAUD_TOOL_NAME, {"order_id": world.order_id}, tool_id="toolu_f")
        route.reply_tool_use(PAYMENT_TOOL_NAME, {"intent_id": foreign}, tool_id="toolu_p")
        route.reply("Looked both up.")
        result = run(world, request, llm, tmp_path)
        seen_by_model = json.dumps(
            [r["body"] for r in llm.requests if ORDERS in system_text(r["body"])]
        )
    [section] = result.sections
    assert result.status is RunStatus.PARTIAL and section.result.status is RunStatus.PARTIAL
    [gap] = section.result.failures
    assert gap.code == "intent_not_for_order" and gap.failure_type is FailureType.PERMISSION
    assert [r.step for r in section.result.results] == ["fraud_assessments"]
    assert "captured_minor" not in seen_by_model and "is captured" not in result.answer
    assert "payment_status not done (intent_not_for_order)" not in result.answer
    assert "payment.status not done (intent_not_for_order)" in result.answer


def test_a_payment_under_another_order_is_taken_out_of_the_one_result() -> None:
    """Defence in depth behind the tool's own check (re-review N6): the values leave the envelope
    and the section can't stay complete."""
    payment = {
        "intent_id": "pi_000042",
        "order_id": "O000999",
        "status": "captured",
        "currency": "USD",
        "authorized_minor": 5000,
        "captured_minor": 5000,
        "refunded_minor": 0,
    }
    fraud = [{"decision": "approve", "score": 3, "reasons": []}]
    result = AgentResult(
        RunStatus.COMPLETE,
        (
            StepResult("fraud_assessments", True, False, fraud),
            StepResult("payment_status", True, False, payment),
        ),
        (),
        "r.t1",
    )
    concern = Concern(Kind.PAYMENT_FRAUD, "O000123", "pi_000042")
    one = aggregate((Section(concern, "r.t1", result),), (), "r")
    [section] = one.sections
    assert one.status is RunStatus.PARTIAL and section.result.status is RunStatus.PARTIAL
    assert [r.step for r in section.result.results] == ["fraud_assessments"]
    assert [f.code for f in section.result.failures] == ["intent_not_for_order"]
    envelope = json.dumps(one.to_envelope())
    assert "O000999" not in envelope and "captured_minor" not in envelope
    assert "is captured" not in one.answer and "50.00" not in one.answer


# --- M2 ----------------------------------------------------------------------------------------


class FailingSink(InMemoryAuditSink):
    """Refuses the writes `refuse` picks; keeps every other one."""

    def __init__(self, refuse: Callable[[Mapping[str, Any]], bool]) -> None:
        super().__init__()
        self._refuse = refuse

    def append(self, draft: Mapping[str, Any]) -> AuditEvent:
        if self._refuse(draft):
            raise OSError("sink down")
        return super().append(draft)


@pytest.mark.req("BE-02")
@pytest.mark.req("BE-08")
def test_a_planner_audit_gap_makes_the_run_partial_even_with_a_good_plan(tmp_path: Path) -> None:
    world = World.make()
    world.sink = FailingSink(
        lambda d: d["actor"] == "orchestrator-planner" and d["action"] == "agent.tool_use"
    )
    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world))
        script_orders(llm, world)
        script_returns(llm, world)
        result = run(world, world.request(), llm, tmp_path, run_id="run-gap")
    assert [s.result.status for s in result.sections] == [RunStatus.COMPLETE] * 2
    assert result.status is RunStatus.PARTIAL
    assert "audit_incomplete_run" in [f.code for f in result.failures]
    [final] = [
        e
        for e in world.events()
        if e.correlation_id == "run-gap" and e.action == "orchestrator.run" and e.phase == "outcome"
    ]
    assert final.outcome is not None and "audit_incomplete_run" in final.outcome["failures"]
    assert "Orchestrator: audit_incomplete_run" in result.answer


# --- M3 ----------------------------------------------------------------------------------------

REQUEST = "Was O000123 charged? Where is O000124's parcel? Refund O000124. Payment pi_000042."


def test_every_id_in_the_request_is_found() -> None:
    assert ids_in(REQUEST) == {"O000123", "O000124", "pi_000042"}
    assert ids_in("XO000123 O0001234 pi_0000421 O000123") == {"O000123"}


def test_a_plan_that_drops_an_id_the_request_names_is_refused() -> None:
    dropped = {"concerns": [{"kind": "payment_fraud", "order_id": "O000123"}]}
    assert check_plan(dropped, REQUEST) == "uncovered_id"


def test_an_unsupported_concern_can_cover_what_no_other_kind_fits() -> None:
    plan = {
        "concerns": [
            {"kind": "payment_fraud", "order_id": "O000123"},
            {"kind": "tracking", "order_id": "O000124"},
            {"kind": "unsupported", "topic": "refund_request", "order_id": "O000124"},
            {"kind": "unsupported", "topic": "other", "intent_id": "pi_000042"},
        ]
    }
    concerns = check_plan(plan, REQUEST)
    assert isinstance(concerns, tuple) and len(concerns) == 4
    assert Concern(Kind.UNSUPPORTED, None, "pi_000042", "other") in concerns


# --- L1 ----------------------------------------------------------------------------------------


class _JumpingLoop(asyncio.SelectorEventLoop):
    """Row 18a (the user's change 1): the run's deadline was 1.5 s of wall time, so under xdist
    the orders subagent could miss it too, and the test failed on GitHub at #31's ship. Here the
    deadline is far off, and the test moves this loop's clock past it once the orders section has
    closed and the carrier read is hung: the deadline cuts only the hung run, whatever the load."""

    ahead = 0.0

    def time(self) -> float:
        return super().time() + self.ahead


CUT_DEADLINE = 600.0  # never reached in real time: the jump decides


def _orders_closed(world: Any) -> bool:
    return any(
        e.action == "agent.run" and e.phase == "outcome" and e.correlation_id == "run-cut.t1"
        for e in world.events()
    )


@pytest.mark.req("BE-08")
def test_a_deadline_that_cancels_a_subagent_still_closes_its_run_in_the_audit(
    tmp_path: Path,
) -> None:
    world = World.make()
    carrier: Any = world.ports.carrier
    carrier.faults.inject("track", FaultKind.HANG)
    # The hung read waits for the test, not for a 2 s bound that load could outlast: it is let go
    # once the run is over, so the loop's shutdown does not wait on its thread.
    release = threading.Event()

    def hang() -> Any:
        carrier.faults.waiting.set()
        if not release.wait(60.0):
            carrier.faults.bound_expired = True
        return TIMEOUT

    carrier.faults.hang = hang
    made = count(1)

    async def cut_when_ready() -> None:
        loop = asyncio.get_running_loop()
        assert isinstance(loop, _JumpingLoop)
        while not (carrier.faults.waiting.is_set() and _orders_closed(world)):
            await asyncio.sleep(0.02)
        loop.ahead += CUT_DEADLINE + 1

    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world))
        script_orders(llm, world)
        script_returns(llm, world)

        async def run() -> Any:
            watcher = asyncio.create_task(cut_when_ready())
            try:
                return await orchestrate(
                    world.request(),
                    ports=world.ports,
                    sink=world.sink,
                    runtime=lambda: agent_runtime(
                        base_url=llm.url, root=tmp_path / f"rt{next(made)}"
                    ),
                    # The read's own deadline is longer than the jump, so the orchestrator
                    # cancels the run while the tool call is still waiting.
                    policy=RetryPolicy(
                        max_attempts=1, deadline_seconds=10 * CUT_DEADLINE, backoff_seconds=()
                    ),
                    deadline_seconds=CUT_DEADLINE,
                    run_id="run-cut",
                    clock=fixed_clock,
                )
            finally:
                watcher.cancel()
                release.set()

        with asyncio.Runner(loop_factory=_JumpingLoop) as runner:
            result = runner.run(run())
    orders, parcel = result.sections
    assert orders.result.status is RunStatus.COMPLETE
    assert [f.code for f in parcel.result.failures] == ["deadline"]
    runs = [
        e for e in world.events() if e.correlation_id == "run-cut.t2" and e.action == "agent.run"
    ]
    assert [e.phase for e in runs] == ["attempt", "outcome"]
    assert runs[1].outcome is not None and runs[1].outcome["failures"] == ["cancelled"]
    assert not carrier.faults.bound_expired  # let go by the test, after the cut


# --- L2 ----------------------------------------------------------------------------------------


def test_the_length_limit_holds_after_normalisation() -> None:
    expands = "ﷺ" * 200  # 200 characters, about 3,600 after NFKC
    assert len(expands) < 2_000
    assert request_problem(f"Where is O000123? {expands}") == "too_long"


# --- N1 and N7 (scoped re-review) ----------------------------------------------------------------


@pytest.mark.parametrize(
    "written",
    [
        "\uff2f\uff10\uff10\uff10\uff11\uff12\uff14",  # fullwidth O000124
        "O\uff10\uff10\uff10\uff11\uff12\uff14",  # fullwidth digits only
        "O000\u200d124",  # a zero-width joiner inside
        "O000\u200b124",  # a zero-width space inside
    ],
)
def test_an_id_written_in_other_characters_counts_as_the_id_the_model_sees(written: str) -> None:
    request = f"Was O000123 charged? Where is the parcel of {written}?"
    assert ids_in(canonical(request)) == {"O000123", "O000124"}
    dropped = {"concerns": [{"kind": "payment_fraud", "order_id": "O000123"}]}
    assert check_plan(dropped, request) == "uncovered_id"
    both = {
        "concerns": [
            {"kind": "payment_fraud", "order_id": "O000123"},
            {"kind": "tracking", "order_id": "O000124"},
        ]
    }
    assert isinstance(check_plan(both, request), tuple)
    assert "O000124" in prompt_for(request)


def test_a_fullwidth_low_line_makes_an_intent_id_too() -> None:
    request = "Order O000123, payment pi\uff3f000042: charged?"
    assert ids_in(canonical(request)) == {"O000123", "pi_000042"}


def test_the_length_limit_holds_on_what_the_model_reads_after_escaping() -> None:
    ampersands = "&" * 600  # 600 characters, 3,000 once escaped
    assert request_problem(f"Where is O000123? {ampersands}") == "too_long"
    assert request_problem("Where is O000123? Tom & Jerry ordered it.") is None


# --- N4 (scoped re-review) -----------------------------------------------------------------------


@pytest.mark.req("BE-08")
def test_a_cancel_from_outside_closes_every_open_attempt(tmp_path: Path) -> None:
    """Ctrl-C or a caller gone: the run, its tasks and its subagent runs all get an outcome."""
    world = World.make()
    carrier: Any = world.ports.carrier
    carrier.faults.inject("track", FaultKind.HANG)
    carrier.faults.bound_seconds = 2.0
    made = count(1)

    async def cancel_soon() -> None:
        task = asyncio.ensure_future(
            orchestrate(
                world.request(),
                ports=world.ports,
                sink=world.sink,
                runtime=lambda: agent_runtime(base_url=llm.url, root=tmp_path / f"rt{next(made)}"),
                policy=RetryPolicy(max_attempts=1, deadline_seconds=30.0, backoff_seconds=()),
                run_id="run-ctrlc",
                clock=fixed_clock,
            )
        )
        await asyncio.to_thread(carrier.faults.waiting.wait, 20)  # the tracking read is hung
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world))
        script_orders(llm, world)
        script_returns(llm, world)
        asyncio.run(cancel_soon())
    events = [e for e in world.events() if e.correlation_id.startswith("run-ctrlc")]
    opened = {(e.action_id, e.action) for e in events if e.phase == "attempt"}
    closed = {(e.action_id, e.action) for e in events if e.phase == "outcome"}
    # Only a port read still blocked in its worker may close late; every run and task is closed.
    assert {a for a in opened - closed if a[1] != "carrier.track"} == set()
    [run_end] = [e for e in events if e.action == "orchestrator.run" and e.phase == "outcome"]
    assert run_end.outcome is not None and run_end.outcome["failures"] == ["cancelled"]
    [parcel_run] = [
        e
        for e in events
        if e.correlation_id == "run-ctrlc.t2" and e.action == "agent.run" and e.phase == "outcome"
    ]
    assert parcel_run.outcome is not None and parcel_run.outcome["failures"] == ["cancelled"]
    assert parcel_run.outcome["steps_ok"] == 0


@pytest.mark.req("BE-08")
def test_a_cancelled_run_counts_the_steps_that_finished_before_the_cancel(tmp_path: Path) -> None:
    """Re-review R5: the fraud read finishes, the payment read hangs, then the run is cancelled."""
    world = World.make()
    payment: Any = world.ports.payment
    payment.faults.inject("status", FaultKind.HANG)
    payment.faults.bound_seconds = 2.0
    made = count(1)

    async def cancel_soon() -> None:
        task = asyncio.ensure_future(
            orchestrate(
                world.request(),
                ports=world.ports,
                sink=world.sink,
                runtime=lambda: agent_runtime(base_url=llm.url, root=tmp_path / f"rt{next(made)}"),
                policy=RetryPolicy(max_attempts=1, deadline_seconds=30.0, backoff_seconds=()),
                run_id="run-half",
                clock=fixed_clock,
            )
        )
        await asyncio.to_thread(payment.faults.waiting.wait, 20)  # the payment read is hung
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with ScriptedLLM() as llm:
        script_planner(llm, full_plan(world))
        script_orders(llm, world)
        script_returns(llm, world)
        asyncio.run(cancel_soon())
    [orders_run] = [
        e
        for e in world.events()
        if e.correlation_id == "run-half.t1" and e.action == "agent.run" and e.phase == "outcome"
    ]
    assert orders_run.outcome is not None
    assert orders_run.outcome["failures"] == ["cancelled"] and orders_run.outcome["steps_ok"] == 1
