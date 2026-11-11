"""Run one backend subagent, audited, and turn what it did into a structured result (BE-02, BE-08).

The run writes an `agent.run` attempt before the model is called and one outcome after. Every tool
use the model makes is written as `agent.tool_use`, by reference, whether or not it reaches a
handler (L3): `agent_options()` takes no hooks, so the stream is where an unknown or denied tool is
seen.

The result's status comes from the steps the task requires and the audited outcomes of the tool
calls for each step's own target (M4, N5), never from the model's text. A failure in the model call
itself becomes an annotated failure; nothing raises out of `run_subagent`.

Options are built only through `agent_options()` (FOUND-2): no built-in tools, in-process tools
only, `dontAsk`, a turn cap."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKError,
    ResultMessage,
    SdkMcpTool,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
)

from northwind_backend.audit import AuditContext, AuditWriteError, input_refs
from northwind_backend.errors import (
    AgentFailure,
    AgentResult,
    FailureType,
    Ok,
    Result,
    RunStatus,
    StepResult,
    ToolError,
    alternatives_for,
    derive_status,
    failure_type_of,
)
from northwind_backend.recovery import Recovered
from northwind_common.llm import AgentRuntime, agent_options

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Step:
    """A required step: which tool's call counts, for which target value."""

    name: str
    action: str
    target_field: str
    target: str


@dataclass(slots=True)
class _Call:
    target: object
    result: Result[Any]
    attempts: int
    inputs: Mapping[str, object]
    value: Any = None
    empty: bool = False


@dataclass
class RunState:
    """What the run's tool handlers saw, per step. Bound into each run's handlers (L4)."""

    steps: tuple[Step, ...]
    calls: dict[str, list[_Call]] = field(default_factory=dict)

    def record(
        self,
        step: str,
        target: object,
        done: Recovered[Any],
        inputs: Mapping[str, object],
        value: Any = None,
        empty: bool = False,
    ) -> None:
        self.calls.setdefault(step, []).append(
            _Call(target, done.result, done.attempts, inputs, value, empty)
        )

    def results(self) -> tuple[tuple[StepResult, ...], tuple[AgentFailure, ...]]:
        results: list[StepResult] = []
        pending: list[Step] = []
        for step in self.steps:
            # A call counts only for the task's own target (N5).
            own = [c for c in self.calls.get(step.name, []) if c.target == step.target]
            ok = [c for c in own if isinstance(c.result, Ok)]
            if ok:
                last = ok[-1]
                note = "no assessment yet" if last.empty else None
                results.append(StepResult(step.name, True, last.empty, last.value, note))
            else:
                pending.append(step)
        partial = {r.step: r.value for r in results}
        failures: list[AgentFailure] = []
        for step in pending:
            own = [c for c in self.calls.get(step.name, []) if c.target == step.target]
            if own and isinstance(own[-1].result, ToolError):
                error = own[-1].result
                failures.append(
                    AgentFailure(
                        failure_type=failure_type_of(error),
                        code=error.code,
                        attempted_action=step.action,
                        attempted_inputs=input_refs(own[-1].inputs),
                        partial_results=partial,
                        alternatives=alternatives_for(error.code),
                        is_retryable=error.is_retryable,
                        attempts=own[-1].attempts,
                    )
                )
            else:
                failures.append(
                    AgentFailure(
                        failure_type=FailureType.NOT_RUN,
                        code="not_run",
                        attempted_action=step.action,
                        attempted_inputs=input_refs({step.target_field: step.target}),
                        partial_results=partial,
                        alternatives=alternatives_for("not_run"),
                        is_retryable=True,
                        attempts=0,
                    )
                )
        return tuple(results), tuple(failures)


@dataclass(frozen=True, slots=True)
class SubagentSpec:
    actor: str
    system_prompt: str
    server_name: str
    model: str
    max_turns: int
    # Per allowed tool, its schema's argument names: only these are audited, by reference, and any
    # other argument is only counted (security review M1).
    tool_args: Mapping[str, frozenset[str]] = field(default_factory=dict)


_RUN_FAILURES: dict[str, tuple[FailureType, bool]] = {
    "model_unavailable": (FailureType.TRANSIENT, True),
    "replay_miss": (FailureType.VALIDATION, False),
    "max_turns": (FailureType.BUSINESS, False),
    "unexpected_error": (FailureType.UNEXPECTED, True),
    # The run has no idempotency key of its own, so this is its own code (re-review N1): rerunning
    # the whole run is safe only while its tools are reads, or once #21 builds every write key from
    # the task's IDs alone.
    "audit_incomplete_run": (FailureType.TRANSIENT, True),
}


def _run_failure(
    code: str, partial: Mapping[str, Any], inputs: Mapping[str, object]
) -> AgentFailure:
    failure_type, retryable = _RUN_FAILURES[code]
    return AgentFailure(
        failure_type=failure_type,
        code=code,
        attempted_action="agent.run",
        attempted_inputs=input_refs(inputs),
        partial_results=partial,
        alternatives=alternatives_for(code),
        is_retryable=retryable,
        attempts=1,
    )


def _result_code(result: ResultMessage) -> str | None:
    if result.subtype == "error_max_turns":
        return "max_turns"
    if not result.is_error:
        return None
    # The result text is only matched here, never passed on.
    return "replay_miss" if "cassette miss" in (result.result or "") else "model_unavailable"


def _audit_tool_use(audit: AuditContext, block: ToolUseBlock, spec: SubagentSpec) -> None:
    """Only the tool's own schema arguments are written, by reference; every other argument the
    model sent, name and all, is only counted (security review M1). An unknown tool's name is
    written as type and length."""
    args = block.input if isinstance(block.input, dict) else {}
    known = spec.tool_args.get(block.name, frozenset())
    inputs: dict[str, object] = {k: v for k, v in args.items() if k in known}
    extra = len(args) - len(inputs) + (0 if isinstance(block.input, dict) else 1)
    if extra:
        inputs["extra_args"] = extra
    labels: dict[str, str] = {}
    if block.name in spec.tool_args:
        labels["tool"] = block.name
    else:
        inputs["tool"] = block.name
    audit.record("agent.tool_use", inputs, {"ok": True}, labels=labels)


async def run_subagent(
    *,
    spec: SubagentSpec,
    prompt: str,
    tools: Sequence[SdkMcpTool[Any]],
    allowed_tools: Sequence[str],
    state: RunState,
    audit: AuditContext,
    run_inputs: Mapping[str, object],
    runtime: AgentRuntime | None = None,
) -> AgentResult:
    if set(spec.tool_args) != set(allowed_tools):
        raise ValueError("every allowed tool needs its argument names in the spec")
    try:
        action_id = audit.attempt("agent.run", run_inputs)
    except AuditWriteError:
        log.error("agent.run not recorded; %s not run", spec.actor)
        failure = _run_failure("unexpected_error", {}, run_inputs)
        return AgentResult(RunStatus.FAILED, (), (failure,), audit.correlation_id)

    run_code: str | None = None
    summary = ""
    finished = False
    audit_lost = False
    try:
        server = create_sdk_mcp_server(spec.server_name, tools=list(tools))
        options = agent_options(
            system_prompt=spec.system_prompt,
            model=spec.model,
            tools=[],
            runtime=runtime,
            mcp_servers={spec.server_name: server},
            allowed_tools=list(allowed_tools),
            permission_mode="dontAsk",
            max_turns=spec.max_turns,
        )
        # Runs to the end even after an error: leaving early strands the SDK's generator.
        async for item in query(prompt=prompt, options=options):
            if isinstance(item, AssistantMessage):
                for block in item.content:
                    if isinstance(block, ToolUseBlock):
                        try:
                            _audit_tool_use(audit, block, spec)
                        except AuditWriteError:
                            # Keep draining: leaving the loop strands the generator (review L5).
                            log.error("agent.tool_use not recorded for %s", spec.actor)
                            audit_lost = True
            elif isinstance(item, ResultMessage):
                finished = True
                run_code = _result_code(item)
                if run_code is None:
                    summary = item.result or ""
    except ClaudeSDKError as error:
        # The SDK raises after an error result too, and that result names the cause.
        log.error("%s run failed: %s", spec.actor, type(error).__name__)
        run_code = run_code or "model_unavailable"
    except Exception as error:  # noqa: BLE001 - its text must go nowhere
        log.error("%s run failed: %s", spec.actor, type(error).__name__)
        run_code = "unexpected_error"
    if not finished and run_code is None:
        run_code = "model_unavailable"

    results, failures = state.results()
    status = derive_status([s.name for s in state.steps], results)
    if run_code is not None:
        failures = (
            *failures,
            _run_failure(run_code, {r.step: r.value for r in results}, run_inputs),
        )
        if status is RunStatus.COMPLETE:
            status = RunStatus.PARTIAL
    if audit_lost:
        failures = (*failures, _run_failure("audit_incomplete_run", {}, run_inputs))
        if status is RunStatus.COMPLETE:
            status = RunStatus.PARTIAL
    result = AgentResult(status, results, failures, audit.correlation_id, summary)
    outcome = {
        "ok": status is not RunStatus.FAILED,
        "status": status.value,
        "steps_ok": len(results),
        "failures": [f.code for f in failures],
        "summary": {"type": "str", "length": len(summary)},
    }
    try:
        audit.outcome(action_id, "agent.run", run_inputs, outcome)
    except AuditWriteError:
        # The caller learns it too, as the views' AUDIT_INCOMPLETE does (security review L5).
        log.error("agent.run outcome not recorded for %s", spec.actor)
        lost = _run_failure("audit_incomplete_run", {r.step: r.value for r in results}, run_inputs)
        status = RunStatus.PARTIAL if result.status is RunStatus.COMPLETE else result.status
        result = replace(result, status=status, failures=(*result.failures, lost))
    return result
