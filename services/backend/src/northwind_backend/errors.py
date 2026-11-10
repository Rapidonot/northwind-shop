"""Structured results for every port call (hard rule 10).

A call returns `Ok` or `ToolError`. An outage is a transient `ToolError`, and a query that found
nothing is an `Ok` holding an empty value, so "access failed" and "valid empty result" can't be
confused. `to_envelope()` gives the wire keys the tool design rules name.

Messages are fixed text. Nothing here echoes an argument, so a refused card number can't reach a log
through an error (hard rule 1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


class ErrorCategory(StrEnum):
    TRANSIENT = "transient"
    VALIDATION = "validation"
    BUSINESS = "business"
    PERMISSION = "permission"


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T
    is_error: Literal[False] = False

    def to_envelope(self) -> dict[str, Any]:
        return {"isError": False, "value": self.value}


@dataclass(frozen=True, slots=True)
class ToolError:
    """`is_retryable` means the same call, with the same key, may succeed later. A soft decline is
    not retryable in this sense: its result is recorded, and a new attempt takes a new key."""

    error_category: ErrorCategory
    is_retryable: bool
    code: str
    message: str
    is_error: Literal[True] = True

    def to_envelope(self) -> dict[str, Any]:
        return {
            "isError": True,
            "errorCategory": self.error_category.value,
            "isRetryable": self.is_retryable,
            "code": self.code,
            "message": self.message,
        }


type Result[T] = Ok[T] | ToolError


def transient(code: str, message: str) -> ToolError:
    return ToolError(ErrorCategory.TRANSIENT, True, code, message)


def validation(code: str, message: str) -> ToolError:
    return ToolError(ErrorCategory.VALIDATION, False, code, message)


def business(code: str, message: str) -> ToolError:
    return ToolError(ErrorCategory.BUSINESS, False, code, message)


def permission(code: str, message: str) -> ToolError:
    return ToolError(ErrorCategory.PERMISSION, False, code, message)


# BE-02 (row #20): structured errors between agents, built on the tool envelope above. Every message
# is fixed text, so nothing an argument or an exception carried can reach a log or a model through
# one.

TIMEOUT = transient(
    "timeout",
    "The call did not finish in time. It may or may not have taken effect; a retry with the same "
    "key is safe.",
)
AUDIT_UNAVAILABLE = transient(
    "audit_unavailable", "The action could not be recorded, so it was not run."
)
AUDIT_INCOMPLETE = transient(
    "audit_incomplete",
    "The action may have taken effect but its result was not recorded. Retry with the same key.",
)
# Transient and retryable, as in row #10's plan (the user's decision, N7), bounded by
# `max_attempts` with the same idempotency key. The store records nothing for an exception, so a
# retry runs the call again: harmless for calls that change nothing, which is all an agent gets in
# this row. Before any agent gets a state-changing tool, an exception after the effect must not lead
# to a second call (#21).
UNEXPECTED = transient(
    "unexpected_error", "An unexpected error stopped the call. A retry with the same key is safe."
)


class FailureType(StrEnum):
    TRANSIENT = "transient"
    VALIDATION = "validation"
    BUSINESS = "business"
    PERMISSION = "permission"
    TIMEOUT = "timeout"
    NOT_RUN = "not_run"
    UNEXPECTED = "unexpected"


def failure_type_of(error: ToolError) -> FailureType:
    if error.code == TIMEOUT.code:
        return FailureType.TIMEOUT
    if error.code == UNEXPECTED.code:
        return FailureType.UNEXPECTED
    return FailureType(error.error_category.value)


_ESCALATE = "escalate_to_care"
_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "timeout": ("retry_later_same_key", _ESCALATE),
    "service_unavailable": ("retry_later_same_key", _ESCALATE),
    "unexpected_error": ("retry_later_same_key", _ESCALATE),
    "audit_unavailable": ("retry_later_same_key", _ESCALATE),
    "audit_incomplete": ("retry_same_key_to_settle", _ESCALATE),
    "audit_incomplete_run": ("rerun_the_run", _ESCALATE),
    "not_run": ("rerun_the_step", _ESCALATE),
    "not_permitted": (_ESCALATE,),
    "model_unavailable": ("retry_later", _ESCALATE),
    "replay_miss": ("re_record_on_purpose", _ESCALATE),
    "max_turns": ("rerun_with_a_narrower_task", _ESCALATE),
}


def alternatives_for(code: str) -> tuple[str, ...]:
    """Fixed per code, never written by a model."""
    return _ALTERNATIVES.get(code, (_ESCALATE,))


@dataclass(frozen=True, slots=True)
class AgentFailure:
    """One step an agent could not finish. `attempted_inputs` holds references only (BE-08)."""

    failure_type: FailureType
    code: str
    attempted_action: str
    attempted_inputs: Mapping[str, Any]
    partial_results: Mapping[str, Any]
    alternatives: tuple[str, ...]
    is_retryable: bool
    attempts: int

    def to_envelope(self) -> dict[str, Any]:
        return {
            "failureType": self.failure_type.value,
            "code": self.code,
            "attemptedAction": {
                "action": self.attempted_action,
                "inputs": dict(self.attempted_inputs),
            },
            "partialResults": dict(self.partial_results),
            "alternatives": list(self.alternatives),
            "isRetryable": self.is_retryable,
            "attempts": self.attempts,
        }


class RunStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StepResult:
    """A required step that finished. `empty` is a valid empty result, not a failure (hard rule
    10)."""

    step: str
    ok: bool
    empty: bool
    value: Any
    annotation: str | None = None

    def to_envelope(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "ok": self.ok,
            "empty": self.empty,
            "value": self.value,
            "annotation": self.annotation,
        }


@dataclass(frozen=True, slots=True)
class AgentResult:
    status: RunStatus
    results: tuple[StepResult, ...]
    failures: tuple[AgentFailure, ...]
    correlation_id: str
    summary: str = ""

    def to_envelope(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "results": [r.to_envelope() for r in self.results],
            "failures": [f.to_envelope() for f in self.failures],
            "correlationId": self.correlation_id,
            "summary": self.summary,
        }


def derive_status(required: Sequence[str], results: Sequence[StepResult]) -> RunStatus:
    """From the steps that finished, never from model text: every required step ok is complete, some
    is partial, none is failed. A valid empty result is ok."""
    if not required:
        # Nothing required is nothing proved: never a silent empty success (BE-02).
        return RunStatus.FAILED
    done = {r.step for r in results if r.ok}
    finished = sum(1 for step in required if step in done)
    if finished == len(required):
        return RunStatus.COMPLETE
    return RunStatus.PARTIAL if finished else RunStatus.FAILED
