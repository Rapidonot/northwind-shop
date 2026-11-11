"""Criteria-level gap tests for BE-08 and BE-02 (row #20), written from the PRD acceptance text and
the public interfaces only.

BE-08: every agent action writes an audit event (actor, action, inputs by reference, outcome,
correlation ID); any order can be reconstructed end to end from audit events alone.
BE-02: structured errors between agents; subagents recover transient failures locally; injected
timeouts give partial results with annotations, not a crashed run or a silent empty success.

Synthetic data only; the one card number used is the sandbox test PAN."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.agents.orders_check import (
    FRAUD_TOOL_NAME,
    PAYMENT_TOOL_NAME,
    OrdersCheckTask,
    run_orders_check,
)
from northwind_backend.audit import (
    AuditEvent,
    InMemoryAuditSink,
    JsonlAuditSink,
    verify_chain,
)
from northwind_backend.errors import (
    AUDIT_UNAVAILABLE,
    TIMEOUT,
    FailureType,
    Ok,
    RunStatus,
    StepResult,
    ToolError,
    derive_status,
)
from northwind_backend.fakes._base import FaultKind
from northwind_backend.ports import Money, PaymentStatus
from northwind_backend.reconstruct import reconstruct_order
from northwind_backend.recovery import RetryPolicy, call_with_recovery
from northwind_backend.views import AuditedFraudPort, AuditedPaymentPort
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM

from ._confirm import confirm, ref_of
from ._driver import (
    Fakes,
    Views,
    amount_for,
    context,
    orders,
    setup_captured,
)
from ._reconcile import assert_reconciles

TEST_PAN = "4111111111111111"  # the sandbox test card


def _policy(attempts: int = 3, deadline: float | None = 0.05) -> RetryPolicy:
    return RetryPolicy(max_attempts=attempts, deadline_seconds=deadline, backoff_seconds=())


def _clean_rows(count: int) -> list[dict[str, Any]]:
    return [row for row in orders() if row["exception"] is None][:count]


def _authorize(views: Views, row: Mapping[str, Any], key: str = "auth-1") -> str:
    done = views.payment.authorize(
        idempotency_key=key,
        payment_token=row["payment_token"],
        amount=amount_for(row),
        order_id=row["order_id"],
        confirmation_ref=confirm(views.payment, row["order_id"], amount_for(row), key),
    )
    assert isinstance(done, Ok), done
    return done.value.intent_id


class FlakySink:
    """A sink that refuses the writes `refuse` picks, and passes the rest to a real sink."""

    def __init__(self, refuse: Callable[[Mapping[str, Any]], bool]) -> None:
        self.inner = InMemoryAuditSink()
        self._refuse = refuse

    def append(self, draft: Mapping[str, Any]) -> AuditEvent:
        if self._refuse(draft):
            raise OSError("disk full")
        return self.inner.append(draft)

    def read_all(self) -> tuple[AuditEvent, ...]:
        return self.inner.read_all()


# BE-08 ------------------------------------------------------------------------------------------


@pytest.mark.req("BE-08")
def test_events_on_every_fault_path_carry_the_five_fields() -> None:
    """The criterion is "every action", so a hang, a timeout after the effect, a refusal by scope
    and a refusal of card data each write an event with actor, action, inputs, outcome and
    correlation ID."""
    row = _clean_rows(1)[0]
    fakes = Fakes.for_orders([row])
    sink = InMemoryAuditSink()
    audit = context(sink, row["order_id"])
    views = Views.full(fakes, audit)
    intent_id = _authorize(views, row)
    fakes.payment.faults.inject("capture", FaultKind.HANG, times=1)
    asyncio.run(
        call_with_recovery(
            lambda: views.payment.capture(
                idempotency_key="cap-1",
                intent_id=intent_id,
                confirmation_ref=ref_of(views.payment, intent_id),
            ),
            _policy(attempts=1),
        )
    )
    fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_AFTER, times=1)
    views.payment.capture(
        idempotency_key="cap-2",
        intent_id=intent_id,
        confirmation_ref=ref_of(views.payment, intent_id),
    )
    read_only = AuditedFraudPort(fakes.fraud, audit, allowed=frozenset({"read"}))
    refused = read_only.assess(
        idempotency_key="as-1", order_id=row["order_id"], shopper_id=row["shopper_id"]
    )
    assert isinstance(refused, ToolError) and refused.error_category.value == "permission"
    views.payment.void(idempotency_key=TEST_PAN, intent_id=intent_id)

    events = sink.read_all()
    assert len(events) >= 10
    for event in events:
        assert event.actor and event.action and event.correlation_id
        assert isinstance(event.inputs, Mapping)
        if event.phase == "outcome":
            assert isinstance(event.outcome, Mapping) and "ok" in event.outcome
        else:
            assert event.outcome is None
    assert TEST_PAN not in json.dumps([e.to_dict() for e in events])
    assert verify_chain(events) == []


@pytest.mark.req("BE-08")
def test_a_faulted_order_rebuilds_from_a_reloaded_jsonl_file_alone(tmp_path: Path) -> None:
    """Acceptance, on the fault path and through the disk: the capture took effect but every
    outcome was a timeout. A new sink on the same file (the fakes discarded) still lists it as
    uncertain with its amount, and a later status read, appended there, settles it."""
    row = _clean_rows(1)[0]
    path = tmp_path / "audit.jsonl"
    fakes = Fakes.for_orders([row])
    views = Views.full(fakes, context(JsonlAuditSink(path), row["order_id"]))
    intent_id = _authorize(views, row)
    fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_AFTER)
    asyncio.run(
        call_with_recovery(
            lambda: views.payment.capture(
                idempotency_key="cap-1",
                intent_id=intent_id,
                confirmation_ref=ref_of(views.payment, intent_id),
            ),
            _policy(attempts=2),
        )
    )
    truth = fakes.payment.status(intent_id)
    assert isinstance(truth, Ok) and truth.value.status is PaymentStatus.CAPTURED
    expected = amount_for(row).amount_minor
    order_id = row["order_id"]

    reloaded = JsonlAuditSink(path)
    assert verify_chain(reloaded.read_all()) == []
    record = reconstruct_order(reloaded.read_all(), order_id)
    assert_reconciles(record, fakes)
    assert [(u.action, u.amount_minor, u.settled) for u in record.uncertain] == [
        ("payment.capture", expected, "open")
    ]
    assert record.captured_minor == 0 and record.payment_label.endswith("uncertain)")

    # The read comes from a view bound to the reloaded sink, so only the file carries the history.
    reader = AuditedPaymentPort(
        fakes.payment, context(reloaded, order_id), allowed=frozenset({"status"})
    )
    assert isinstance(reader.status(intent_id), Ok)
    settled = reconstruct_order(JsonlAuditSink(path).read_all(), order_id)
    assert_reconciles(settled, fakes)
    assert settled.uncertain[0].settled == "done"
    assert settled.captured_minor == expected and settled.payment_status == "captured"


@pytest.mark.req("BE-08")
def test_dry_runs_never_change_the_reconstructed_order() -> None:
    row = _clean_rows(1)[0]
    fakes = Fakes.for_orders([row])
    sink = InMemoryAuditSink()
    views = Views.full(fakes, context(sink, row["order_id"]))
    intent_id = _authorize(views, row)
    for done in (
        views.payment.capture(
            idempotency_key="dry-cap",
            intent_id=intent_id,
            dry_run=True,
            confirmation_ref=ref_of(views.payment, intent_id),
        ),
        views.payment.void(idempotency_key="dry-void", intent_id=intent_id, dry_run=True),
    ):
        assert isinstance(done, Ok)
    truth = fakes.payment.status(intent_id)
    assert isinstance(truth, Ok) and truth.value.status is PaymentStatus.AUTHORIZED
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert record.intent_id == intent_id
    assert record.payment_status == "authorized" and record.captured_minor == 0
    assert record.uncertain == ()
    # The dry runs are still audited: nothing an agent does goes unrecorded.
    dry = [e for e in sink.read_all() if e.action in ("payment.capture", "payment.void")]
    assert [e.phase for e in dry] == ["attempt", "outcome", "attempt", "outcome"]


@pytest.mark.req("BE-08")
def test_one_orders_fault_never_appears_in_another_orders_record() -> None:
    first, second = _clean_rows(2)
    fakes = Fakes.for_orders([first, second])
    sink = InMemoryAuditSink()
    views_a = Views.full(fakes, context(sink, first["order_id"]))
    views_b = Views.full(fakes, context(sink, second["order_id"]))
    intent_a = _authorize(views_a, first, "auth-a")
    intent_b = _authorize(views_b, second, "auth-b")
    fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_AFTER, times=1)
    views_a.payment.capture(
        idempotency_key="cap-a",
        intent_id=intent_a,
        confirmation_ref=ref_of(views_a.payment, intent_a),
    )
    assert isinstance(
        views_b.payment.capture(
            idempotency_key="cap-b",
            intent_id=intent_b,
            confirmation_ref=ref_of(views_b.payment, intent_b),
        ),
        Ok,
    )

    record_a = reconstruct_order(sink.read_all(), first["order_id"])
    assert_reconciles(record_a, fakes)
    record_b = reconstruct_order(sink.read_all(), second["order_id"])
    assert_reconciles(record_b, fakes)
    assert [u.action for u in record_a.uncertain] == ["payment.capture"]
    assert record_b.uncertain == ()
    assert record_b.payment_status == "captured"
    assert record_b.captured_minor == amount_for(second).amount_minor
    assert {e.correlation_id for e in record_a.timeline} == {f"corr-{first['order_id']}"}
    assert {e.correlation_id for e in record_b.timeline} == {f"corr-{second['order_id']}"}


@pytest.mark.req("BE-08")
def test_fail_closed_survives_recovery_a_refused_attempt_never_runs_the_action() -> None:
    row = _clean_rows(1)[0]
    fakes = Fakes.for_orders([row])
    sink = FlakySink(lambda d: d["action"] == "payment.capture" and d["phase"] == "attempt")
    views = Views.full(fakes, context(sink, row["order_id"]))
    intent_id = _authorize(views, row)
    done = asyncio.run(
        call_with_recovery(
            lambda: views.payment.capture(
                idempotency_key="cap-1",
                intent_id=intent_id,
                confirmation_ref=ref_of(views.payment, intent_id),
            ),
            _policy(attempts=3),
        )
    )
    assert done.result == AUDIT_UNAVAILABLE and done.attempts == 3
    assert [e for e in fakes.payment.effects if e.operation == "capture"] == []
    truth = fakes.payment.status(intent_id)
    assert isinstance(truth, Ok) and truth.value.status is PaymentStatus.AUTHORIZED
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert record.payment_status == "authorized" and record.uncertain == ()


# BE-02 ------------------------------------------------------------------------------------------


@pytest.mark.req("BE-02")
def test_a_lost_outcome_write_is_recovered_locally_with_one_effect() -> None:
    """The action took effect but its outcome could not be written once. The recovery layer's
    same-key retry settles it: one effect, an Ok, and the order reconstructs as captured."""
    row = _clean_rows(1)[0]
    fakes = Fakes.for_orders([row])
    refused: list[str] = []

    def refuse_first_capture_outcome(draft: Mapping[str, Any]) -> bool:
        hit = draft["action"] == "payment.capture" and draft["phase"] == "outcome" and not refused
        if hit:
            refused.append(draft["action_id"])
        return hit

    sink = FlakySink(refuse_first_capture_outcome)
    views = Views.full(fakes, context(sink, row["order_id"]))
    intent_id = _authorize(views, row)
    done = asyncio.run(
        call_with_recovery(
            lambda: views.payment.capture(
                idempotency_key="cap-1",
                intent_id=intent_id,
                confirmation_ref=ref_of(views.payment, intent_id),
            ),
            _policy(attempts=3),
        )
    )
    assert isinstance(done.result, Ok) and done.attempts == 2
    assert [e.operation for e in fakes.payment.effects].count("capture") == 1
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert record.payment_status == "captured"
    assert record.captured_minor == amount_for(row).amount_minor
    assert all(u.settled == "done" for u in record.uncertain)


@pytest.mark.req("BE-02")
@pytest.mark.parametrize("operation", ["authorize", "assess", "create_shipment", "void", "refund"])
def test_a_timeout_after_the_effect_is_retried_to_exactly_one_effect(operation: str) -> None:
    """Local recovery on every state-changing call: the retry reuses the key, so the effect that
    already happened is replayed, not run again."""
    row = _clean_rows(1)[0]
    fakes = Fakes.for_orders([row])
    sink = InMemoryAuditSink()
    views = Views.full(fakes, context(sink, row["order_id"]))
    order_id, money = row["order_id"], amount_for(row)
    calls: dict[str, Callable[[], Any]] = {
        "authorize": lambda: views.payment.authorize(
            idempotency_key="k-1",
            payment_token=row["payment_token"],
            amount=money,
            order_id=order_id,
            confirmation_ref=confirm(views.payment, order_id, money, "k-1"),
        ),
        "assess": lambda: views.fraud.assess(
            idempotency_key="k-1", order_id=order_id, shopper_id=row["shopper_id"]
        ),
        "create_shipment": lambda: views.carrier.create_shipment(
            idempotency_key="k-1",
            order_id=order_id,
            region=row["region"],
            postal_code=row["shipping_postal_code"],
        ),
    }
    if operation in ("void", "refund"):
        intent_id = _authorize(views, row)
        if operation == "refund":
            assert isinstance(
                views.payment.capture(
                    idempotency_key="cap-1",
                    intent_id=intent_id,
                    confirmation_ref=ref_of(views.payment, intent_id),
                ),
                Ok,
            )
            calls[operation] = lambda: views.payment.refund(
                idempotency_key="k-1", intent_id=intent_id, amount=Money(100, money.currency)
            )
        else:
            calls[operation] = lambda: views.payment.void(
                idempotency_key="k-1", intent_id=intent_id
            )
    service = {
        "assess": fakes.fraud,
        "create_shipment": fakes.carrier,
    }.get(operation, fakes.payment)
    service.faults.inject(operation, FaultKind.TIMEOUT_AFTER, times=1)

    done = asyncio.run(call_with_recovery(calls[operation], _policy(attempts=2)))
    assert isinstance(done.result, Ok) and done.attempts == 2
    assert [e.operation for e in service.effects].count(operation) == 1


@pytest.mark.req("BE-02")
@pytest.mark.parametrize(
    "kind", [FaultKind.TIMEOUT_BEFORE, FaultKind.TIMEOUT_AFTER, FaultKind.HANG]
)
def test_a_persistent_timeout_on_a_read_is_an_error_never_a_silent_empty_success(
    kind: FaultKind,
) -> None:
    """The fraud read is the one call whose valid answer is empty (`Ok(())`). A timeout, however
    it is injected, must never come back as that, and every attempt is audited."""
    row = _clean_rows(1)[0]
    fakes = Fakes.for_orders([row])
    sink = InMemoryAuditSink()
    views = Views.full(fakes, context(sink, row["order_id"]))
    fakes.fraud.faults.inject("read", kind)
    done = asyncio.run(
        call_with_recovery(lambda: views.fraud.assessments_for(row["order_id"]), _policy(2))
    )
    assert not isinstance(done.result, Ok)
    assert done.result == TIMEOUT and done.attempts == 2
    reads = [e for e in sink.read_all() if e.action == "fraud.read"]
    assert [e.phase for e in reads] == ["attempt", "outcome", "attempt", "outcome"]
    outcomes = [e.outcome or {} for e in reads[1::2]]
    assert all(o.get("ok") is False and o.get("code") == "timeout" for o in outcomes)
    assert not any(e.outcome and e.outcome.get("empty") for e in reads)
    assert fakes.fraud.faults.bound_expired is False


@pytest.mark.req("BE-02")
def test_derive_status_counts_only_finished_required_steps() -> None:
    def ok(step: str) -> StepResult:
        return StepResult(step, ok=True, empty=False, value={})

    def bad(step: str) -> StepResult:
        return StepResult(step, ok=False, empty=False, value=None)

    required = ["fraud_assessments", "payment_status"]
    assert derive_status(required, [ok("fraud_assessments"), ok("payment_status")]) is (
        RunStatus.COMPLETE
    )
    assert derive_status(required, [ok("fraud_assessments"), bad("payment_status")]) is (
        RunStatus.PARTIAL
    )
    assert derive_status(required, [bad("fraud_assessments"), bad("payment_status")]) is (
        RunStatus.FAILED
    )
    # A finished step nobody required satisfies nothing, however many there are.
    assert derive_status(required, [ok("other"), ok("another")]) is RunStatus.FAILED
    assert derive_status(required, []) is RunStatus.FAILED


@pytest.mark.req("BE-02")
def test_a_hung_fraud_read_gives_an_annotated_partial_result_through_the_agent(
    tmp_path: Path,
) -> None:
    """The acceptance sentence end to end: a real deadline on a hung call, the scripted model
    claiming success, and the run still returns a structured partial result, with every attempt
    audited and no card data anywhere in what it returns."""
    row = _clean_rows(1)[0]
    fakes = Fakes.for_orders([row])
    sink = InMemoryAuditSink()
    order_id = row["order_id"]
    intent_id = setup_captured(row, fakes, sink, assess=True)
    fakes.fraud.faults.bound_seconds = 5.0
    fakes.fraud.faults.inject("read", FaultKind.HANG)
    policy = RetryPolicy(max_attempts=2, deadline_seconds=0.1, backoff_seconds=())

    with ScriptedLLM() as llm:
        llm.reply_tool_use(FRAUD_TOOL_NAME, {"order_id": order_id}, tool_id="toolu_fraud")
        llm.reply_tool_use(PAYMENT_TOOL_NAME, {"intent_id": intent_id}, tool_id="toolu_pay")
        llm.reply("Everything checked out.")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path)
        result = asyncio.run(
            run_orders_check(
                OrdersCheckTask(order_id=order_id, intent_id=intent_id),
                payment=fakes.payment,
                fraud=fakes.fraud,
                audit=context(sink, order_id),
                correlation_id="run-hang",
                runtime=runtime,
                policy=policy,
            )
        )

    assert fakes.fraud.faults.bound_expired is False
    assert result.status is RunStatus.PARTIAL
    [failure] = result.failures
    assert failure.failure_type is FailureType.TIMEOUT and failure.code == "timeout"
    assert failure.attempted_action == "fraud.read" and failure.attempts == 2
    assert failure.is_retryable and failure.alternatives
    assert "payment_status" in failure.partial_results
    assert [r.step for r in result.results] == ["payment_status"]
    envelope = result.to_envelope()
    assert envelope["status"] == "partial" and envelope["correlationId"] == "run-hang"
    assert TEST_PAN not in json.dumps(envelope)

    run_events = [e for e in sink.read_all() if e.correlation_id == "run-hang"]
    reads = [e for e in run_events if e.action == "fraud.read"]
    assert [e.phase for e in reads] == ["attempt", "outcome", "attempt", "outcome"]
    ids = [e.action_id for e in reads]
    assert len(set(ids)) == 2 and ids[0] == ids[1] and ids[2] == ids[3]
    assert verify_chain(sink.read_all()) == []
    record = reconstruct_order(sink.read_all(), order_id)
    assert_reconciles(record, fakes)
    assert "run-hang" in record.agent_runs
