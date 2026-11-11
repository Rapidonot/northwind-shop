"""Row #21's prerequisites before any agent calls `authorize` or `capture` (hard rule 9; FOUND-3
open mediums 1 and 2; decision 0014).

P1: a confirmation reference from the portal control (a test fixture until CHK-02), with the rule
the user stated on 2026-11-11, one test per sentence. P2: a deterministic per-order limit. P3:
scopes, the one-payment rule, idempotency and the refund cap hold across instances on one state."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.errors import Ok, Result, ToolError
from northwind_backend.fakes.fraud import FraudFake, FraudState
from northwind_backend.fakes.payment import (
    DEFAULT_ORDER_LIMITS,
    SANDBOX_APPROVED,
    SANDBOX_SOFT_DECLINE,
    PaymentFake,
    PaymentState,
)
from northwind_backend.ports import Money, PaymentStatus

pytestmark = [pytest.mark.req("BE-05"), pytest.mark.req("CHK-05")]

SRC = Path(__file__).resolve().parents[1] / "src"
ORDER = "O000001"
USD = Money(12_345, "USD")


def _code(result: Result[Any]) -> str:
    assert isinstance(result, ToolError), result
    return result.code


def _authorize(
    fake: PaymentFake,
    ref: str,
    *,
    key: str = "a1",
    order_id: str = ORDER,
    amount: Money = USD,
    token: str = SANDBOX_APPROVED,
    dry_run: bool = False,
) -> Result[Any]:
    return fake.authorize(
        idempotency_key=key,
        payment_token=token,
        amount=amount,
        order_id=order_id,
        confirmation_ref=ref,
        dry_run=dry_run,
    )


def _capture(
    fake: PaymentFake, intent_id: str, ref: str, key: str = "c1", **kw: Any
) -> Result[Any]:
    return fake.capture(idempotency_key=key, intent_id=intent_id, confirmation_ref=ref, **kw)


def _authorized(fake: PaymentFake) -> tuple[str, str]:
    ref = fake.state.confirmations.issue(ORDER, USD)
    result = _authorize(fake, ref)
    assert isinstance(result, Ok)
    return result.value.intent_id, ref


# ---- P1: the confirmation reference --------------------------------------------------------


@pytest.mark.parametrize("ref", ["", "cf_short", "cf_abcdefghijklmnop", "4111111111111111", None])
def test_authorize_without_a_well_formed_reference_is_refused(ref: Any) -> None:
    fake = PaymentFake()
    assert _code(_authorize(fake, ref)) in ("confirmation_required", "card_data_refused")
    assert fake.effects == ()


def test_capture_without_a_well_formed_reference_is_refused() -> None:
    fake = PaymentFake()
    intent_id, _ = _authorized(fake)
    assert _code(_capture(fake, intent_id, "")) == "confirmation_required"
    assert [e.operation for e in fake.effects] == ["authorize"]


def test_a_reference_never_issued_is_refused() -> None:
    fake = PaymentFake()
    assert _code(_authorize(fake, "cf_ABCDEFGHIJKLMNOP")) == "confirmation_mismatch"
    assert fake.effects == ()


@pytest.mark.parametrize(
    ("order_id", "amount"),
    [
        ("O000002", USD),  # another order
        (ORDER, Money(12_344, "USD")),  # less than confirmed
        (ORDER, Money(12_346, "USD")),  # more than confirmed
        (ORDER, Money(12_345, "CAD")),  # another currency
    ],
)
def test_authorize_needs_the_confirmed_order_currency_and_exact_amount(
    order_id: str, amount: Money
) -> None:
    fake = PaymentFake()
    ref = fake.state.confirmations.issue(ORDER, USD)
    assert _code(_authorize(fake, ref, order_id=order_id, amount=amount)) == (
        "confirmation_mismatch"
    )
    assert fake.effects == ()
    # The reference is still unused: the confirmed call goes through.
    assert isinstance(_authorize(fake, ref, key="a2"), Ok)


def test_authorize_uses_the_reference_up() -> None:
    """Sentence 1: `authorize` uses the reference up."""
    fake = PaymentFake()
    intent_id, ref = _authorized(fake)
    assert isinstance(fake.void(idempotency_key="v1", intent_id=intent_id), Ok)
    # A new key, the same used reference: refused, though the order has no live intent now.
    assert _code(_authorize(fake, ref, key="a2")) == "confirmation_used"
    assert [e.operation for e in fake.effects] == ["authorize", "void"]


def test_a_same_key_replay_returns_the_first_result_and_uses_nothing_more() -> None:
    fake = PaymentFake()
    ref = fake.state.confirmations.issue(ORDER, USD)
    first = _authorize(fake, ref)
    assert _authorize(fake, ref) == first
    assert len(fake.effects) == 1


def test_capture_presents_the_reference_that_authorized_the_intent() -> None:
    """Sentence 2: accepted although it is used."""
    fake = PaymentFake()
    intent_id, ref = _authorized(fake)
    captured = _capture(fake, intent_id, ref)
    assert isinstance(captured, Ok) and captured.value.status is PaymentStatus.CAPTURED
    assert captured.value.captured_minor == USD.amount_minor


def test_capture_refuses_a_fresh_unused_reference_for_the_same_order() -> None:
    """Sentence 3: any other reference is refused, a fresh unused one for the same order
    included."""
    fake = PaymentFake()
    intent_id, _ = _authorized(fake)
    fresh = fake.state.confirmations.issue(ORDER, USD)
    assert _code(_capture(fake, intent_id, fresh)) == "confirmation_mismatch"
    assert [e.operation for e in fake.effects] == ["authorize"]


def test_capture_refuses_another_orders_reference() -> None:
    fake = PaymentFake()
    intent_id, _ = _authorized(fake)
    other_ref = fake.state.confirmations.issue("O000002", USD)
    assert isinstance(_authorize(fake, other_ref, key="b1", order_id="O000002"), Ok)
    assert _code(_capture(fake, intent_id, other_ref)) == "confirmation_mismatch"
    never_issued = _capture(fake, intent_id, "cf_ABCDEFGHIJKLMNOP", key="c2")
    assert _code(never_issued) == "confirmation_mismatch"


def test_after_a_void_a_new_authorization_needs_a_new_confirmation() -> None:
    """Sentence 4."""
    fake = PaymentFake()
    intent_id, ref = _authorized(fake)
    assert isinstance(fake.void(idempotency_key="v1", intent_id=intent_id), Ok)
    assert _code(_authorize(fake, ref, key="a2")) == "confirmation_used"
    fresh = fake.state.confirmations.issue(ORDER, USD)
    second = _authorize(fake, fresh, key="a3")
    assert isinstance(second, Ok) and second.value.intent_id != intent_id
    # The voided intent's reference can't capture the new intent, nor the new one the old.
    assert _code(_capture(fake, second.value.intent_id, ref)) == "confirmation_mismatch"
    assert _code(_capture(fake, intent_id, fresh, key="c2")) == "intent_voided"
    assert isinstance(_capture(fake, second.value.intent_id, fresh, key="c3"), Ok)


def test_a_soft_decline_uses_nothing_up() -> None:
    fake = PaymentFake()
    ref = fake.state.confirmations.issue(ORDER, USD)
    assert _code(_authorize(fake, ref, token=SANDBOX_SOFT_DECLINE)) == "soft_decline"
    assert isinstance(_authorize(fake, ref, key="a2"), Ok)


def test_a_dry_run_checks_the_reference_and_uses_nothing() -> None:
    fake = PaymentFake()
    ref = fake.state.confirmations.issue(ORDER, USD)
    assert _code(_authorize(fake, "cf_ABCDEFGHIJKLMNOP", dry_run=True)) == "confirmation_mismatch"
    dry = _authorize(fake, ref, dry_run=True)
    assert isinstance(dry, Ok) and dry.value.dry_run
    assert fake.effects == ()
    assert isinstance(_authorize(fake, ref), Ok)


def test_nothing_under_src_issues_a_confirmation() -> None:
    """Until CHK-02, only a test fixture stands in for the portal control: no agent, view or port
    issues one. `confirmations.py` may define `issue`; nothing under `src` may call it."""
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "issue":
                found.append(f"{path.relative_to(SRC)}:{node.lineno}")
            if isinstance(node, ast.Name) and node.id == "issue":
                found.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert found == []


def test_the_reference_is_written_to_the_audit_as_type_and_length_only() -> None:
    from northwind_backend.audit import AuditContext, InMemoryAuditSink
    from northwind_backend.views import AuditedPaymentPort

    fake = PaymentFake()
    ref = fake.state.confirmations.issue(ORDER, USD)
    sink = InMemoryAuditSink()
    audit = AuditContext(sink, correlation_id="corr-p1", actor="test")
    view = AuditedPaymentPort(fake, audit, allowed=frozenset({"authorize"}))
    view.authorize(
        idempotency_key="a1",
        payment_token=SANDBOX_APPROVED,
        amount=USD,
        order_id=ORDER,
        confirmation_ref=ref,
    )
    for event in sink.read_all():
        assert ref not in repr(event.to_dict())
        assert event.inputs["confirmation_ref"] == {"type": "str", "length": len(ref)}


# ---- P2: the per-order limit ---------------------------------------------------------------


@pytest.mark.parametrize("currency", sorted(DEFAULT_ORDER_LIMITS))
def test_authorize_above_the_order_limit_is_refused_and_at_it_allowed(currency: str) -> None:
    limit = DEFAULT_ORDER_LIMITS[currency]
    fake = PaymentFake()
    over = Money(limit + 1, currency)
    ref = fake.state.confirmations.issue(ORDER, over)
    refused = _authorize(fake, ref, amount=over)
    assert _code(refused) == "over_order_limit"
    assert isinstance(refused, ToolError) and refused.error_category.value == "business"
    at = Money(limit, currency)
    at_ref = fake.state.confirmations.issue("O000002", at)
    assert isinstance(_authorize(fake, at_ref, key="a2", order_id="O000002", amount=at), Ok)
    assert [e.operation for e in fake.effects] == ["authorize"]


def test_a_currency_with_no_order_limit_is_refused() -> None:
    fake = PaymentFake()
    jpy = Money(1_000, "JPY")
    ref = fake.state.confirmations.issue(ORDER, jpy)
    assert _code(_authorize(fake, ref, amount=jpy)) == "no_order_limit"


def test_the_limits_are_placeholders_above_every_generated_order() -> None:
    """The values the user accepted (Q2). The replay set's live-price totals stay under them, so
    the replay measures the agent; the refusal is covered above."""
    assert dict(DEFAULT_ORDER_LIMITS) == {
        "USD": 200_000,
        "CAD": 270_000,
        "GBP": 160_000,
        "EUR": 210_000,
    }


# ---- P3: across instances ------------------------------------------------------------------


def test_the_one_payment_rule_holds_across_instances() -> None:
    state = PaymentState()
    one, two = PaymentFake(state=state), PaymentFake(state=state)
    first = state.confirmations.issue(ORDER, USD)
    second = state.confirmations.issue(ORDER, USD)
    assert isinstance(_authorize(one, first), Ok)
    assert _code(_authorize(two, second, key="other")) == "order_already_authorized"
    assert len(state.effects) == 1


def test_a_key_holds_across_instances() -> None:
    state = PaymentState()
    one, two = PaymentFake(state=state), PaymentFake(state=state)
    ref = state.confirmations.issue(ORDER, USD)
    first = _authorize(one, ref)
    assert _authorize(two, ref) == first
    other = state.confirmations.issue(ORDER, USD)
    assert _code(_authorize(two, other)) == "idempotency_conflict"
    assert len(state.effects) == 1


def test_the_refund_cap_holds_across_instances() -> None:
    state = PaymentState()
    one, two = PaymentFake(state=state), PaymentFake(state=state)
    amount = Money(40_000, "USD")
    ref = state.confirmations.issue(ORDER, amount)
    authorized = _authorize(one, ref, amount=amount)
    assert isinstance(authorized, Ok)
    intent_id = authorized.value.intent_id
    assert isinstance(_capture(two, intent_id, ref), Ok)
    cap = state.caps["USD"]
    half = Money(cap // 2, "USD")
    assert isinstance(one.refund(idempotency_key="r1", intent_id=intent_id, amount=half), Ok)
    assert isinstance(two.refund(idempotency_key="r2", intent_id=intent_id, amount=half), Ok)
    over = two.refund(idempotency_key="r3", intent_id=intent_id, amount=Money(1, "USD"))
    assert _code(over) == "refund_over_cap"


def test_a_confirmation_used_on_one_instance_is_used_on_all() -> None:
    state = PaymentState()
    one, two = PaymentFake(state=state), PaymentFake(state=state)
    intent_id, ref = None, state.confirmations.issue(ORDER, USD)
    authorized = _authorize(one, ref)
    assert isinstance(authorized, Ok)
    intent_id = authorized.value.intent_id
    assert isinstance(two.void(idempotency_key="v1", intent_id=intent_id), Ok)
    assert _code(_authorize(two, ref, key="a2")) == "confirmation_used"
    # And the capture rule reads the shared binding: instance two captures with instance one's.
    fresh = state.confirmations.issue(ORDER, USD)
    again = _authorize(two, fresh, key="a3")
    assert isinstance(again, Ok)
    assert isinstance(_capture(one, again.value.intent_id, fresh), Ok)


def test_one_assessment_per_order_holds_across_instances() -> None:
    state = FraudState()
    one, two = FraudFake(state=state), FraudFake(state=state)
    assert isinstance(one.assess(idempotency_key="f1", order_id=ORDER, shopper_id="S0001"), Ok)
    again = two.assess(idempotency_key="f2", order_id=ORDER, shopper_id="S0001")
    assert _code(again) == "already_assessed"
    seen = two.assessments_for(ORDER)
    assert isinstance(seen, Ok) and len(seen.value) == 1


def test_scopes_stay_per_instance_over_one_state() -> None:
    state = PaymentState()
    full = PaymentFake(state=state)
    reader = PaymentFake(state=state, scopes=frozenset({"status"}))
    ref = state.confirmations.issue(ORDER, USD)
    assert _code(_authorize(reader, ref)) == "not_permitted"
    authorized = _authorize(full, ref)
    assert isinstance(authorized, Ok)
    seen = reader.status(authorized.value.intent_id)
    assert isinstance(seen, Ok) and seen.value.status is PaymentStatus.AUTHORIZED
    assert _code(_capture(reader, authorized.value.intent_id, ref)) == "not_permitted"


def test_a_state_and_its_own_settings_are_never_both_given() -> None:
    with pytest.raises(ValueError):
        PaymentFake(state=PaymentState(), refund_caps={"USD": 1})
    with pytest.raises(ValueError):
        FraudFake(state=FraudState(), flagged_order_ids=[ORDER])


# ---- Q1: the sandbox issuer's soft-decline retry -------------------------------------------


def test_by_default_a_soft_decline_token_declines_every_attempt() -> None:
    fake = PaymentFake()
    ref = fake.state.confirmations.issue(ORDER, USD)
    for key in ("a1", "a2", "a3"):
        assert _code(_authorize(fake, ref, key=key, token=SANDBOX_SOFT_DECLINE)) == "soft_decline"


def test_with_the_switch_the_issuer_approves_the_next_attempt_under_a_new_key() -> None:
    fake = PaymentFake(state=PaymentState(soft_decline_retry_approves=True))
    ref = fake.state.confirmations.issue(ORDER, USD)
    assert _code(_authorize(fake, ref, key="a1", token=SANDBOX_SOFT_DECLINE)) == "soft_decline"
    # A same-key replay is the recorded decline, not a new attempt.
    assert _code(_authorize(fake, ref, key="a1", token=SANDBOX_SOFT_DECLINE)) == "soft_decline"
    retried = _authorize(fake, ref, key="a2", token=SANDBOX_SOFT_DECLINE)
    assert isinstance(retried, Ok)
    # A dry run never counts as the first attempt.
    other = PaymentFake(state=PaymentState(soft_decline_retry_approves=True))
    other_ref = other.state.confirmations.issue(ORDER, USD)
    _authorize(other, other_ref, key="d", token=SANDBOX_SOFT_DECLINE, dry_run=True)
    assert _code(_authorize(other, other_ref, key="a1", token=SANDBOX_SOFT_DECLINE)) == (
        "soft_decline"
    )
