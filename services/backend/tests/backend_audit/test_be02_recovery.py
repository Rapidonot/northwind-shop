"""BE-02: structured errors between agents, local recovery of transient failures, and a real
deadline on a hung call with no late effect (row #20; M3, N2, N6, R3, R4, S1, S3, S4)."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from northwind_backend.audit import InMemoryAuditSink
from northwind_backend.errors import (
    AUDIT_INCOMPLETE,
    AUDIT_UNAVAILABLE,
    TIMEOUT,
    UNEXPECTED,
    AgentFailure,
    AgentResult,
    ErrorCategory,
    FailureType,
    Ok,
    RunStatus,
    StepResult,
    ToolError,
    alternatives_for,
    business,
    derive_status,
    failure_type_of,
    permission,
    transient,
    validation,
)
from northwind_backend.fakes._base import FaultKind
from northwind_backend.reconstruct import reconstruct_order
from northwind_backend.recovery import (
    CANCEL,
    Recovered,
    RetryPolicy,
    call_with_recovery,
    with_deadline,
)

from ._driver import Fakes, Views, amount_for, context, first_of, orders
from ._reconcile import assert_reconciles

pytestmark = pytest.mark.req("BE-02")

DEADLINE = 0.05
BOUND = 5.0


# Envelopes ----------------------------------------------------------------------------------------


def test_the_tool_envelope_is_unchanged() -> None:
    """Regression pin on FOUND-3's wire keys (hard rule 10)."""
    assert Ok(1).to_envelope() == {"isError": False, "value": 1}
    assert business("x", "y").to_envelope() == {
        "isError": True,
        "errorCategory": "business",
        "isRetryable": False,
        "code": "x",
        "message": "y",
    }


def test_the_new_constants_are_fixed_transient_and_retryable() -> None:
    for error, code in [
        (TIMEOUT, "timeout"),
        (AUDIT_UNAVAILABLE, "audit_unavailable"),
        (AUDIT_INCOMPLETE, "audit_incomplete"),
        (UNEXPECTED, "unexpected_error"),
    ]:
        assert error.error_category is ErrorCategory.TRANSIENT
        assert error.is_retryable is True
        assert error.code == code
    assert (
        UNEXPECTED.message
        == "An unexpected error stopped the call. A retry with the same key is safe."
    )
    assert "may or may not have taken effect" in TIMEOUT.message
    assert "nothing was changed" not in AUDIT_INCOMPLETE.message.lower()


def test_failure_types_name_timeouts_and_unexpected_errors() -> None:
    assert failure_type_of(TIMEOUT) is FailureType.TIMEOUT
    assert failure_type_of(UNEXPECTED) is FailureType.UNEXPECTED
    assert failure_type_of(transient("service_unavailable", "m")) is FailureType.TRANSIENT
    assert failure_type_of(validation("v", "m")) is FailureType.VALIDATION
    assert failure_type_of(business("b", "m")) is FailureType.BUSINESS
    assert failure_type_of(permission("p", "m")) is FailureType.PERMISSION
    assert {t.value for t in FailureType} == {
        "transient",
        "validation",
        "business",
        "permission",
        "timeout",
        "not_run",
        "unexpected",
    }


def test_the_agent_failure_and_result_envelopes() -> None:
    failure = AgentFailure(
        failure_type=FailureType.TIMEOUT,
        code="timeout",
        attempted_action="fraud.read",
        attempted_inputs={"order_id": "O000001"},
        partial_results={"payment_status": {"status": "captured"}},
        alternatives=alternatives_for("timeout"),
        is_retryable=True,
        attempts=3,
    )
    assert failure.to_envelope() == {
        "failureType": "timeout",
        "code": "timeout",
        "attemptedAction": {"action": "fraud.read", "inputs": {"order_id": "O000001"}},
        "partialResults": {"payment_status": {"status": "captured"}},
        "alternatives": ["retry_later_same_key", "escalate_to_care"],
        "isRetryable": True,
        "attempts": 3,
    }
    result = AgentResult(
        status=RunStatus.PARTIAL,
        results=(StepResult("payment_status", ok=True, empty=False, value={"status": "captured"}),),
        failures=(failure,),
        correlation_id="corr-1",
        summary="Payment captured; fraud check timed out.",
    )
    envelope = result.to_envelope()
    assert set(envelope) == {"status", "results", "failures", "correlationId", "summary"}
    assert envelope["status"] == "partial"
    assert envelope["results"][0] == {
        "step": "payment_status",
        "ok": True,
        "empty": False,
        "value": {"status": "captured"},
        "annotation": None,
    }


def test_every_code_has_alternatives() -> None:
    for code in (
        "timeout",
        "service_unavailable",
        "unexpected_error",
        "audit_unavailable",
        "not_run",
    ):
        assert alternatives_for(code)
    assert alternatives_for("something_new") == ("escalate_to_care",)


@pytest.mark.parametrize(
    ("required", "oks", "failed", "expected"),
    [
        (("a", "b"), {"a", "b"}, set(), RunStatus.COMPLETE),
        (("a", "b"), {"a"}, {"b"}, RunStatus.PARTIAL),
        (("a", "b"), set(), {"a", "b"}, RunStatus.FAILED),
        (("a",), set(), {"a"}, RunStatus.FAILED),
        (("a",), {"a"}, set(), RunStatus.COMPLETE),
        (("a", "b"), {"a"}, set(), RunStatus.PARTIAL),  # b never ran
    ],
)
def test_the_status_rule(
    required: tuple[str, ...], oks: set[str], failed: set[str], expected: RunStatus
) -> None:
    results = tuple(StepResult(step, ok=True, empty=False, value=None) for step in sorted(oks))
    assert derive_status(required, results) is expected


def test_a_task_with_no_required_step_is_never_complete() -> None:
    """Zero of zero steps is not a success (found by test-writer, row #20)."""
    assert derive_status((), ()) is RunStatus.FAILED
    ok = (StepResult("anything", ok=True, empty=False, value=None),)
    assert derive_status((), ok) is RunStatus.FAILED


def test_a_valid_empty_step_is_complete_not_failed() -> None:
    results = (
        StepResult(
            "fraud_assessments", ok=True, empty=True, value=[], annotation="no assessment yet"
        ),
    )
    assert derive_status(("fraud_assessments",), results) is RunStatus.COMPLETE


# Recovery -----------------------------------------------------------------------------------------


def _policy(attempts: int = 3, deadline: float | None = DEADLINE) -> RetryPolicy:
    return RetryPolicy(max_attempts=attempts, deadline_seconds=deadline, backoff_seconds=())


@pytest.fixture
def order_setup() -> Iterator[tuple[dict[str, Any], Fakes, InMemoryAuditSink, Views, str]]:
    row = first_of(orders(), None)
    fakes = Fakes.for_orders([row])
    fakes.payment.faults.bound_seconds = BOUND
    sink = InMemoryAuditSink()
    views = Views.full(fakes, context(sink, row["order_id"]))
    authorized = views.payment.authorize(
        idempotency_key="auth-1",
        payment_token=row["payment_token"],
        amount=amount_for(row),
        order_id=row["order_id"],
    )
    assert isinstance(authorized, Ok)
    yield row, fakes, sink, views, authorized.value.intent_id
    assert fakes.payment.faults.bound_expired is False


def _capture(views: Views, intent_id: str, key: str = "cap-1") -> Any:
    return lambda: views.payment.capture(idempotency_key=key, intent_id=intent_id)


def _effects(fakes: Fakes, operation: str = "capture") -> int:
    return sum(1 for e in fakes.payment.effects if e.operation == operation)


@pytest.mark.parametrize("fault", ["outage", "timeout_before", "timeout_after"])
def test_a_transient_failure_is_recovered_with_the_same_key(order_setup: Any, fault: str) -> None:
    row, fakes, sink, views, intent_id = order_setup
    if fault == "outage":
        fakes.payment.faults.inject("capture", FaultKind.OUTAGE, times=1)
    elif fault == "timeout_before":
        fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_BEFORE, times=1)
    else:
        fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_AFTER, times=1)
    done = asyncio.run(call_with_recovery(_capture(views, intent_id), _policy()))
    assert isinstance(done.result, Ok) and done.attempts == 2
    assert _effects(fakes) == 1
    keys = [e.inputs["idempotency_key"] for e in sink.read_all() if e.action == "payment.capture"]
    assert keys == ["cap-1"] * 4


def test_an_unexpected_exception_is_retried_and_bounded(order_setup: Any) -> None:
    row, fakes, sink, views, intent_id = order_setup
    calls = {"n": 0}

    def boom() -> Any:
        calls["n"] += 1
        raise RuntimeError("password=hunter2")

    done = asyncio.run(call_with_recovery(boom, _policy(attempts=3)))
    assert done.result is UNEXPECTED and done.attempts == 3 and calls["n"] == 3


def test_a_persistent_failure_stops_at_max_attempts(order_setup: Any) -> None:
    row, fakes, sink, views, intent_id = order_setup
    fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_BEFORE)
    done = asyncio.run(call_with_recovery(_capture(views, intent_id), _policy(attempts=3)))
    assert isinstance(done.result, ToolError) and done.result.code == "timeout"
    assert done.attempts == 3
    assert _effects(fakes) == 0


@pytest.mark.parametrize(
    "error",
    [
        validation("v", "m"),
        business("b", "m"),
        permission("p", "m"),
        ToolError(ErrorCategory.TRANSIENT, False, "not_retryable", "m"),
    ],
)
def test_non_retryable_errors_are_tried_once(error: ToolError) -> None:
    calls = {"n": 0}

    def fail() -> ToolError:
        calls["n"] += 1
        return error

    done: Recovered[None] = asyncio.run(call_with_recovery(fail, _policy()))
    assert done.result is error and done.attempts == 1 and calls["n"] == 1


def test_backoff_uses_the_injected_sleep() -> None:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    asyncio.run(
        call_with_recovery(
            lambda: TIMEOUT,
            RetryPolicy(max_attempts=3, deadline_seconds=None, backoff_seconds=(0.1, 0.4)),
            sleep=sleep,
        )
    )
    assert slept == [0.1, 0.4]


# Hangs and the deadline (M3, N2, N6, R3, R4, S1, S3, S4) ------------------------------------------


def test_the_deadline_is_well_inside_the_bound() -> None:
    assert DEADLINE < BOUND / 10


def test_hang_case_1_no_retry_no_effect(order_setup: Any) -> None:
    row, fakes, sink, views, intent_id = order_setup
    fakes.payment.faults.inject("capture", FaultKind.HANG, times=1)
    started = time.monotonic()
    done = asyncio.run(call_with_recovery(_capture(views, intent_id), _policy(attempts=1)))
    assert time.monotonic() - started < BOUND / 2  # the real deadline fired, not the bound
    assert done.result is TIMEOUT and _effects(fakes) == 0
    captures = [e for e in sink.read_all() if e.action == "payment.capture"]
    assert [e.phase for e in captures] == ["attempt", "outcome"]
    assert captures[1].outcome["code"] == "timeout"
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert [(u.action, u.settled) for u in record.uncertain] == [("payment.capture", "open")]
    assert record.captured_minor == 0


def test_hang_case_2_the_recoverys_own_retry_runs_it_once(order_setup: Any) -> None:
    row, fakes, sink, views, intent_id = order_setup
    fakes.payment.faults.inject("capture", FaultKind.HANG, times=1)
    done = asyncio.run(call_with_recovery(_capture(views, intent_id), _policy(attempts=2)))
    assert isinstance(done.result, Ok) and done.attempts == 2 and _effects(fakes) == 1
    captures = [
        e for e in sink.read_all() if e.action == "payment.capture" and e.phase == "outcome"
    ]
    assert [e.outcome["ok"] for e in captures] == [False, True]
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert [u.settled for u in record.uncertain] == ["done"]
    assert record.captured_minor == amount_for(row).amount_minor


def test_hang_case_3_a_same_key_call_during_the_hang_runs_once(order_setup: Any) -> None:
    row, fakes, sink, views, intent_id = order_setup
    fakes.payment.faults.inject("capture", FaultKind.HANG, times=1)

    async def scenario() -> tuple[Any, Any]:
        # A longer deadline here (still a tenth of the bound), so the second call surely runs while
        # the first is hung.
        policy = _policy(attempts=1, deadline=BOUND / 10)
        first = asyncio.ensure_future(call_with_recovery(_capture(views, intent_id), policy))
        assert await asyncio.to_thread(fakes.payment.faults.waiting.wait, BOUND)
        second = await asyncio.to_thread(_capture(views, intent_id))
        return await first, second

    first, second = asyncio.run(scenario())
    assert first.result is TIMEOUT and isinstance(second, Ok)
    assert _effects(fakes) == 1
    status = fakes.payment.status(intent_id)
    assert isinstance(status, Ok) and status.value.captured_minor == amount_for(row).amount_minor
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert [u.settled for u in record.uncertain] == ["done"]
    assert record.captured_minor == amount_for(row).amount_minor  # counted once


def test_hang_case_4_a_same_key_call_after_the_cancel_finds_nothing_stored(
    order_setup: Any,
) -> None:
    row, fakes, sink, views, intent_id = order_setup
    fakes.payment.faults.inject("capture", FaultKind.HANG, times=1)
    first = asyncio.run(call_with_recovery(_capture(views, intent_id), _policy(attempts=1)))
    assert first.result is TIMEOUT and _effects(fakes) == 0
    second = _capture(views, intent_id)()
    assert isinstance(second, Ok) and _effects(fakes) == 1
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert [u.settled for u in record.uncertain] == ["done"]
    assert record.captured_minor == amount_for(row).amount_minor


def test_cancel_is_set_before_the_task_and_reset_after() -> None:
    """S3: the worker sees the call's own cancel event, and the caller's context is left clean."""
    seen: list[threading.Event | None] = []

    def probe() -> Any:
        seen.append(CANCEL.get())
        return Ok(None)

    async def scenario() -> None:
        await with_deadline(probe, DEADLINE)
        await with_deadline(probe, DEADLINE)
        assert CANCEL.get() is None

    asyncio.run(scenario())
    assert len(seen) == 2 and all(isinstance(e, threading.Event) for e in seen)
    assert seen[0] is not seen[1]


def test_a_bound_that_expires_also_aborts_with_no_effect(order_setup: Any) -> None:
    row, fakes, sink, views, intent_id = order_setup
    fakes.payment.faults.bound_seconds = 0.01
    fakes.payment.faults.inject("capture", FaultKind.HANG, times=1)
    # Called directly, with no deadline: nothing ever sets cancel, so only the bound ends the wait.
    assert _capture(views, intent_id)() is TIMEOUT
    assert _effects(fakes) == 0
    assert fakes.payment.faults.bound_expired is True
    fakes.payment.faults.bound_expired = False  # the fixture asserts it for every other test


def test_a_deadline_on_a_slow_success_returns_the_success() -> None:
    def slow() -> Any:
        return Ok("done")

    assert asyncio.run(with_deadline(slow, 1.0)) == Ok("done")


def test_with_deadline_turns_an_exception_into_unexpected() -> None:
    def boom() -> Any:
        raise ValueError("4111111111111111")

    assert asyncio.run(with_deadline(boom, 1.0)) is UNEXPECTED
