"""FOUND-3: tests from the security-reviewer findings.

Some of these failed before their fix. The others pin behaviour a fix relies on and passed from the
start, so they were proved by removing the guard instead. The evidence file says which is which.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest

from northwind_backend.carriers import tracking_number_for
from northwind_backend.errors import Ok, Result, ToolError
from northwind_backend.fakes.carrier import CARRIER_SCOPES, CarrierFake
from northwind_backend.fakes.fraud import FRAUD_SCOPES, FraudFake
from northwind_backend.fakes.payment import (
    PAYMENT_SCOPES,
    SANDBOX_APPROVED,
    SANDBOX_SOFT_DECLINE,
    PaymentFake,
)
from northwind_backend.idempotency import IdempotencyStore
from northwind_backend.ports import FraudDecision, Money, TrackingEvent

USD = Money(5_000, "USD")
FULLWIDTH_ORDER = "O００００02"  # the same order as O000002 to a reader, not to a dict


def _code(result: Result[Any]) -> str:
    assert isinstance(result, ToolError), result
    return result.code


def _authorize(fake: PaymentFake, key: str, order_id: str = "O000001") -> Result[Any]:
    return fake.authorize(
        idempotency_key=key, payment_token=SANDBOX_APPROVED, amount=USD, order_id=order_id
    )


@pytest.mark.req("FOUND-3")
def test_non_ascii_digits_cannot_alias_a_flagged_order() -> None:
    fake = FraudFake(flagged_order_ids=["O000002"])
    alias = fake.assess(idempotency_key="k", order_id=FULLWIDTH_ORDER, shopper_id="S0001")
    assert _code(alias) == "invalid_order_id"
    real = fake.assess(idempotency_key="k2", order_id="O000002", shopper_id="S0001")
    assert isinstance(real, Ok)
    assert real.value.decision is FraudDecision.REVIEW


@pytest.mark.req("FOUND-3")
def test_non_ascii_digits_cannot_ship_an_order_twice() -> None:
    fake = CarrierFake()
    args: dict[str, Any] = {"region": "US", "postal_code": "US-1234"}
    assert isinstance(fake.create_shipment(idempotency_key="a", order_id="O000002", **args), Ok)
    alias = fake.create_shipment(idempotency_key="b", order_id=FULLWIDTH_ORDER, **args)
    assert _code(alias) == "invalid_order_id"
    assert len(fake.effects) == 1


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize(
    ("call", "code"),
    [
        (lambda: PaymentFake().status("pi_００００01"), "invalid_intent"),
        (lambda: FraudFake().assessments_for("O００００01"), "invalid_order_id"),
        (
            lambda: FraudFake().assess(
                idempotency_key="k", order_id="O000001", shopper_id="S００01"
            ),
            "invalid_shopper_id",
        ),
        (
            lambda: CarrierFake().create_shipment(
                idempotency_key="k", order_id="O000001", region="US", postal_code="US-１２３４"
            ),
            "invalid_postal_code",
        ),
        (
            lambda: PaymentFake().authorize(
                idempotency_key="k",
                payment_token=f"{SANDBOX_APPROVED}_００１",
                amount=USD,
                order_id="O000001",
            ),
            "invalid_token",
        ),
    ],
)
def test_every_id_pattern_takes_ascii_digits_only(
    call: Callable[[], Result[Any]], code: str
) -> None:
    assert _code(call()) == code


@pytest.mark.req("FOUND-3")
def test_a_second_key_cannot_authorize_an_order_again() -> None:
    fake = PaymentFake()
    first = _authorize(fake, "k1")
    assert isinstance(first, Ok)
    assert _code(_authorize(fake, "k2")) == "order_already_authorized"
    assert len(fake.effects) == 1


@pytest.mark.req("FOUND-3")
def test_a_lost_key_cannot_charge_an_order_twice() -> None:
    fake = PaymentFake()
    first = _authorize(fake, "k1")
    assert isinstance(first, Ok)
    assert isinstance(fake.capture(idempotency_key="c1", intent_id=first.value.intent_id), Ok)
    assert _code(_authorize(fake, "k2")) == "order_already_authorized"
    assert sum(e.amount_minor for e in fake.effects if e.operation == "capture") == USD.amount_minor


@pytest.mark.req("FOUND-3")
def test_a_dry_run_also_reports_an_order_already_authorized() -> None:
    fake = PaymentFake()
    assert isinstance(_authorize(fake, "k1"), Ok)
    dry = fake.authorize(
        idempotency_key="k2",
        payment_token=SANDBOX_APPROVED,
        amount=USD,
        order_id="O000001",
        dry_run=True,
    )
    assert _code(dry) == "order_already_authorized"


@pytest.mark.req("FOUND-3")
def test_a_voided_authorization_frees_the_order() -> None:
    fake = PaymentFake()
    first = _authorize(fake, "k1")
    assert isinstance(first, Ok)
    assert isinstance(fake.void(idempotency_key="v", intent_id=first.value.intent_id), Ok)
    assert isinstance(_authorize(fake, "k2"), Ok)


@pytest.mark.req("FOUND-3")
def test_a_declined_attempt_does_not_hold_the_order() -> None:
    fake = PaymentFake()
    declined = fake.authorize(
        idempotency_key="k1",
        payment_token=SANDBOX_SOFT_DECLINE,
        amount=USD,
        order_id="O000001",
    )
    assert _code(declined) == "soft_decline"
    assert isinstance(_authorize(fake, "k2"), Ok)


@pytest.mark.req("FOUND-3")
def test_the_refund_cap_holds_per_order_across_a_voided_attempt() -> None:
    fake = PaymentFake(refund_caps={"USD": 3_000})
    first = _authorize(fake, "k1")
    assert isinstance(first, Ok)
    assert isinstance(fake.void(idempotency_key="v", intent_id=first.value.intent_id), Ok)
    second = _authorize(fake, "k2")
    assert isinstance(second, Ok)
    intent_id = second.value.intent_id
    assert isinstance(fake.capture(idempotency_key="c", intent_id=intent_id), Ok)
    assert isinstance(
        fake.refund(idempotency_key="r1", intent_id=intent_id, amount=Money(3_000, "USD")), Ok
    )
    over = fake.refund(idempotency_key="r2", intent_id=intent_id, amount=Money(1, "USD"))
    assert _code(over) == "refund_over_cap"


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize("refunded", [1_000, 3_000], ids=["partly", "up-to-the-cap"])
def test_a_new_intent_cannot_reset_the_refund_cap(refunded: int) -> None:
    fake = PaymentFake(refund_caps={"USD": 3_000})
    first = _authorize(fake, "k1")
    assert isinstance(first, Ok)
    intent_id = first.value.intent_id
    assert isinstance(fake.capture(idempotency_key="c", intent_id=intent_id), Ok)
    assert isinstance(
        fake.refund(idempotency_key="r", intent_id=intent_id, amount=Money(refunded, "USD")), Ok
    )
    assert _code(_authorize(fake, "k2")) == "order_already_authorized"


@pytest.mark.req("FOUND-3")
def test_a_fully_refunded_order_cannot_be_authorized_again() -> None:
    fake = PaymentFake()
    first = _authorize(fake, "k1")
    assert isinstance(first, Ok)
    intent_id = first.value.intent_id
    assert isinstance(fake.capture(idempotency_key="c", intent_id=intent_id), Ok)
    assert isinstance(fake.refund(idempotency_key="r", intent_id=intent_id, amount=USD), Ok)
    assert _code(_authorize(fake, "k2")) == "order_already_authorized"


@pytest.mark.req("FOUND-3")
def test_a_dry_run_waits_for_an_effect_in_progress() -> None:
    """A dry run reads what a real call is changing, so it takes the same lock."""
    store = IdempotencyStore()
    entered, release = threading.Event(), threading.Event()
    order: list[str] = []

    def slow_effect() -> Result[str]:
        entered.set()
        release.wait(5)
        order.append("effect")
        return Ok("done")

    def dry() -> Result[str]:
        order.append("dry")
        return Ok("dry")

    real = threading.Thread(target=store.run, args=("k", "h", slow_effect))
    real.start()
    assert entered.wait(5)
    reader = threading.Thread(target=store.exclusive, args=(dry,))
    reader.start()
    reader.join(0.1)
    release.set()
    real.join(5)
    reader.join(5)
    assert order == ["effect", "dry"]


class _SlowPayment(PaymentFake):
    """Pauses a real authorize inside its effect, after the store lock is taken."""

    def __init__(self) -> None:
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def _has_live_intent(self, order_id: str) -> bool:
        if threading.current_thread().name == "real":
            self.entered.set()
            self.release.wait(5)
        return super()._has_live_intent(order_id)


@pytest.mark.req("FOUND-3")
def test_a_fakes_dry_run_waits_for_a_real_call_and_then_sees_its_result() -> None:
    fake = _SlowPayment()
    dry_results: list[Result[Any]] = []

    def dry() -> None:
        dry_results.append(
            fake.authorize(
                idempotency_key="k2",
                payment_token=SANDBOX_APPROVED,
                amount=USD,
                order_id="O000001",
                dry_run=True,
            )
        )

    real = threading.Thread(target=_authorize, args=(fake, "k1"), name="real")
    real.start()
    assert fake.entered.wait(5)
    reader = threading.Thread(target=dry, name="dry")
    reader.start()
    reader.join(0.1)
    waited = reader.is_alive()
    fake.release.set()
    real.join(5)
    reader.join(5)
    assert waited
    assert _code(dry_results[0]) == "order_already_authorized"


@pytest.mark.req("FOUND-3")
def test_a_refused_second_authorization_does_not_promise_a_void_will_work() -> None:
    fake = PaymentFake()
    first = _authorize(fake, "k1")
    assert isinstance(first, Ok)
    assert isinstance(fake.capture(idempotency_key="c", intent_id=first.value.intent_id), Ok)
    refused = _authorize(fake, "k2")
    assert isinstance(refused, ToolError)
    assert "void" not in refused.message.lower()


@pytest.mark.req("FOUND-3")
def test_a_refund_only_payment_credential_cannot_capture_or_void() -> None:
    fake = PaymentFake(scopes=frozenset({"refund"}))
    assert _code(fake.capture(idempotency_key="c", intent_id="pi_000001")) == "not_permitted"
    assert _code(fake.void(idempotency_key="v", intent_id="pi_000001")) == "not_permitted"
    assert _code(_authorize(fake, "a")) == "not_permitted"
    assert _code(fake.status("pi_000001")) == "not_permitted"
    # Past the scope check: refused for what it is, not for who asked.
    allowed = fake.refund(idempotency_key="r", intent_id="pi_000001", amount=USD)
    assert _code(allowed) == "unknown_intent"
    assert PAYMENT_SCOPES > {"refund"}


@pytest.mark.req("FOUND-3")
def test_a_read_only_fraud_credential_cannot_assess() -> None:
    fake = FraudFake(scopes=frozenset({"read"}))
    denied = fake.assess(idempotency_key="k", order_id="O000001", shopper_id="S0001")
    assert _code(denied) == "not_permitted"
    assert fake.assessments_for("O000001") == Ok(())
    assert FRAUD_SCOPES > {"read"}


@pytest.mark.req("FOUND-3")
def test_a_tracking_only_carrier_credential_cannot_ship() -> None:
    fake = CarrierFake(scopes=frozenset({"track"}))
    denied = fake.create_shipment(
        idempotency_key="k", order_id="O000001", region="US", postal_code="US-1234"
    )
    assert _code(denied) == "not_permitted"
    assert _code(fake.track(tracking_number_for("O000001"))) == "unknown_tracking_number"
    assert CARRIER_SCOPES > {"track"}


@pytest.mark.req("FOUND-3")
def test_track_refuses_an_unknown_carrier_prefix_without_raising() -> None:
    event = TrackingEvent("2026-10-02", "picked_up")
    fake = CarrierFake(tracking={"ABC-AAAAAAAA": (event,)})
    assert _code(fake.track("ABC-AAAAAAAA")) == "invalid_tracking_number"
