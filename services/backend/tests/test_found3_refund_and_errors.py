"""FOUND-3: refund cap, error categories on every port, access failed vs valid empty."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

from northwind_backend.carriers import tracking_number_for
from northwind_backend.errors import ErrorCategory, Ok, Result, ToolError
from northwind_backend.fakes.carrier import CarrierFake
from northwind_backend.fakes.fraud import FraudFake
from northwind_backend.fakes.payment import (
    DEFAULT_ORDER_LIMITS,
    DEFAULT_REFUND_CAPS,
    SANDBOX_APPROVED,
    SANDBOX_SOFT_DECLINE,
    PaymentFake,
    PaymentState,
)
from northwind_backend.ports import CarrierPort, FraudPort, Money, PaymentPort


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


ENVELOPE_KEYS = {"isError", "errorCategory", "isRetryable", "message"}


def _intent(fake: PaymentFake, currency: str, captured: int, authorized: int | None = None) -> str:
    """A captured intent. Keys are unique per call, so one fake can hold several."""
    n = len(fake.effects)
    auth = fake.authorize(
        idempotency_key=f"auth-{n}",
        payment_token=SANDBOX_APPROVED,
        amount=Money(authorized or captured, currency),
        order_id=f"O{n + 1:06d}",
        confirmation_ref=confirm(
            fake, f"O{n + 1:06d}", Money(authorized or captured, currency), f"auth-{n}"
        ),
    )
    assert isinstance(auth, Ok)
    intent_id = auth.value.intent_id
    cap = fake.capture(
        idempotency_key=f"cap-{n}",
        intent_id=intent_id,
        amount=Money(captured, currency),
        confirmation_ref=ref_of(fake, intent_id),
    )
    assert isinstance(cap, Ok)
    return intent_id


def _refund(fake: PaymentFake, intent_id: str, key: str, amount: int, cur: str) -> Result[Any]:
    return fake.refund(idempotency_key=key, intent_id=intent_id, amount=Money(amount, cur))


# ---- refund cap ---------------------------------------------------------------------------


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize("currency", sorted(DEFAULT_REFUND_CAPS))
def test_refund_at_the_cap_passes_and_one_minor_unit_over_is_refused(currency: str) -> None:
    cap = DEFAULT_REFUND_CAPS[currency]
    fake = PaymentFake()
    at = _intent(fake, currency, cap + 5000)
    effects = len(fake.effects)
    assert isinstance(_refund(fake, at, "r1", cap, currency), Ok)
    assert len(fake.effects) == effects + 1

    over = _intent(fake, currency, cap + 5000)
    effects = len(fake.effects)
    refused = _refund(fake, over, "r2", cap + 1, currency)
    assert isinstance(refused, ToolError)
    assert refused.error_category is ErrorCategory.BUSINESS
    assert not refused.is_retryable
    assert len(fake.effects) == effects
    status = fake.status(over)
    assert isinstance(status, Ok)
    assert status.value.refunded_minor == 0


@pytest.mark.req("FOUND-3")
def test_cumulative_split_refunds_cannot_pass_the_cap() -> None:
    cap = DEFAULT_REFUND_CAPS["USD"]
    fake = PaymentFake()
    intent_id = _intent(fake, "USD", cap + 10_000)
    assert isinstance(_refund(fake, intent_id, "r1", cap - 1000, "USD"), Ok)
    assert isinstance(_refund(fake, intent_id, "r2", 1000, "USD"), Ok)  # exactly at the cap
    effects = len(fake.effects)
    for n in range(10):  # splitting into single units gets nowhere either
        refused = _refund(fake, intent_id, f"r-more-{n}", 1, "USD")
        assert isinstance(refused, ToolError)
    assert len(fake.effects) == effects
    status = fake.status(intent_id)
    assert isinstance(status, Ok)
    assert status.value.refunded_minor == cap


@pytest.mark.req("FOUND-3")
def test_the_cap_is_set_per_currency_and_can_be_configured() -> None:
    fake = PaymentFake(refund_caps={"USD": 500, "EUR": 900})
    usd = _intent(fake, "USD", 2000)
    eur = _intent(fake, "EUR", 2000)
    assert isinstance(_refund(fake, usd, "a", 500, "USD"), Ok)
    assert isinstance(_refund(fake, usd, "b", 1, "USD"), ToolError)
    assert isinstance(_refund(fake, eur, "c", 900, "EUR"), Ok)
    assert isinstance(_refund(fake, eur, "d", 1, "EUR"), ToolError)
    # A USD cap says nothing about a EUR refund: each intent is judged on its own currency.
    eur2 = _intent(fake, "EUR", 2000)
    assert isinstance(_refund(fake, eur2, "e", 901, "EUR"), ToolError)


@pytest.mark.req("FOUND-3")
def test_a_currency_with_no_cap_is_refused() -> None:
    # JPY needs an order limit to be authorized at all (row #21), so the refund is what's refused.
    fake = PaymentFake(state=PaymentState(order_limits={**DEFAULT_ORDER_LIMITS, "JPY": 100_000}))
    assert "JPY" not in DEFAULT_REFUND_CAPS
    intent_id = _intent(fake, "JPY", 1000)
    effects = len(fake.effects)
    refused = _refund(fake, intent_id, "r1", 1, "JPY")
    assert isinstance(refused, ToolError)
    assert refused.error_category is ErrorCategory.BUSINESS
    assert len(fake.effects) == effects


@pytest.mark.req("FOUND-3")
def test_refund_above_what_was_captured_is_refused() -> None:
    fake = PaymentFake()
    # Authorized 10000, captured only 4000: well under the cap, so only "captured" can refuse it.
    intent_id = _intent(fake, "USD", captured=4000, authorized=10_000)
    effects = len(fake.effects)
    refused = _refund(fake, intent_id, "r1", 4001, "USD")
    assert isinstance(refused, ToolError)
    assert refused.error_category is ErrorCategory.BUSINESS
    assert len(fake.effects) == effects
    assert isinstance(_refund(fake, intent_id, "r2", 4000, "USD"), Ok)
    # Nothing left to refund.
    assert isinstance(_refund(fake, intent_id, "r3", 1, "USD"), ToolError)


@pytest.mark.req("FOUND-3")
def test_refund_of_an_uncaptured_payment_is_refused() -> None:
    fake = PaymentFake()
    auth = fake.authorize(
        idempotency_key="a",
        payment_token=SANDBOX_APPROVED,
        amount=Money(1000, "USD"),
        order_id="O000001",
        confirmation_ref=confirm(fake, "O000001", Money(1000, "USD"), "a"),
    )
    assert isinstance(auth, Ok)
    assert isinstance(_refund(fake, auth.value.intent_id, "r", 100, "USD"), ToolError)


# ---- error categories on every port -------------------------------------------------------


def _payment_trigger(category: str) -> Callable[[], Result[Any]]:
    if category == "transient":
        fake = PaymentFake()
        fake.outage = True
        return lambda: fake.status("pi_000001")
    if category == "validation":
        fake = PaymentFake()
        return lambda: fake.authorize(
            idempotency_key="k",
            payment_token="not a token",  # noqa: S106
            amount=Money(100, "USD"),
            order_id="O000001",
            confirmation_ref=confirm(fake, "O000001", Money(100, "USD"), "k"),
        )
    if category == "business":
        fake = PaymentFake()
        return lambda: fake.authorize(
            idempotency_key="k",
            payment_token=SANDBOX_SOFT_DECLINE,
            amount=Money(100, "USD"),
            order_id="O000001",
            confirmation_ref=confirm(fake, "O000001", Money(100, "USD"), "k"),
        )
    fake = PaymentFake(scopes=frozenset())
    return lambda: fake.status("pi_000001")


def _fraud_trigger(category: str) -> Callable[[], Result[Any]]:
    if category == "transient":
        fraud = FraudFake()
        fraud.outage = True
        return lambda: fraud.assessments_for("O000001")
    if category == "validation":
        fraud = FraudFake()
        return lambda: fraud.assess(idempotency_key="k", order_id="bad", shopper_id="S0001")
    if category == "business":
        fraud = FraudFake()
        assert isinstance(
            fraud.assess(idempotency_key="k1", order_id="O000001", shopper_id="S0001"), Ok
        )
        return lambda: fraud.assess(idempotency_key="k2", order_id="O000001", shopper_id="S0001")
    fraud = FraudFake(scopes=frozenset())
    return lambda: fraud.assess(idempotency_key="k", order_id="O000001", shopper_id="S0001")


def _carrier_trigger(category: str) -> Callable[[], Result[Any]]:
    if category == "transient":
        carrier = CarrierFake()
        carrier.outage = True
        return lambda: carrier.track("NLP-AAAAAAAA")
    if category == "validation":
        carrier = CarrierFake()
        return lambda: carrier.create_shipment(
            idempotency_key="k", order_id="O000001", region="MARS", postal_code="US-1234"
        )
    if category == "business":
        carrier = CarrierFake()
        return lambda: carrier.create_shipment(
            idempotency_key="k", order_id="O000001", region="US", postal_code="XX-1234"
        )
    carrier = CarrierFake(scopes=frozenset())
    return lambda: carrier.track("NLP-AAAAAAAA")


TRIGGERS = {"payment": _payment_trigger, "fraud": _fraud_trigger, "carrier": _carrier_trigger}


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize("category", ["transient", "validation", "business", "permission"])
@pytest.mark.parametrize("port", sorted(TRIGGERS))
def test_every_error_category_on_every_port(port: str, category: str) -> None:
    result = TRIGGERS[port](category)()
    assert isinstance(result, ToolError)
    assert result.is_error is True
    assert result.error_category is ErrorCategory(category)
    assert result.is_retryable is (category == "transient")
    envelope = result.to_envelope()
    assert ENVELOPE_KEYS <= set(envelope)
    assert envelope["isError"] is True
    assert envelope["errorCategory"] == category
    assert envelope["isRetryable"] is (category == "transient")
    assert isinstance(envelope["message"], str)
    assert envelope["message"]


@pytest.mark.req("FOUND-3")
def test_a_successful_result_is_not_flagged_as_an_error() -> None:
    result = FraudFake().assessments_for("O000001")
    assert isinstance(result, Ok)
    assert result.to_envelope()["isError"] is False
    assert "errorCategory" not in result.to_envelope()


# ---- access failed vs valid empty ---------------------------------------------------------


@pytest.mark.req("FOUND-3")
def test_assessments_for_separates_valid_empty_from_an_outage() -> None:
    fraud = FraudFake()
    empty = fraud.assessments_for("O000001")
    assert isinstance(empty, Ok)
    assert not empty.value
    assert empty.is_error is False

    fraud.outage = True
    down = fraud.assessments_for("O000001")
    assert isinstance(down, ToolError)
    assert down.error_category is ErrorCategory.TRANSIENT
    assert down.is_retryable

    fraud.outage = False
    assert fraud.assessments_for("O000001") == empty  # the outage left nothing behind


@pytest.mark.req("FOUND-3")
def test_track_separates_valid_empty_outage_and_unknown_number() -> None:
    carrier = CarrierFake()
    shipment = carrier.create_shipment(
        idempotency_key="k", order_id="O000001", region="US", postal_code="US-1234"
    )
    assert isinstance(shipment, Ok)
    number = shipment.value.tracking_number
    assert number == tracking_number_for("O000001")

    empty = carrier.track(number)
    assert isinstance(empty, Ok)
    assert empty.value.events == ()

    unknown = carrier.track("NLP-ZZZZZZZZ")
    assert isinstance(unknown, ToolError)
    assert unknown.error_category is ErrorCategory.VALIDATION
    assert not unknown.is_retryable

    carrier.outage = True
    down = carrier.track(number)
    assert isinstance(down, ToolError)
    assert down.error_category is ErrorCategory.TRANSIENT
    assert down.is_retryable

    # Three different outcomes.
    assert len({empty.is_error, unknown.error_category, down.error_category}) == 3


# ---- Protocols ----------------------------------------------------------------------------


@pytest.mark.req("FOUND-3")
def test_fakes_satisfy_the_runtime_checkable_ports() -> None:
    assert isinstance(PaymentFake(), PaymentPort)
    assert isinstance(FraudFake(), FraudPort)
    assert isinstance(CarrierFake(), CarrierPort)
    assert not isinstance(FraudFake(), PaymentPort)
    assert not isinstance(PaymentFake(), CarrierPort)


@pytest.mark.req("FOUND-3")
def test_payment_signatures_have_no_card_parameter() -> None:
    banned = ("card", "pan", "cvv", "cvc", "expiry", "exp_", "number")
    for owner in (PaymentPort, PaymentFake):
        for name in ("authorize", "capture", "void", "refund", "status"):
            params = inspect.signature(getattr(owner, name)).parameters
            for param in params:
                assert not any(b in param.lower() for b in banned), (owner, name, param)
    authorize = inspect.signature(PaymentPort.authorize).parameters
    assert "payment_token" in authorize
