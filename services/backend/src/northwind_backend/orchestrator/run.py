"""The orchestrator: split a multi-concern staff request, run the subagents in parallel, aggregate
one result (row #24, BE-01; BE-02's propagation; BE-08's orchestrator spans).

1. The request is checked (no card data, a sane length) before any model sees it.
2. The planner (Sonnet) splits it into validated concerns (`plan.py`).
3. Every supported concern runs its subagent (Haiku) at the same time, in one task group, each under
   its own deadline: `payment_fraud` runs the Orders subagent's read-only slice, `tracking` the
   Returns subagent's tracking slice. An unsupported concern runs nothing and is annotated.
4. The sections become one result (`aggregate.py`).

Nothing here moves money or changes state: both subagents are read-only, and the planner has no
data tools. Every action is audited under one run: the orchestrator's correlation ID is
`run-<hex>`, and the planner's and each subagent's runs are its children (`<run>.plan`,
`<run>.t1`, ...), so their `parent_id` is the run's ID. None of these IDs, no time and no deadline
ever reaches a model, so the same request always makes the same model input.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from northwind_backend.agents.orders_check import OrdersCheckTask, run_orders_check
from northwind_backend.agents.returns_track import ReturnsTrackTask, run_returns_track
from northwind_backend.audit import AuditContext, AuditSink, AuditWriteError
from northwind_backend.errors import AgentFailure, AgentResult, FailureType, RunStatus
from northwind_backend.orchestrator.aggregate import (
    OrchestratorResult,
    Section,
    aggregate,
    failure,
    not_supported,
)
from northwind_backend.orchestrator.plan import (
    Concern,
    Kind,
    concerns_of,
    request_problem,
    run_planner,
)
from northwind_backend.ports import CarrierPort, FraudPort, PaymentPort
from northwind_backend.recovery import RetryPolicy
from northwind_common.llm import AgentRuntime

log = logging.getLogger(__name__)

ACTOR = "orchestrator"
DEFAULT_DEADLINE_SECONDS = 180.0
DEFAULT_POLICY = RetryPolicy()

# One subagent run for one concern: (concern, its correlation ID, its parent audit context).
Runner = Callable[[Concern, str, AuditContext], Awaitable[AgentResult]]
RuntimeFactory = Callable[[], AgentRuntime | None]


@dataclass(frozen=True, slots=True)
class Ports:
    """The shared ports. Each subagent sees them only through its own read-only, audited view."""

    payment: PaymentPort
    fraud: FraudPort
    carrier: CarrierPort


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def default_runners(
    ports: Ports, runtime: RuntimeFactory, policy: RetryPolicy
) -> dict[Kind, Runner]:
    async def payment_fraud(concern: Concern, cid: str, audit: AuditContext) -> AgentResult:
        # The plan's check guarantees an order ID; the task refuses anything else.
        task = OrdersCheckTask(order_id=concern.order_id or "", intent_id=concern.intent_id)
        return await run_orders_check(
            task,
            payment=ports.payment,
            fraud=ports.fraud,
            audit=audit,
            correlation_id=cid,
            runtime=runtime(),
            policy=policy,
        )

    async def tracking(concern: Concern, cid: str, audit: AuditContext) -> AgentResult:
        return await run_returns_track(
            ReturnsTrackTask(order_id=concern.order_id or ""),
            carrier=ports.carrier,
            audit=audit,
            correlation_id=cid,
            runtime=runtime(),
            policy=policy,
        )

    return {Kind.PAYMENT_FRAUD: payment_fraud, Kind.TRACKING: tracking}


def _close_cancelled(
    audit: AuditContext,
    action_id: str,
    action: str,
    inputs: Mapping[str, object],
    labels: Mapping[str, str] | None = None,
) -> None:
    try:
        audit.outcome(
            action_id,
            action,
            inputs,
            {"ok": False, "status": RunStatus.FAILED.value, "failures": ["cancelled"]},
            labels=labels,
        )
    except AuditWriteError:
        log.error("%s outcome not recorded after a cancel", action)


def _task_inputs(concern: Concern) -> dict[str, object]:
    return {"order_id": concern.order_id, "intent_id": concern.intent_id}


async def _run_one(
    concern: Concern,
    cid: str,
    audit: AuditContext,
    runner: Runner | None,
    deadline: float,
) -> AgentResult:
    """Never raises: a deadline or a crash becomes an annotated failure for this concern only."""
    inputs = _task_inputs(concern)
    labels = {"kind": concern.kind.value, "task": cid}
    try:
        action_id = audit.attempt("orchestrator.task", inputs, labels=labels)
    except AuditWriteError:
        log.error("orchestrator.task not recorded; %s not run", cid)
        gap = failure(
            "audit_unavailable", FailureType.TRANSIENT, "orchestrator.task", inputs, retryable=True
        )
        return AgentResult(RunStatus.FAILED, (), (gap,), cid)
    if runner is None:
        result = not_supported(concern, cid)
    else:
        try:
            result = await asyncio.wait_for(runner(concern, cid, audit), timeout=deadline)
        except asyncio.CancelledError:
            # Cancelled from outside (Ctrl-C, a caller gone): close the attempt, then go on
            # cancelling (security re-review N4).
            _close_cancelled(audit, action_id, "orchestrator.task", inputs, labels)
            raise
        except TimeoutError:
            log.error("%s passed its deadline", cid)
            gap = failure("deadline", FailureType.TIMEOUT, "agent.run", inputs, retryable=True)
            result = AgentResult(RunStatus.FAILED, (), (gap,), cid)
        except Exception as error:  # noqa: BLE001 - its text must go nowhere
            log.error("%s crashed: %s", cid, type(error).__name__)
            gap = failure(
                "unexpected_error", FailureType.UNEXPECTED, "agent.run", inputs, retryable=True
            )
            result = AgentResult(RunStatus.FAILED, (), (gap,), cid)
    outcome = {
        "ok": result.status is not RunStatus.FAILED,
        "status": result.status.value,
        "failures": [f.code for f in result.failures],
    }
    try:
        audit.outcome(action_id, "orchestrator.task", inputs, outcome, labels=labels)
    except AuditWriteError:
        # The caller learns it too, as the subagent runtime's `audit_incomplete_run` does.
        log.error("orchestrator.task outcome not recorded for %s", cid)
        lost = failure(
            "audit_incomplete_run",
            FailureType.TRANSIENT,
            "orchestrator.task",
            inputs,
            retryable=True,
        )
        status = RunStatus.PARTIAL if result.status is RunStatus.COMPLETE else result.status
        result = replace(result, status=status, failures=(*result.failures, lost))
    return result


async def run_concerns(
    concerns: tuple[Concern, ...],
    *,
    run_id: str,
    audit: AuditContext,
    runners: Mapping[Kind, Runner],
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
) -> tuple[Section, ...]:
    """Every concern at once, in one task group; the sections keep the plan's order."""
    cids = [f"{run_id}.t{n}" for n in range(1, len(concerns) + 1)]
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(_run_one(c, cid, audit, runners.get(c.kind), deadline_seconds))
            for c, cid in zip(concerns, cids, strict=True)
        ]
    return tuple(
        Section(c, cid, t.result()) for c, cid, t in zip(concerns, cids, tasks, strict=True)
    )


async def _plan_and_run(
    request: str,
    *,
    run_id: str,
    audit: AuditContext,
    ports: Ports,
    runtime: RuntimeFactory,
    runners: Mapping[Kind, Runner] | None,
    deadline_seconds: float,
    policy: RetryPolicy,
) -> tuple[tuple[Section, ...], tuple[AgentFailure, ...], tuple[Concern, ...]]:
    plan = await run_planner(
        request, audit=audit, correlation_id=f"{run_id}.plan", runtime=runtime()
    )
    planned = concerns_of(plan)
    # The planner's own run failures always travel with the result (security review M2): a plan
    # that ran past its turns or lost an audit write still makes the run partial.
    failures = plan.failures
    if not planned:
        return (), failures or (_request_failure("invalid_plan"),), ()
    chosen = runners if runners is not None else default_runners(ports, runtime, policy)
    sections = await run_concerns(
        planned,
        run_id=run_id,
        audit=audit,
        runners=chosen,
        deadline_seconds=deadline_seconds,
    )
    return sections, failures, planned


def _request_failure(code: str, reason: str | None = None) -> AgentFailure:
    inputs: dict[str, object] = {} if reason is None else {"reason": reason}
    return failure(code, FailureType.VALIDATION, "orchestrator.run", inputs, retryable=False)


async def orchestrate(
    request: str,
    *,
    ports: Ports,
    sink: AuditSink,
    runtime: RuntimeFactory = lambda: None,
    runners: Mapping[Kind, Runner] | None = None,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    policy: RetryPolicy = DEFAULT_POLICY,
    run_id: str | None = None,
    clock: Callable[[], str] | None = None,
) -> OrchestratorResult:
    """`runtime` makes one agent runtime per model run (None: a fresh one each). `runners` replaces
    the default subagent per kind (tests)."""
    run_id = run_id or new_run_id()
    audit = AuditContext(sink, correlation_id=run_id, actor=ACTOR, clock=clock)
    run_inputs = {"request": request}
    try:
        action_id = audit.attempt("orchestrator.run", run_inputs)
    except AuditWriteError:
        log.error("orchestrator.run not recorded; %s not run", run_id)
        gap = failure(
            "audit_unavailable", FailureType.TRANSIENT, "orchestrator.run", {}, retryable=True
        )
        return aggregate((), (gap,), run_id)

    sections: tuple[Section, ...] = ()
    failures: tuple[AgentFailure, ...] = ()
    planned: tuple[Concern, ...] = ()
    problem = request_problem(request)
    if problem is not None:
        failures = (_request_failure("invalid_request", problem),)
    else:
        try:
            sections, failures, planned = await _plan_and_run(
                request,
                run_id=run_id,
                audit=audit,
                ports=ports,
                runtime=runtime,
                runners=runners,
                deadline_seconds=deadline_seconds,
                policy=policy,
            )
        except asyncio.CancelledError:
            _close_cancelled(audit, action_id, "orchestrator.run", run_inputs)
            raise
    result = aggregate(sections, failures, run_id)
    outcome: dict[str, Any] = {
        "ok": result.status is not RunStatus.FAILED,
        "status": result.status.value,
        "concerns": len(planned),
        "kinds": [c.kind.value for c in planned],
        "failures": [f.code for f in failures],
    }
    try:
        audit.outcome(action_id, "orchestrator.run", run_inputs, outcome)
    except AuditWriteError:
        log.error("orchestrator.run outcome not recorded for %s", run_id)
        lost = failure(
            "audit_incomplete_run", FailureType.TRANSIENT, "orchestrator.run", {}, retryable=True
        )
        result = aggregate(sections, (*failures, lost), run_id)
    return result
