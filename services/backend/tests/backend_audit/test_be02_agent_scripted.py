"""BE-02 and BE-08 for the Orders subagent's read-only slice, on a scripted model through the real
CLI. No cassette, no spend (row #20; M4, N4, N5, N10, L3, L7).

Injected timeouts give annotated partial results, never a crash and never a silent empty success;
the run's status comes from the audited tool outcomes, not from the model's text."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.agents.orders_check import (
    ACTOR,
    FRAUD_TOOL_NAME,
    MAX_TURNS,
    PAYMENT_TOOL_NAME,
    OrdersCheckTask,
    run_orders_check,
)
from northwind_backend.audit import InMemoryAuditSink
from northwind_backend.errors import AgentResult, FailureType, RunStatus
from northwind_backend.fakes._base import FaultKind
from northwind_backend.reconstruct import reconstruct_order
from northwind_backend.recovery import RetryPolicy
from northwind_common.llm import AgentRuntime, agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM

from ._driver import (
    Fakes,
    context,
    first_of,
    load_jsonl,
    orders,
    run_order,
    setup_captured,
    setup_unassessed,
)

pytestmark = pytest.mark.req("BE-02")

POLICY = RetryPolicy(max_attempts=3, deadline_seconds=2.0, backoff_seconds=())


class Setup:
    def __init__(self, kind: str) -> None:
        rows = orders()
        exception = {
            "clean": None,
            "unassessed": None,
            "fraud_review": "fraud_flag",
            "soft_decline": "soft_decline",
        }[kind]
        self.row = first_of(rows, exception)
        self.fakes = Fakes.for_orders([self.row])
        self.sink = InMemoryAuditSink()
        self.intent_id: str | None = None
        if kind == "clean":
            self.intent_id = setup_captured(self.row, self.fakes, self.sink, assess=True)
        elif kind == "unassessed":
            self.intent_id = setup_unassessed(self.row, self.fakes, self.sink)
        else:
            run_order(self.row, self.fakes, self.sink)
        self.order_id: str = self.row["order_id"]

    @property
    def task(self) -> OrdersCheckTask:
        return OrdersCheckTask(order_id=self.order_id, intent_id=self.intent_id)

    def run(self, llm: ScriptedLLM, root: Path, correlation_id: str = "run-1") -> AgentResult:
        runtime = agent_runtime(base_url=llm.url, root=root)
        return asyncio.run(self.arun(runtime, correlation_id))

    async def arun(self, runtime: AgentRuntime, correlation_id: str) -> AgentResult:
        return await run_orders_check(
            self.task,
            payment=self.fakes.payment,
            fraud=self.fakes.fraud,
            audit=context(self.sink, self.order_id),
            correlation_id=correlation_id,
            runtime=runtime,
            policy=POLICY,
        )


def script_both(
    llm: ScriptedLLM, s: Setup, text: str = "Payment captured and fraud check clear."
) -> None:
    llm.reply_tool_use(FRAUD_TOOL_NAME, {"order_id": s.order_id}, tool_id="toolu_fraud")
    if s.intent_id is not None:
        llm.reply_tool_use(PAYMENT_TOOL_NAME, {"intent_id": s.intent_id}, tool_id="toolu_pay")
    llm.reply(text)


def run_events(s: Setup, correlation_id: str = "run-1") -> list[Any]:
    return [e for e in s.sink.read_all() if e.correlation_id == correlation_id]


def test_a_healthy_two_step_run_is_complete_and_fully_audited(tmp_path: Path) -> None:
    s = Setup("clean")
    with ScriptedLLM() as llm:
        script_both(llm, s)
        result = s.run(llm, tmp_path)
    assert result.status is RunStatus.COMPLETE and result.failures == ()
    assert [r.step for r in result.results] == ["fraud_assessments", "payment_status"]
    assert result.summary == "Payment captured and fraud check clear."
    events = run_events(s)
    actions = [(e.action, e.phase) for e in events]
    assert actions[0] == ("agent.run", "attempt") and actions[-1] == ("agent.run", "outcome")
    assert ("fraud.read", "outcome") in actions and ("payment.status", "outcome") in actions
    assert sum(1 for a in actions if a == ("agent.tool_use", "attempt")) == 2
    assert {e.actor for e in events} == {ACTOR}
    assert {e.parent_id for e in events} == {f"corr-{s.order_id}"}
    final = events[-1].outcome
    assert final["status"] == "complete" and final["summary"] == {
        "type": "str",
        "length": len(result.summary),
    }
    record = reconstruct_order(s.sink.read_all(), s.order_id)
    assert "run-1" in record.agent_runs


@pytest.mark.parametrize(
    ("tool", "times", "expected", "failed_step"),
    [
        ("fraud", 1, RunStatus.COMPLETE, None),
        ("fraud", None, RunStatus.PARTIAL, "fraud_assessments"),
        ("payment", 1, RunStatus.COMPLETE, None),
        ("payment", None, RunStatus.PARTIAL, "payment_status"),
    ],
)
def test_injected_timeouts_on_a_two_step_task(
    tmp_path: Path, tool: str, times: int | None, expected: RunStatus, failed_step: str | None
) -> None:
    s = Setup("clean")
    fake = s.fakes.fraud if tool == "fraud" else s.fakes.payment
    fake.faults.inject(
        "read" if tool == "fraud" else "status", FaultKind.TIMEOUT_BEFORE, times=times
    )
    with ScriptedLLM() as llm:
        script_both(llm, s, "All good.")
        result = s.run(llm, tmp_path)
    assert result.status is expected
    if failed_step is None:
        assert result.failures == ()
        action = "fraud.read" if tool == "fraud" else "payment.status"
        attempts = [e for e in run_events(s) if e.action == action and e.phase == "attempt"]
        assert len(attempts) == 2  # recovered on the second, each attempt audited
        return
    [failure] = result.failures
    assert failure.failure_type is FailureType.TIMEOUT and failure.code == "timeout"
    assert failure.attempted_action == ("fraud.read" if tool == "fraud" else "payment.status")
    assert failure.attempts == 3 and failure.is_retryable
    assert failure.alternatives == ("retry_later_same_key", "escalate_to_care")
    other = "payment_status" if failed_step == "fraud_assessments" else "fraud_assessments"
    assert set(failure.partial_results) == {other}
    assert [r.step for r in result.results] == [other]


def test_both_steps_timing_out_is_failed_not_a_crash(tmp_path: Path) -> None:
    s = Setup("clean")
    s.fakes.fraud.faults.inject("read", FaultKind.TIMEOUT_BEFORE)
    s.fakes.payment.faults.inject("status", FaultKind.TIMEOUT_BEFORE)
    with ScriptedLLM() as llm:
        script_both(llm, s, "Everything is fine.")
        result = s.run(llm, tmp_path)
    assert result.status is RunStatus.FAILED
    assert {f.code for f in result.failures} == {"timeout"}


def test_a_one_step_task_whose_only_step_fails_is_failed(tmp_path: Path) -> None:
    s = Setup("soft_decline")
    assert s.intent_id is None
    s.fakes.fraud.faults.inject("read", FaultKind.TIMEOUT_BEFORE)
    with ScriptedLLM() as llm:
        script_both(llm, s)
        result = s.run(llm, tmp_path)
    assert result.status is RunStatus.FAILED
    assert [f.failure_type for f in result.failures] == [FailureType.TIMEOUT]


def test_a_valid_empty_result_is_complete_and_annotated(tmp_path: Path) -> None:
    """M4: access failed and valid empty stay distinct (hard rule 10)."""
    s = Setup("unassessed")
    with ScriptedLLM() as llm:
        script_both(llm, s, "Captured; no fraud assessment yet.")
        result = s.run(llm, tmp_path)
    assert result.status is RunStatus.COMPLETE
    fraud = next(r for r in result.results if r.step == "fraud_assessments")
    assert (
        fraud.ok and fraud.empty and fraud.value == [] and fraud.annotation == "no assessment yet"
    )


def test_the_same_empty_order_with_fraud_down_is_partial_not_empty(tmp_path: Path) -> None:
    s = Setup("unassessed")
    s.fakes.fraud.faults.inject("read", FaultKind.OUTAGE)
    with ScriptedLLM() as llm:
        script_both(llm, s)
        result = s.run(llm, tmp_path)
    assert result.status is RunStatus.PARTIAL
    assert [r.step for r in result.results] == ["payment_status"]
    [failure] = result.failures
    assert failure.failure_type is FailureType.TRANSIENT and failure.code == "service_unavailable"


@pytest.mark.parametrize(
    ("kind", "expected"), [("clean", RunStatus.PARTIAL), ("soft_decline", RunStatus.FAILED)]
)
def test_a_skipped_required_tool_is_not_run_never_complete(
    tmp_path: Path, kind: str, expected: RunStatus
) -> None:
    s = Setup(kind)
    with ScriptedLLM() as llm:
        if s.intent_id is not None:
            llm.reply_tool_use(PAYMENT_TOOL_NAME, {"intent_id": s.intent_id})
        llm.reply("Everything checked and clear.")
        result = s.run(llm, tmp_path)
    assert result.status is expected
    assert any(
        f.failure_type is FailureType.NOT_RUN and f.code == "not_run" for f in result.failures
    )


def test_a_claim_of_success_after_a_failed_tool_is_still_partial(tmp_path: Path) -> None:
    s = Setup("clean")
    s.fakes.payment.faults.inject("status", FaultKind.OUTAGE)
    with ScriptedLLM() as llm:
        script_both(llm, s, "Payment is captured and everything is complete.")
        result = s.run(llm, tmp_path)
    assert result.status is RunStatus.PARTIAL


def test_a_call_for_another_orders_id_satisfies_no_step(tmp_path: Path) -> None:
    """N5: a step counts only for the task's own ID. Security review L2: a call for another ID is
    refused and audited, and never reaches the port."""
    s = Setup("soft_decline")
    other = next(r for r in orders() if r["order_id"] != s.order_id)["order_id"]
    asked: list[str] = []
    real = s.fakes.fraud.assessments_for

    def spy(order_id: str) -> Any:
        asked.append(order_id)
        return real(order_id)

    s.fakes.fraud.assessments_for = spy  # type: ignore[method-assign]
    with ScriptedLLM() as llm:
        llm.reply_tool_use(FRAUD_TOOL_NAME, {"order_id": other})
        llm.reply("Fraud check clear.")
        result = s.run(llm, tmp_path)
    assert result.status is RunStatus.FAILED
    assert [f.failure_type for f in result.failures] == [FailureType.NOT_RUN]
    refused = [
        e for e in run_events(s) if e.action == "fraud.read" and e.inputs.get("order_id") == other
    ]
    assert [e.phase for e in refused] == ["attempt", "outcome"]
    assert refused[1].outcome is not None and refused[1].outcome["code"] == "not_permitted"
    assert asked == []
    assert "not_permitted" in json.dumps(llm.requests[-1]["body"]["messages"])


def test_a_call_to_an_unknown_tool_is_still_audited(tmp_path: Path) -> None:
    """L3: a tool use that never reaches a handler leaves an event too."""
    s = Setup("soft_decline")
    with ScriptedLLM() as llm:
        llm.reply_tool_use("mcp__northwind_orders__refund_everything", {"order_id": s.order_id})
        llm.reply_tool_use(FRAUD_TOOL_NAME, {"order_id": s.order_id})
        llm.reply("Done.")
        result = s.run(llm, tmp_path)
    uses = [e for e in run_events(s) if e.action == "agent.tool_use" and e.phase == "attempt"]
    assert uses[0].inputs["tool"] == {
        "type": "str",
        "length": len("mcp__northwind_orders__refund_everything"),
    }
    # An unknown tool has no schema, so its arguments are only counted (security review M1).
    assert "order_id" not in uses[0].inputs and uses[0].inputs["extra_args"] == 1
    assert uses[1].inputs["tool"] == FRAUD_TOOL_NAME
    assert result.status is RunStatus.COMPLETE
    assert s.fakes.payment.effects == ()


def test_an_exception_in_a_port_becomes_a_structured_failure(tmp_path: Path) -> None:
    s = Setup("clean")

    def boom(intent_id: str) -> Any:
        raise RuntimeError(f"password=hunter2 {intent_id}")

    s.fakes.payment.status = boom  # type: ignore[method-assign]
    with ScriptedLLM() as llm:
        script_both(llm, s)
        result = s.run(llm, tmp_path)
    assert result.status is RunStatus.PARTIAL
    [failure] = result.failures
    assert failure.failure_type is FailureType.UNEXPECTED and failure.attempts == 3
    text = json.dumps([e.to_dict() for e in s.sink.read_all()])
    assert "hunter2" not in text
    tool_result = json.dumps(llm.requests[-1]["body"]["messages"])
    assert "hunter2" not in tool_result


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("bad request", "model_unavailable"),
        ("cassette miss in suite 'backend-audit'", "replay_miss"),
    ],
)
def test_an_upstream_failure_is_failed_with_an_annotation(
    tmp_path: Path, message: str, code: str
) -> None:
    s = Setup("clean")
    error = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    with ScriptedLLM() as llm:
        for _ in range(4):
            llm.reply_raw(400, json.dumps(error))
        result = s.run(llm, tmp_path)
    assert result.status is RunStatus.FAILED
    assert any(f.attempted_action == "agent.run" and f.code == code for f in result.failures)
    assert run_events(s)[-1].action == "agent.run" and run_events(s)[-1].phase == "outcome"


def test_a_max_turns_stop_is_failed_with_an_annotation(tmp_path: Path) -> None:
    s = Setup("soft_decline")
    other = next(r for r in orders() if r["order_id"] != s.order_id)["order_id"]
    with ScriptedLLM() as llm:
        for n in range(MAX_TURNS + 2):
            llm.reply_tool_use(FRAUD_TOOL_NAME, {"order_id": other}, tool_id=f"toolu_{n}")
        result = s.run(llm, tmp_path)
    assert len(llm.requests) == MAX_TURNS
    assert result.status is RunStatus.FAILED
    assert any(f.code == "max_turns" for f in result.failures)
    assert run_events(s)[-1].outcome["status"] == "failed"


def test_the_model_sees_no_personal_data_token_or_correlation_id(tmp_path: Path) -> None:
    s = Setup("clean")
    shopper = next(
        x for x in load_jsonl("shoppers.jsonl") if x["shopper_id"] == s.row["shopper_id"]
    )
    with ScriptedLLM() as llm:
        script_both(llm, s)
        s.run(llm, tmp_path)
    sent = json.dumps([r["body"] for r in llm.requests])
    for secret in (s.row["payment_token"], s.row["shipping_postal_code"], "corr-", "run-1"):
        assert secret not in sent
    for value in _strings(shopper):
        if value != shopper["shopper_id"] and len(value) > 3:
            assert value not in sent, value
    assert s.order_id in sent and str(s.intent_id) in sent


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def test_the_agent_is_offered_only_its_two_read_only_tools(tmp_path: Path) -> None:
    s = Setup("clean")
    with ScriptedLLM() as llm:
        script_both(llm, s)
        s.run(llm, tmp_path)
    offered = {t["name"] for t in llm.requests[0]["body"].get("tools", [])}
    assert offered == {FRAUD_TOOL_NAME, PAYMENT_TOOL_NAME}


def test_two_concurrent_runs_keep_their_own_ids(tmp_path: Path) -> None:
    """N10: each run has its own scripted model, so replies can't cross."""
    first, second = Setup("clean"), Setup("fraud_review")
    assert first.order_id != second.order_id
    with ScriptedLLM() as llm_a, ScriptedLLM() as llm_b:
        script_both(llm_a, first, "A")
        script_both(llm_b, second, "B")
        runtime_a = agent_runtime(base_url=llm_a.url, root=tmp_path / "a")
        runtime_b = agent_runtime(base_url=llm_b.url, root=tmp_path / "b")

        async def both() -> tuple[AgentResult, AgentResult]:
            return await asyncio.gather(
                first.arun(runtime_a, "run-a"), second.arun(runtime_b, "run-b")
            )

        result_a, result_b = asyncio.run(both())
    assert (result_a.summary, result_b.summary) == ("A", "B")
    assert result_a.correlation_id == "run-a" and result_b.correlation_id == "run-b"
    assert {e.correlation_id for e in first.sink.read_all() if e.actor == ACTOR} == {"run-a"}
    assert {e.correlation_id for e in second.sink.read_all() if e.actor == ACTOR} == {"run-b"}
