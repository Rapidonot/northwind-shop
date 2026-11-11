"""FOUND-3: card data never enters the backend ports (hard rule 1, PAY-05)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from northwind_backend.errors import ErrorCategory, Ok, Result, ToolError
from northwind_backend.fakes._base import MAX_AMOUNT_MINOR
from northwind_backend.fakes.carrier import CarrierFake
from northwind_backend.fakes.fraud import FraudFake
from northwind_backend.fakes.payment import SANDBOX_APPROVED, PaymentFake
from northwind_backend.ports import Money


def _payment_state(port: Any) -> Any:
    while not hasattr(port, "state"):
        port = port._fake
    return port.state


def confirm(port: Any, order_id: Any, amount: Any, key: Any = None) -> str:
    """The portal confirmation a test's `authorize` presents (row #21, hard rule 9). One per
    (key, order, amount), so a same-key replay presents the same reference."""
    state = _payment_state(port)

    def memo() -> str:
        refs = state.__dict__.setdefault("_test_refs", {})
        slot = repr((key, order_id, amount))
        if slot not in refs:
            refs[slot] = state.confirmations.issue(order_id, amount)
        return str(refs[slot])

    # Under the state's own lock: concurrent replays must present one reference.
    return str(state.store.exclusive(memo))


def ref_of(port: Any, intent_id: Any) -> str:
    """The reference that authorized this intent; a well-formed, never-issued one otherwise."""
    found = _payment_state(port).confirmation_of(intent_id) if isinstance(intent_id, str) else None
    return found or "cf_ZZZZZZZZZZZZZZZZ"


# Well-known sandbox test numbers only.
PANS = [
    "4111111111111111",
    "4111 1111 1111 1111",
    "4111-1111-1111-1111",
    "4242424242424242",
    "4242 4242 4242 4242",
    "5555-5555-5555-4444",
    "378282246310005",  # 15 digits
    "3782 822463 10005",
]
NON_LUHN = ["1234567890123456", "1234 5678 9012 3456", "1234-5678-9012-3456"]
BAD_VALUES = PANS + NON_LUHN


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _assert_clean_refusal(result: Result[Any], bad: str, effects: int, fake: Any) -> None:
    assert isinstance(result, ToolError), result
    assert result.error_category is ErrorCategory.VALIDATION
    assert not result.is_retryable
    shown = repr(result) + str(result.to_envelope()) + result.message + str(result)
    assert bad not in shown
    assert _digits(bad) not in _digits(shown)
    assert len(fake.effects) == effects


def _auth_ok(fake: PaymentFake) -> str:
    result = fake.authorize(
        idempotency_key="seed",
        payment_token=SANDBOX_APPROVED,
        amount=Money(5000, "USD"),
        order_id="O000001",
        confirmation_ref=confirm(fake, "O000001", Money(5000, "USD"), "seed"),
    )
    assert isinstance(result, Ok)
    return result.value.intent_id


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize("bad", BAD_VALUES)
class TestCardLookingValuesAreRefused:
    def test_as_token(self, bad: str) -> None:
        fake = PaymentFake()
        result = fake.authorize(
            idempotency_key="k",
            payment_token=bad,
            amount=Money(100, "USD"),
            order_id="O000001",
            confirmation_ref=confirm(fake, "O000001", Money(100, "USD"), "k"),
        )
        _assert_clean_refusal(result, bad, 0, fake)

    def test_as_idempotency_key_on_every_state_changing_call(self, bad: str) -> None:
        pay = PaymentFake()
        intent_id = _auth_ok(pay)
        n = len(pay.effects)
        calls: list[Callable[[], Result[Any]]] = [
            lambda: pay.authorize(
                idempotency_key=bad,
                payment_token=SANDBOX_APPROVED,
                amount=Money(100, "USD"),
                order_id="O000002",
                confirmation_ref=confirm(pay, "O000002", Money(100, "USD"), bad),
            ),
            lambda: pay.capture(
                idempotency_key=bad, intent_id=intent_id, confirmation_ref=ref_of(pay, intent_id)
            ),
            lambda: pay.void(idempotency_key=bad, intent_id=intent_id),
            lambda: pay.refund(idempotency_key=bad, intent_id=intent_id, amount=Money(1, "USD")),
        ]
        for call in calls:
            _assert_clean_refusal(call(), bad, n, pay)
        fraud = FraudFake()
        _assert_clean_refusal(
            fraud.assess(idempotency_key=bad, order_id="O000001", shopper_id="S0001"),
            bad,
            0,
            fraud,
        )
        carrier = CarrierFake()
        _assert_clean_refusal(
            carrier.create_shipment(
                idempotency_key=bad, order_id="O000001", region="US", postal_code="US-1234"
            ),
            bad,
            0,
            carrier,
        )

    def test_as_order_id(self, bad: str) -> None:
        pay = PaymentFake()
        _assert_clean_refusal(
            pay.authorize(
                idempotency_key="k",
                payment_token=SANDBOX_APPROVED,
                amount=Money(100, "USD"),
                order_id=bad,
                confirmation_ref=confirm(pay, bad, Money(100, "USD"), "k"),
            ),
            bad,
            0,
            pay,
        )
        fraud = FraudFake()
        _assert_clean_refusal(
            fraud.assess(idempotency_key="k", order_id=bad, shopper_id="S0001"), bad, 0, fraud
        )
        _assert_clean_refusal(fraud.assessments_for(bad), bad, 0, fraud)
        carrier = CarrierFake()
        _assert_clean_refusal(
            carrier.create_shipment(
                idempotency_key="k", order_id=bad, region="US", postal_code="US-1234"
            ),
            bad,
            0,
            carrier,
        )

    def test_as_intent_id(self, bad: str) -> None:
        pay = PaymentFake()
        intent_id = _auth_ok(pay)
        n = len(pay.effects)
        _assert_clean_refusal(
            pay.capture(idempotency_key="k1", intent_id=bad, confirmation_ref=ref_of(pay, bad)),
            bad,
            n,
            pay,
        )
        _assert_clean_refusal(pay.void(idempotency_key="k2", intent_id=bad), bad, n, pay)
        _assert_clean_refusal(
            pay.refund(idempotency_key="k3", intent_id=bad, amount=Money(1, "USD")), bad, n, pay
        )
        _assert_clean_refusal(pay.status(bad), bad, n, pay)
        # The good intent is untouched.
        status = pay.status(intent_id)
        assert isinstance(status, Ok)
        assert status.value.captured_minor == 0

    def test_as_other_string_fields(self, bad: str) -> None:
        carrier = CarrierFake()
        _assert_clean_refusal(
            carrier.create_shipment(
                idempotency_key="k", order_id="O000001", region="US", postal_code=bad
            ),
            bad,
            0,
            carrier,
        )
        _assert_clean_refusal(
            carrier.create_shipment(
                idempotency_key="k", order_id="O000001", region=bad, postal_code="US-7099"
            ),
            bad,
            0,
            carrier,
        )
        _assert_clean_refusal(
            carrier.create_shipment(
                idempotency_key="k",
                order_id="O000001",
                region="US",
                postal_code="US-7099",
                service=bad,
            ),
            bad,
            0,
            carrier,
        )
        _assert_clean_refusal(carrier.track(bad), bad, 0, carrier)
        fraud = FraudFake()
        _assert_clean_refusal(
            fraud.assess(idempotency_key="k", order_id="O000001", shopper_id=bad), bad, 0, fraud
        )


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize("bad", PANS + NON_LUHN)
def test_a_refused_card_number_leaves_no_trace_in_later_results(bad: str) -> None:
    """Refused, then a good call: nothing of the number is in anything the fake returns."""
    fake = PaymentFake()
    fake.authorize(
        idempotency_key=bad,
        payment_token=bad,
        amount=Money(100, "USD"),
        order_id=bad,
        confirmation_ref=confirm(fake, bad, Money(100, "USD"), bad),
    )
    good = fake.authorize(
        idempotency_key="good",
        payment_token=SANDBOX_APPROVED,
        amount=Money(100, "USD"),
        order_id="O000001",
        confirmation_ref=confirm(fake, "O000001", Money(100, "USD"), "good"),
    )
    assert isinstance(good, Ok)
    dump = repr(fake.effects) + repr(good) + repr(fake.status(good.value.intent_id))
    assert _digits(bad) not in dump
    assert len(fake.effects) == 1


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize("amount", [4111111111111111, 4242424242424242, 1234567890123456])
def test_an_amount_that_looks_like_a_card_number_is_refused(amount: int) -> None:
    assert amount > MAX_AMOUNT_MINOR
    pay = PaymentFake()
    result = pay.authorize(
        idempotency_key="k",
        payment_token=SANDBOX_APPROVED,
        amount=Money(amount, "USD"),
        order_id="O000001",
        confirmation_ref=confirm(pay, "O000001", Money(amount, "USD"), "k"),
    )
    assert isinstance(result, ToolError)
    assert result.error_category is ErrorCategory.VALIDATION
    assert str(amount) not in repr(result) + str(result.to_envelope())
    assert pay.effects == ()

    intent_id = _auth_ok(pay)
    n = len(pay.effects)
    for refused in (
        pay.capture(
            idempotency_key="c",
            intent_id=intent_id,
            amount=Money(amount, "USD"),
            confirmation_ref=ref_of(pay, intent_id),
        ),
        pay.refund(idempotency_key="r", intent_id=intent_id, amount=Money(amount, "USD")),
    ):
        assert isinstance(refused, ToolError)
        assert str(amount) not in repr(refused)
    assert len(pay.effects) == n
