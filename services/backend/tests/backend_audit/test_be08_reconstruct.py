"""BE-08: any order can be reconstructed end to end from audit events alone (row #20).

The comparison rule (S1) is the same in every test here: confirmed state must equal the fakes'
ground truth, and an `uncertain` action passes only if it is listed with its amount, is never
counted as done, and the truth for its key is either "happened" or "not happened". An uncertain
capture that happened is never reported as absent (R1)."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.audit import AuditEvent, InMemoryAuditSink, JsonlAuditSink, verify_chain
from northwind_backend.errors import Ok
from northwind_backend.fakes._base import FaultKind
from northwind_backend.ports import PaymentStatus
from northwind_backend.reconstruct import OrderRecord, reconstruct_order
from northwind_backend.recovery import RetryPolicy, call_with_recovery

from ._driver import (
    Fakes,
    Truth,
    Views,
    amount_for,
    by_exception,
    context,
    first_of,
    orders,
    run_all,
)
from ._reconcile import assert_reconciles

pytestmark = pytest.mark.req("BE-08")

EXPECTED_ACTIONS = {
    None: [
        "fraud.assess",
        "payment.authorize",
        "carrier.create_shipment",
        "payment.capture",
        "carrier.track",
    ],
    "stock_out": ["order.held"],
    "fraud_flag": ["fraud.assess", "order.held"],
    "soft_decline": ["fraud.assess", "payment.authorize", "order.held"],
    "address_error": [
        "fraud.assess",
        "payment.authorize",
        "carrier.create_shipment",
        "payment.void",
        "order.held",
    ],
}


def assert_matches_truth(
    record: OrderRecord, order: dict[str, Any], fakes: Fakes, truth: Truth
) -> None:
    order_id = order["order_id"]
    assessed = fakes.fraud.assessments_for(order_id)
    assert isinstance(assessed, Ok)
    if assessed.value:
        assert record.fraud_decision == assessed.value[0].decision.value
        assert record.fraud_score == assessed.value[0].score
    else:
        assert record.fraud_decision is None
    if truth.intent_id is None:
        assert record.intent_id is None and record.payment_status is None
    else:
        intent = fakes.payment.status(truth.intent_id)
        assert isinstance(intent, Ok)
        assert record.intent_id == intent.value.intent_id
        assert record.payment_status == intent.value.status.value
        assert record.authorized_minor == intent.value.authorized_minor
        assert record.captured_minor == intent.value.captured_minor
        assert record.refunded_minor == intent.value.refunded_minor
        assert record.currency == intent.value.currency
    assert record.shipment_id == truth.shipment_id
    assert record.tracking_number == truth.tracking_number
    if truth.tracking_number is not None:
        tracked = fakes.carrier.track(truth.tracking_number)
        assert isinstance(tracked, Ok)
        assert record.scans == len(tracked.value.events)
    assert record.held == truth.held
    assert record.escalated == truth.escalated
    assert record.uncertain == ()
    assert record.unknown == ()


def test_all_1000_orders_rebuild_from_reloaded_events_alone(tmp_path: Path) -> None:
    rows = orders()
    assert len(rows) == 1000
    fakes, sink, truths = run_all(rows)
    path = tmp_path / "audit.jsonl"
    disk = JsonlAuditSink(path)
    for event in sink.read_all():
        draft = {
            k: v
            for k, v in event.to_dict().items()
            if k not in ("seq", "event_id", "prev_hash", "hash")
        }
        disk.append(draft)
    # A fresh reader with the fakes out of reach: nothing below reads `sink` or a fake to rebuild.
    events = JsonlAuditSink(path).read_all()
    assert verify_chain(list(events)) == []
    for row in rows:
        record = reconstruct_order(events, row["order_id"])
        assert_reconciles(record, fakes)
        assert_matches_truth(record, row, fakes, truths[row["order_id"]])
        assert [e.action for e in record.timeline if e.phase == "outcome"] == EXPECTED_ACTIONS[
            row["exception"]
        ]
        assert record.order_id == row["order_id"]


def test_each_exception_kind_has_its_expected_timeline() -> None:
    rows = orders()
    fakes, sink, truths = run_all(rows)
    kinds = dict(by_exception(rows))
    assert set(kinds) == set(EXPECTED_ACTIONS)
    for kind, row in kinds.items():
        record = reconstruct_order(sink.read_all(), row["order_id"])
        assert_reconciles(record, fakes)
        assert truths[row["order_id"]].actions == EXPECTED_ACTIONS[kind]
        assert [e.phase for e in record.timeline] == ["attempt", "outcome"] * len(
            EXPECTED_ACTIONS[kind]
        )
    held = Counter(t.held for t in truths.values())
    assert held == {
        None: 900,
        "stock_out": 25,
        "fraud_flag": 25,
        "soft_decline": 25,
        "address_error": 25,
    }


def test_one_order_never_sees_another_orders_events() -> None:
    rows = orders()[:40]
    fakes, sink, _ = run_all(rows)
    for row in rows:
        record = reconstruct_order(sink.read_all(), row["order_id"])
        assert_reconciles(record, fakes)
        assert {e.correlation_id for e in record.timeline} == {f"corr-{row['order_id']}"}


def test_each_action_has_one_attempt_and_at_most_one_outcome() -> None:
    """R2: exactly one outcome, except where the sink refused the outcome write."""
    _, sink, _ = run_all()
    phases: dict[str, list[str]] = {}
    for event in sink.read_all():
        phases.setdefault(event.action_id, []).append(event.phase)
    assert all(p == ["attempt", "outcome"] for p in phases.values())


def test_fifty_interleaved_flows_keep_their_correlation_ids() -> None:
    rows = orders()[:50]
    fakes = Fakes.for_orders(rows)
    sink = InMemoryAuditSink()

    async def flow(row: dict[str, Any]) -> None:
        from ._driver import run_order

        await asyncio.to_thread(run_order, row, fakes, sink)

    async def all_flows() -> None:
        await asyncio.gather(*(flow(row) for row in rows))

    asyncio.run(all_flows())
    events = sink.read_all()
    assert verify_chain(list(events)) == []
    for row in rows:
        record = reconstruct_order(events, row["order_id"])
        assert_reconciles(record, fakes)
        assert {e.correlation_id for e in record.timeline} == {f"corr-{row['order_id']}"}


# Fault paths (M1) and uncertain actions (R1, S1, S2) ----------------------------------------------


def _captured_order() -> tuple[dict[str, Any], Fakes, InMemoryAuditSink, Views, str]:
    row = first_of(orders(), None)
    fakes = Fakes.for_orders([row])
    sink = InMemoryAuditSink()
    views = Views.full(fakes, context(sink, row["order_id"]))
    authorized = views.payment.authorize(
        idempotency_key=f"{row['order_id']}:authorize",
        payment_token=row["payment_token"],
        amount=amount_for(row),
        order_id=row["order_id"],
    )
    assert isinstance(authorized, Ok)
    return row, fakes, sink, views, authorized.value.intent_id


def _recover(call: Any, attempts: int = 1) -> Any:
    return asyncio.run(
        call_with_recovery(call, RetryPolicy(max_attempts=attempts, backoff_seconds=()))
    )


@pytest.mark.parametrize("fault", ["outage", "timeout_before"])
def test_a_failed_capture_appears_in_its_orders_timeline(fault: str) -> None:
    row, fakes, sink, views, intent_id = _captured_order()
    if fault == "outage":
        fakes.payment.outage = True
    else:
        fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_BEFORE)
    views.payment.capture(idempotency_key="cap-1", intent_id=intent_id)
    views.payment.status(intent_id)
    fakes.payment.outage = False
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert [(e.action, e.phase) for e in record.timeline][-4:] == [
        ("payment.capture", "attempt"),
        ("payment.capture", "outcome"),
        ("payment.status", "attempt"),
        ("payment.status", "outcome"),
    ]
    capture_codes = [
        e.code for e in record.timeline if e.action == "payment.capture" and e.phase == "outcome"
    ]
    assert capture_codes == ["service_unavailable" if fault == "outage" else "timeout"]
    assert record.captured_minor == 0
    assert fakes.payment.effects[-1].operation == "authorize"


@pytest.mark.parametrize("fault", ["outage", "timeout_before"])
def test_a_failed_void_and_status_join_their_order_through_the_intent(fault: str) -> None:
    row, fakes, sink, views, intent_id = _captured_order()
    for operation in ("void", "status"):
        if fault == "outage":
            fakes.payment.outage = True
        else:
            fakes.payment.faults.inject(operation, FaultKind.TIMEOUT_BEFORE, times=1)
    views.payment.void(idempotency_key="void-1", intent_id=intent_id)
    views.payment.status(intent_id)
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    actions = [e.action for e in record.timeline]
    assert actions.count("payment.void") == 2 and actions.count("payment.status") == 2


def test_an_audit_unavailable_capture_leaves_nothing_and_changes_nothing() -> None:
    from .test_be08_audit_log import FailingSink

    row = first_of(orders(), None)
    fakes = Fakes.for_orders([row])
    sink = FailingSink(set())
    views = Views.full(fakes, context(sink, row["order_id"]))
    authorized = views.payment.authorize(
        idempotency_key="auth-1",
        payment_token=row["payment_token"],
        amount=amount_for(row),
        order_id=row["order_id"],
    )
    assert isinstance(authorized, Ok)
    sink.fail_on = {sink.calls}
    views.payment.capture(idempotency_key="cap-1", intent_id=authorized.value.intent_id)
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert record.payment_status == "authorized" and record.uncertain == ()
    assert [e.operation for e in fakes.payment.effects] == ["authorize"]


@pytest.mark.parametrize("attempts", [1, 3])
def test_an_uncertain_capture_is_never_reported_as_absent(attempts: int) -> None:
    """R1: the capture happened, every outcome the view saw is TIMEOUT. The record must list it as
    uncertain with its amount, never as "authorized, not captured" and never as done."""
    row, fakes, sink, views, intent_id = _captured_order()
    fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_AFTER)
    _recover(lambda: views.payment.capture(idempotency_key="cap-1", intent_id=intent_id), attempts)
    truth = fakes.payment.status(intent_id)
    assert isinstance(truth, Ok) and truth.value.status is PaymentStatus.CAPTURED
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert [u.action for u in record.uncertain] == ["payment.capture"]
    entry = record.uncertain[0]
    assert entry.settled == "open"
    assert entry.amount_minor == amount_for(row).amount_minor
    assert record.payment_status == "authorized"
    assert record.payment_label == "authorized (payment.capture uncertain)"
    assert record.captured_minor == 0  # never counted as done


def test_a_later_status_read_settles_an_uncertain_capture() -> None:
    row, fakes, sink, views, intent_id = _captured_order()
    fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_AFTER, times=1)
    views.payment.capture(idempotency_key="cap-1", intent_id=intent_id)
    views.payment.status(intent_id)
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert record.uncertain[0].settled == "done"
    assert record.payment_status == "captured"
    assert record.captured_minor == amount_for(row).amount_minor


def test_a_status_read_that_shows_no_capture_settles_it_as_not_done() -> None:
    row, fakes, sink, views, intent_id = _captured_order()
    fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_BEFORE, times=1)
    views.payment.capture(idempotency_key="cap-1", intent_id=intent_id)
    views.payment.status(intent_id)
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert record.uncertain[0].settled == "not_done"
    assert record.payment_status == "authorized" and record.captured_minor == 0


def test_a_same_key_ok_settles_it_whatever_the_order_and_counts_once() -> None:
    """S1: settlement is by key. The Ok is written before the timed-out call's outcome here."""
    row, fakes, sink, views, intent_id = _captured_order()
    fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_AFTER, times=1)
    first = views.payment.capture(idempotency_key="cap-1", intent_id=intent_id)
    second = views.payment.capture(idempotency_key="cap-1", intent_id=intent_id)
    assert not isinstance(first, Ok) and isinstance(second, Ok)
    events = list(sink.read_all())
    # Swap the two capture outcomes' positions in what the reader sees: order must not matter.
    reordered = events[:-3] + [events[-1], events[-3], events[-2]]
    for seen in (events, reordered):
        record = reconstruct_order(tuple(seen), row["order_id"])
        assert_reconciles(record, fakes)
        assert [u.settled for u in record.uncertain] == ["done"]
        assert record.captured_minor == amount_for(row).amount_minor
    assert [e.operation for e in fakes.payment.effects] == ["authorize", "capture"]


@pytest.mark.parametrize("operation", ["authorize", "void"])
def test_other_money_calls_that_time_out_after_effect_are_uncertain(operation: str) -> None:
    row = first_of(orders(), None)
    fakes = Fakes.for_orders([row])
    sink = InMemoryAuditSink()
    views = Views.full(fakes, context(sink, row["order_id"]))
    if operation == "authorize":
        fakes.payment.faults.inject("authorize", FaultKind.TIMEOUT_AFTER)
        views.payment.authorize(
            idempotency_key="auth-1",
            payment_token=row["payment_token"],
            amount=amount_for(row),
            order_id=row["order_id"],
        )
        record = reconstruct_order(sink.read_all(), row["order_id"])
        assert_reconciles(record, fakes)
        assert [u.action for u in record.uncertain] == ["payment.authorize"]
        assert record.uncertain[0].amount_minor == amount_for(row).amount_minor
        assert record.payment_label == "none (payment.authorize uncertain)"
        return
    authorized = views.payment.authorize(
        idempotency_key="auth-1",
        payment_token=row["payment_token"],
        amount=amount_for(row),
        order_id=row["order_id"],
    )
    assert isinstance(authorized, Ok)
    fakes.payment.faults.inject("void", FaultKind.TIMEOUT_AFTER)
    views.payment.void(idempotency_key="void-1", intent_id=authorized.value.intent_id)
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert [u.action for u in record.uncertain] == ["payment.void"]
    assert record.payment_status == "authorized"


def test_a_refund_that_times_out_after_effect_is_uncertain() -> None:
    row, fakes, sink, views, intent_id = _captured_order()
    assert isinstance(views.payment.capture(idempotency_key="cap-1", intent_id=intent_id), Ok)
    fakes.payment.faults.inject("refund", FaultKind.TIMEOUT_AFTER)
    from northwind_backend.ports import Money

    views.payment.refund(
        idempotency_key="ref-1", intent_id=intent_id, amount=Money(100, amount_for(row).currency)
    )
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert [(u.action, u.amount_minor) for u in record.uncertain] == [("payment.refund", 100)]
    assert record.refunded_minor == 0


def test_an_audit_incomplete_capture_is_uncertain_until_its_retry() -> None:
    from .test_be08_audit_log import FailingSink

    row = first_of(orders(), None)
    fakes = Fakes.for_orders([row])
    sink = FailingSink(set())
    views = Views.full(fakes, context(sink, row["order_id"]))
    authorized = views.payment.authorize(
        idempotency_key="auth-1",
        payment_token=row["payment_token"],
        amount=amount_for(row),
        order_id=row["order_id"],
    )
    assert isinstance(authorized, Ok)
    sink.fail_on = {sink.calls + 1}
    intent_id = authorized.value.intent_id
    views.payment.capture(idempotency_key="cap-1", intent_id=intent_id)
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert [u.settled for u in record.uncertain] == ["open"]
    assert record.unknown != ()
    views.payment.capture(idempotency_key="cap-1", intent_id=intent_id)
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert [u.settled for u in record.uncertain] == ["done"]
    assert record.captured_minor == amount_for(row).amount_minor


def test_a_timed_out_read_is_a_failed_read_not_an_uncertain_action() -> None:
    row, fakes, sink, views, intent_id = _captured_order()
    fakes.payment.faults.inject("status", FaultKind.TIMEOUT_BEFORE)
    views.payment.status(intent_id)
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert_reconciles(record, fakes)
    assert record.uncertain == ()


def test_an_event_list_with_no_events_for_the_order_is_an_empty_record() -> None:
    record = reconstruct_order((), "O999999")
    assert record.timeline == () and record.payment_status is None


def _dump(events: tuple[AuditEvent, ...]) -> str:
    return "\n".join(json.dumps(e.to_dict(), sort_keys=True) for e in events)


def test_a_rerun_writes_byte_identical_events() -> None:
    rows = orders()[:30]
    assert _dump(run_all(rows)[1].read_all()) == _dump(run_all(rows)[1].read_all())
