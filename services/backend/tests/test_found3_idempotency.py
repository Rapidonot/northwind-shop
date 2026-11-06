"""FOUND-3: idempotent replays, dry_run, and the transient-not-recorded rule (hard rule 9)."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from northwind_backend.errors import ErrorCategory, Ok, Result, ToolError
from northwind_backend.fakes.carrier import CarrierFake
from northwind_backend.fakes.fraud import FraudFake
from northwind_backend.fakes.payment import (
    SANDBOX_APPROVED,
    SANDBOX_SOFT_DECLINE,
    PaymentFake,
)
from northwind_backend.ports import Money, PaymentStatus

REPLAYS = 10
USD = "USD"


def _authorize(fake: PaymentFake, key: str = "auth-1", amount: int = 5000) -> str:
    result = fake.authorize(
        idempotency_key=key,
        payment_token=SANDBOX_APPROVED,
        amount=Money(amount, USD),
        order_id="O000001",
    )
    assert isinstance(result, Ok)
    return result.value.intent_id


def _captured(fake: PaymentFake) -> str:
    intent_id = _authorize(fake)
    assert isinstance(fake.capture(idempotency_key="cap-0", intent_id=intent_id), Ok)
    return intent_id


# Each case: a fake and a zero-arg call on it that always uses the same key.
def _payment_case(op: str) -> tuple[PaymentFake, Callable[[], Result[Any]]]:
    fake = PaymentFake()
    if op == "authorize":
        return fake, lambda: fake.authorize(
            idempotency_key="k1",
            payment_token=SANDBOX_APPROVED,
            amount=Money(5000, USD),
            order_id="O000001",
        )
    if op == "capture":
        intent_id = _authorize(fake)
        return fake, lambda: fake.capture(idempotency_key="k1", intent_id=intent_id)
    if op == "void":
        intent_id = _authorize(fake)
        return fake, lambda: fake.void(idempotency_key="k1", intent_id=intent_id)
    intent_id = _captured(fake)
    return fake, lambda: fake.refund(
        idempotency_key="k1", intent_id=intent_id, amount=Money(1000, USD)
    )


def _other_case(op: str) -> tuple[Any, Callable[[], Result[Any]]]:
    if op == "assess":
        fraud = FraudFake()
        return fraud, lambda: fraud.assess(
            idempotency_key="k1", order_id="O000001", shopper_id="S0001"
        )
    carrier = CarrierFake()
    return carrier, lambda: carrier.create_shipment(
        idempotency_key="k1", order_id="O000001", region="US", postal_code="US-1234"
    )


OPS = ["authorize", "capture", "void", "refund", "assess", "create_shipment"]


def _case(op: str) -> tuple[Any, Callable[[], Result[Any]]]:
    if op in ("assess", "create_shipment"):
        return _other_case(op)
    return _payment_case(op)


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize("op", OPS)
def test_replayed_ten_times_gives_one_effect_and_the_first_result(op: str) -> None:
    fake, call = _case(op)
    before = len(fake.effects)
    first = call()
    assert isinstance(first, Ok)
    for _ in range(REPLAYS):
        assert call() == first
    assert len(fake.effects) == before + 1


@pytest.mark.req("FOUND-3")
def test_replay_of_a_recorded_soft_decline_returns_the_first_result() -> None:
    fake = PaymentFake()

    def call() -> Result[Any]:
        return fake.authorize(
            idempotency_key="k1",
            payment_token=SANDBOX_SOFT_DECLINE,
            amount=Money(5000, USD),
            order_id="O000001",
        )

    first = call()
    assert isinstance(first, ToolError)
    assert first.error_category is ErrorCategory.BUSINESS
    for _ in range(REPLAYS):
        assert call() == first
    assert fake.effects == ()


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize("op", ["authorize", "capture", "assess", "create_shipment"])
def test_concurrent_replays_from_threads_give_one_effect(op: str) -> None:
    fake, call = _case(op)
    before = len(fake.effects)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: call(), range(48)))
    assert all(r == results[0] for r in results)
    assert isinstance(results[0], Ok)
    assert len(fake.effects) == before + 1


@pytest.mark.req("FOUND-3")
def test_a_different_request_on_a_used_key_is_refused() -> None:
    fake = PaymentFake()
    intent_id = _authorize(fake)
    first = fake.capture(idempotency_key="k1", intent_id=intent_id, amount=Money(1000, USD))
    assert isinstance(first, Ok)
    other = fake.capture(idempotency_key="k1", intent_id=intent_id, amount=Money(2000, USD))
    assert isinstance(other, ToolError)
    assert other.error_category is ErrorCategory.VALIDATION
    assert not other.is_retryable
    assert len(fake.effects) == 2  # the authorize and the one capture

    # And the used key is also refused across operations and for the other ports.
    assert isinstance(fake.void(idempotency_key="k1", intent_id=intent_id), ToolError)
    fraud = FraudFake()
    assert isinstance(fraud.assess(idempotency_key="k", order_id="O000001", shopper_id="S0001"), Ok)
    clash = fraud.assess(idempotency_key="k", order_id="O000002", shopper_id="S0001")
    assert isinstance(clash, ToolError)
    assert clash.error_category is ErrorCategory.VALIDATION
    assert len(fraud.effects) == 1
    carrier = CarrierFake()
    assert isinstance(
        carrier.create_shipment(
            idempotency_key="k", order_id="O000001", region="US", postal_code="US-1234"
        ),
        Ok,
    )
    clash2 = carrier.create_shipment(
        idempotency_key="k", order_id="O000002", region="US", postal_code="US-1234"
    )
    assert isinstance(clash2, ToolError)
    assert len(carrier.effects) == 1


@pytest.mark.req("FOUND-3")
def test_a_second_key_cannot_capture_twice() -> None:
    fake = PaymentFake()
    intent_id = _authorize(fake)
    assert isinstance(fake.capture(idempotency_key="cap-a", intent_id=intent_id), Ok)
    second = fake.capture(idempotency_key="cap-b", intent_id=intent_id)
    assert isinstance(second, ToolError)
    assert second.error_category is ErrorCategory.BUSINESS
    assert [e.operation for e in fake.effects].count("capture") == 1
    status = fake.status(intent_id)
    assert isinstance(status, Ok)
    assert status.value.captured_minor == 5000

    # Same for a partial capture followed by another key.
    fake2 = PaymentFake()
    intent2 = _authorize(fake2)
    assert isinstance(
        fake2.capture(idempotency_key="a", intent_id=intent2, amount=Money(1000, USD)), Ok
    )
    assert isinstance(fake2.capture(idempotency_key="b", intent_id=intent2), ToolError)
    assert [e.operation for e in fake2.effects].count("capture") == 1


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize("op", OPS)
def test_a_transient_failure_is_not_recorded_so_a_retry_does_the_effect_once(op: str) -> None:
    fake, call = _case(op)
    before = len(fake.effects)
    fake.outage = True
    down = call()
    assert isinstance(down, ToolError)
    assert down.error_category is ErrorCategory.TRANSIENT
    assert down.is_retryable
    assert len(fake.effects) == before

    fake.outage = False
    first = call()
    assert isinstance(first, Ok)
    assert len(fake.effects) == before + 1
    for _ in range(REPLAYS):
        assert call() == first
    assert len(fake.effects) == before + 1


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize("op", OPS)
def test_dry_run_has_no_effect_and_does_not_burn_the_key(op: str) -> None:
    fake, call = _case(op)
    before = len(fake.effects)
    # Re-issue the same call shape with dry_run=True through the real signature.
    dry = _dry(op, fake)
    assert isinstance(dry, Ok)
    assert dry.value.dry_run is True
    assert len(fake.effects) == before
    _assert_no_state_change(op, fake)

    # The key is free: a real call with the same key now performs the effect once.
    real = call()
    assert isinstance(real, Ok)
    assert real.value.dry_run is False
    assert len(fake.effects) == before + 1
    assert call() == real
    assert len(fake.effects) == before + 1


def _dry(op: str, fake: Any) -> Result[Any]:
    result: Result[Any] = _dry_call(op, fake)
    return result


def _dry_call(op: str, fake: Any) -> Any:
    if op == "authorize":
        return fake.authorize(
            idempotency_key="k1",
            payment_token=SANDBOX_APPROVED,
            amount=Money(5000, USD),
            order_id="O000001",
            dry_run=True,
        )
    if op in ("capture", "void"):
        return getattr(fake, op)(idempotency_key="k1", intent_id="pi_000001", dry_run=True)
    if op == "refund":
        return fake.refund(
            idempotency_key="k1", intent_id="pi_000001", amount=Money(1000, USD), dry_run=True
        )
    if op == "assess":
        return fake.assess(
            idempotency_key="k1", order_id="O000001", shopper_id="S0001", dry_run=True
        )
    return fake.create_shipment(
        idempotency_key="k1",
        order_id="O000001",
        region="US",
        postal_code="US-1234",
        dry_run=True,
    )


def _assert_no_state_change(op: str, fake: Any) -> None:
    if op == "authorize":
        status = fake.status("pi_000001")
        assert isinstance(status, ToolError)  # no intent was created
    elif op in ("capture", "void", "refund"):
        status = fake.status("pi_000001")
        assert isinstance(status, Ok)
        expected = {
            "capture": PaymentStatus.AUTHORIZED,
            "void": PaymentStatus.AUTHORIZED,
            "refund": PaymentStatus.CAPTURED,
        }[op]
        assert status.value.status is expected
        assert status.value.refunded_minor == 0
    elif op == "assess":
        found = fake.assessments_for("O000001")
        assert found == Ok(())
    else:
        # No shipment exists, so a real tracking number for the order is still unknown.
        from northwind_backend.carriers import tracking_number_for

        assert isinstance(fake.track(tracking_number_for("O000001")), ToolError)
