"""Row #20, row review at 0035e0b (docs/reviews/2026-11-10-BE-08-advisor-row.md): F1 and F2, written
before the fix and seen failing on the old `reconstruct.py`."""

from __future__ import annotations

import pytest

from northwind_backend.audit import InMemoryAuditSink, outcome_ref
from northwind_backend.errors import TIMEOUT, Ok
from northwind_backend.fakes._base import FaultKind
from northwind_backend.ports import Money
from northwind_backend.reconstruct import reconstruct_order

from ._driver import Fakes, Views, amount_for, context, first_of, orders
from ._reconcile import assert_reconciles

pytestmark = pytest.mark.req("BE-08")


def _authorized(capture: bool = False) -> tuple[dict, Fakes, InMemoryAuditSink, Views, str]:  # type: ignore[type-arg]
    row = first_of(orders(), None)
    fakes = Fakes.for_orders([row])
    sink = InMemoryAuditSink()
    views = Views.full(fakes, context(sink, row["order_id"]))
    made = views.payment.authorize(
        idempotency_key="auth-1",
        payment_token=row["payment_token"],
        amount=amount_for(row),
        order_id=row["order_id"],
    )
    assert isinstance(made, Ok)
    if capture:
        assert isinstance(
            views.payment.capture(idempotency_key="cap-0", intent_id=made.value.intent_id), Ok
        )
    return row, fakes, sink, views, made.value.intent_id


# F1: a read between two timed-out attempts on one key must not settle the later one ---------------


@pytest.mark.parametrize("operation", ["capture", "void", "refund"])
def test_a_read_before_a_later_timed_out_attempt_does_not_settle_it(operation: str) -> None:
    """1. times out before its effect; 2. a status read shows nothing; 3. a same-key retry takes
    effect and also times out; 4. no later read. The action happened, so it must stay `uncertain`
    and open, never `not_done`, never absent."""
    row, fakes, sink, views, intent_id = _authorized(capture=operation == "refund")
    refund = Money(100, amount_for(row).currency)

    def call() -> object:
        if operation == "capture":
            return views.payment.capture(idempotency_key="k-1", intent_id=intent_id)
        if operation == "void":
            return views.payment.void(idempotency_key="k-1", intent_id=intent_id)
        return views.payment.refund(idempotency_key="k-1", intent_id=intent_id, amount=refund)

    fakes.payment.faults.inject(operation, FaultKind.TIMEOUT_BEFORE, times=1)
    assert call() is TIMEOUT
    assert isinstance(views.payment.status(intent_id), Ok)
    fakes.payment.faults.inject(operation, FaultKind.TIMEOUT_AFTER, times=1)
    assert call() is TIMEOUT
    assert any(e.operation == operation for e in fakes.payment.effects)  # it really happened
    record = reconstruct_order(sink.read_all(), row["order_id"])
    [entry] = [u for u in record.uncertain if u.action == f"payment.{operation}"]
    assert entry.settled == "open"
    assert "uncertain" in record.payment_label
    assert_reconciles(record, fakes)


def test_a_read_after_the_last_attempt_still_settles_it() -> None:
    row, fakes, sink, views, intent_id = _authorized()
    fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_BEFORE, times=1)
    views.payment.capture(idempotency_key="k-1", intent_id=intent_id)
    views.payment.status(intent_id)
    fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_AFTER, times=1)
    views.payment.capture(idempotency_key="k-1", intent_id=intent_id)
    views.payment.status(intent_id)  # after the last doubtful attempt
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert [u.settled for u in record.uncertain] == ["done"]
    assert record.captured_minor == amount_for(row).amount_minor
    assert_reconciles(record, fakes)


# F2: a dry run under the same key settles nothing -------------------------------------------------


def test_a_same_key_dry_run_never_settles_a_timed_out_call() -> None:
    row, fakes, sink, views, intent_id = _authorized()
    fakes.payment.faults.inject("capture", FaultKind.TIMEOUT_BEFORE, times=1)
    assert views.payment.capture(idempotency_key="k-1", intent_id=intent_id) is TIMEOUT
    preview = views.payment.capture(idempotency_key="k-1", intent_id=intent_id, dry_run=True)
    assert isinstance(preview, Ok) and preview.value.dry_run
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert [(u.action, u.settled) for u in record.uncertain] == [("payment.capture", "open")]
    assert record.captured_minor == 0
    assert_reconciles(record, fakes)


def test_a_same_key_dry_run_after_an_effect_still_leaves_it_open() -> None:
    """Re-review L2: a refund lets a dry-run preview succeed after a real refund under the same key,
    so the dry-run `Ok` really reaches the group and must still settle nothing."""
    row, fakes, sink, views, intent_id = _authorized(capture=True)
    amount = Money(100, amount_for(row).currency)
    fakes.payment.faults.inject("refund", FaultKind.TIMEOUT_AFTER, times=1)
    assert (
        views.payment.refund(idempotency_key="r-1", intent_id=intent_id, amount=amount) is TIMEOUT
    )
    preview = views.payment.refund(
        idempotency_key="r-1", intent_id=intent_id, amount=amount, dry_run=True
    )
    assert isinstance(preview, Ok) and preview.value.dry_run
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert [(u.action, u.settled) for u in record.uncertain] == [("payment.refund", "open")]
    assert record.refunded_minor == 0
    assert_reconciles(record, fakes)


def test_calls_whose_keys_are_unreadable_are_never_one_group() -> None:
    """Related low under F2: two keys written only as type and length must not merge, so an `Ok` for
    one can't settle the other. Built directly through the audit context, because the fakes refuse
    a malformed key before any effect."""
    row, fakes, sink, views, intent_id = _authorized()
    audit = context(sink, row["order_id"])
    audit.record(
        "payment.capture",
        {"idempotency_key": "bad key one", "intent_id": intent_id},
        outcome_ref(TIMEOUT),
    )
    captured = fakes.payment.capture(idempotency_key="cap-ok", intent_id=intent_id)
    assert isinstance(captured, Ok)
    audit.record(
        "payment.capture",
        {"idempotency_key": "bad key two", "intent_id": intent_id},
        outcome_ref(captured),
    )
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert [u.settled for u in record.uncertain] == ["open"]
    assert_reconciles(record, fakes)


# Re-review L1: another key's refund must not settle a refund that never happened ----------------


def test_another_keys_refund_does_not_settle_a_refund_that_never_happened() -> None:
    """Refund K times out before its effect; refund J on the same intent succeeds; a later status
    read shows a larger refunded total. K never happened, so it must not be labelled `done`."""
    row, fakes, sink, views, intent_id = _authorized(capture=True)
    amount = Money(100, amount_for(row).currency)
    fakes.payment.faults.inject("refund", FaultKind.TIMEOUT_BEFORE, times=1)
    assert (
        views.payment.refund(idempotency_key="r-k", intent_id=intent_id, amount=amount) is TIMEOUT
    )
    assert isinstance(
        views.payment.refund(idempotency_key="r-j", intent_id=intent_id, amount=amount), Ok
    )
    assert isinstance(views.payment.status(intent_id), Ok)
    record = reconstruct_order(sink.read_all(), row["order_id"])
    [entry] = [u for u in record.uncertain if u.idempotency_key == "r-k"]
    assert entry.settled != "done"
    assert record.refunded_minor == 100
    assert_reconciles(record, fakes)


def test_a_refund_that_never_happened_is_settled_not_done_by_a_later_read() -> None:
    """With no other refund on the intent, a later status read settles a timed-out refund, and the
    label must agree with the fake's own record for its key (re-review L1, helper check)."""
    row, fakes, sink, views, intent_id = _authorized(capture=True)
    amount = Money(100, amount_for(row).currency)
    fakes.payment.faults.inject("refund", FaultKind.TIMEOUT_BEFORE, times=1)
    assert (
        views.payment.refund(idempotency_key="r-k", intent_id=intent_id, amount=amount) is TIMEOUT
    )
    assert isinstance(views.payment.status(intent_id), Ok)
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert [(u.idempotency_key, u.settled) for u in record.uncertain] == [("r-k", "not_done")]
    assert_reconciles(record, fakes)
