"""One result from many subagent runs (row #24, BE-01; BE-02's propagation).

Each concern keeps its own section: the subagent's structured result, with its failures, attempted
actions, partial results and alternatives as BE-02 shaped them. The overall status comes from the
sections, never from model text: every section complete is complete, none is failed, anything else
is partial. An unsupported concern is a section of its own with a `not_supported` failure, so it is
annotated, never dropped.

The answer staff read is built here from the step values (`StepResult.value`), never from a
subagent's or the planner's model text (#20 cassette read C1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from northwind_backend.audit import input_refs
from northwind_backend.errors import (
    AgentFailure,
    AgentResult,
    FailureType,
    RunStatus,
    StepResult,
    alternatives_for,
)
from northwind_backend.orchestrator.plan import Concern, Kind

# Minor units per major unit for the fakes' currencies (all four have two decimals).
_EXPONENT = 2


@dataclass(frozen=True, slots=True)
class Section:
    """One concern and what was done for it."""

    concern: Concern
    correlation_id: str
    result: AgentResult

    def to_envelope(self) -> dict[str, Any]:
        return {
            "concern": self.concern.to_envelope(),
            "correlationId": self.correlation_id,
            **{k: v for k, v in self.result.to_envelope().items() if k != "correlationId"},
        }


@dataclass(frozen=True, slots=True)
class OrchestratorResult:
    """The one result of a request. `failures` are the orchestrator's own (the request or the
    plan); each section carries its subagent's."""

    status: RunStatus
    sections: tuple[Section, ...]
    failures: tuple[AgentFailure, ...]
    correlation_id: str
    answer: str

    def to_envelope(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "sections": [s.to_envelope() for s in self.sections],
            "failures": [f.to_envelope() for f in self.failures],
            "correlationId": self.correlation_id,
            "answer": self.answer,
        }


def failure(
    code: str,
    failure_type: FailureType,
    action: str,
    inputs: Mapping[str, object],
    *,
    retryable: bool,
    partial: Mapping[str, Any] | None = None,
    attempts: int = 0,
) -> AgentFailure:
    return AgentFailure(
        failure_type=failure_type,
        code=code,
        attempted_action=action,
        attempted_inputs=input_refs(inputs),
        partial_results=dict(partial or {}),
        alternatives=alternatives_for(code),
        is_retryable=retryable,
        attempts=attempts,
    )


def not_supported(concern: Concern, correlation_id: str) -> AgentResult:
    inputs: dict[str, object] = {"order_id": concern.order_id, "reason": concern.topic}
    if concern.intent_id is not None:
        inputs["intent_id"] = concern.intent_id
    gap = failure(
        "not_supported", FailureType.BUSINESS, "orchestrator.route", inputs, retryable=False
    )
    return AgentResult(RunStatus.FAILED, (), (gap,), correlation_id)


def overall(sections: Sequence[Section]) -> RunStatus:
    statuses = [s.result.status for s in sections]
    if statuses and all(s is RunStatus.COMPLETE for s in statuses):
        return RunStatus.COMPLETE
    if not statuses or all(s is RunStatus.FAILED for s in statuses):
        return RunStatus.FAILED
    return RunStatus.PARTIAL


def _money(minor: object, currency: object) -> str:
    if type(minor) is not int or not isinstance(currency, str):
        return "unknown amount"
    whole, part = divmod(minor, 10**_EXPONENT)
    return f"{currency} {whole}.{part:0{_EXPONENT}d}"


def _payment_line(value: Mapping[str, Any]) -> str:
    status = value.get("status", "unknown")
    currency = value.get("currency")
    parts = [f"authorized {_money(value.get('authorized_minor'), currency)}"]
    for name in ("captured", "refunded"):
        minor = value.get(f"{name}_minor")
        if type(minor) is int and minor > 0:
            parts.append(f"{name} {_money(minor, currency)}")
    return f"payment {value.get('intent_id', '?')} is {status} ({', '.join(parts)})"


def _fraud_line(value: object, empty: bool) -> str:
    if empty or not isinstance(value, list) or not value:
        return "no fraud assessment yet"
    last = value[-1]
    if not isinstance(last, Mapping):
        return "fraud assessment unreadable"
    reasons = last.get("reasons") or []
    why = f"; reasons: {', '.join(str(r) for r in reasons)}" if reasons else ""
    return f"fraud decision {last.get('decision', '?')} (score {last.get('score', '?')}{why})"


def _tracking_line(value: object, empty: bool) -> str:
    if not isinstance(value, Mapping):
        return "tracking unreadable"
    head = f"parcel {value.get('tracking_number', '?')} with {value.get('carrier', '?')}"
    scans = value.get("scans")
    if empty or not isinstance(scans, list) or not scans:
        return f"{head}: no scans yet"
    last = scans[-1] if isinstance(scans[-1], Mapping) else {}
    return f"{head}: last scan {last.get('status', '?')} on {last.get('on', '?')}"


def _label(concern: Concern) -> str:
    target = f" for order {concern.order_id}" if concern.order_id else ""
    if concern.kind is Kind.UNSUPPORTED:
        return f"{concern.topic}{target}"
    name = "payment and fraud" if concern.kind is Kind.PAYMENT_FRAUD else "tracking"
    return f"{name}{target}"


def _section_lines(section: Section) -> list[str]:
    lines: list[str] = []
    for step in section.result.results:
        if step.step == "payment_status" and isinstance(step.value, Mapping):
            lines.append(_payment_line(step.value))
        elif step.step == "fraud_assessments":
            lines.append(_fraud_line(step.value, step.empty))
        elif step.step == "tracking":
            lines.append(_tracking_line(step.value, step.empty))
    for gap in section.result.failures:
        if gap.code == "not_supported":
            lines.append("not handled by the back-office agents")
        else:
            lines.append(f"{gap.attempted_action} not done ({gap.code})")
        lines.append(f"next: {', '.join(gap.alternatives)}")
    return lines


def answer(status: RunStatus, sections: Sequence[Section], failures: Sequence[AgentFailure]) -> str:
    done = sum(1 for s in sections if s.result.status is RunStatus.COMPLETE)
    head = f"Status: {status.value} ({done} of {len(sections)} concerns done)."
    out = [head]
    for number, section in enumerate(sections, start=1):
        out.append(f"{number}. {_label(section.concern)} [{section.result.status.value}]")
        out.extend(f"   - {line}" for line in _section_lines(section))
    for gap in failures:
        out.append(f"Orchestrator: {gap.code}; next: {', '.join(gap.alternatives)}")
    return "\n".join(out)


def _foreign_payment(step: StepResult, concern: Concern) -> bool:
    return (
        step.step == "payment_status"
        and isinstance(step.value, Mapping)
        and step.value.get("order_id") != concern.order_id
    )


def bound_to_its_order(section: Section) -> Section:
    """Defence in depth behind the Orders subagent's own check (security review M1, re-review N6):
    a payment of another order is taken out of the section, values and all, and the section says
    so; it can't stay complete."""
    foreign = [r for r in section.result.results if _foreign_payment(r, section.concern)]
    if not foreign:
        return section
    kept = tuple(r for r in section.result.results if r not in foreign)
    inputs = {"order_id": section.concern.order_id, "intent_id": section.concern.intent_id}
    gap = failure(
        "intent_not_for_order",
        FailureType.PERMISSION,
        "payment.status",
        inputs,
        retryable=False,
        partial={r.step: r.value for r in kept},
    )
    status = RunStatus.PARTIAL if kept else RunStatus.FAILED
    result = replace(
        section.result, status=status, results=kept, failures=(*section.result.failures, gap)
    )
    return replace(section, result=result)


def aggregate(
    sections: Sequence[Section], failures: Sequence[AgentFailure], correlation_id: str
) -> OrchestratorResult:
    """No section is failed. An orchestrator failure beside sections (an outcome the audit lost)
    makes a complete run partial."""
    sections = [bound_to_its_order(s) for s in sections]
    status = overall(sections)
    if failures and status is RunStatus.COMPLETE:
        status = RunStatus.PARTIAL
    return OrchestratorResult(
        status,
        tuple(sections),
        tuple(failures),
        correlation_id,
        answer(status, sections, failures),
    )
