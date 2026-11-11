"""BE-05 (partial) criteria, written from the PRD and the approved plan, not from the code: the
Orders agent validates, scores, takes payment and confirms; it resolves routine exceptions and
escalates the rest with context; the confirmation rule, the per-order limit and the shared states
hold whatever the model does. Handlers are driven directly where no model is needed. No spend."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.agents.orders_place import (
    OrderPlacementTask,
    PlacementResult,
    Ports,
    build_handlers,
    finish,
)
from northwind_backend.audit import AuditEvent, InMemoryAuditSink
from northwind_backend.errors import AgentResult, Ok, Result, RunStatus, ToolError
from northwind_backend.fakes._base import FaultKind
from northwind_backend.fakes.fraud import FraudFake
from northwind_backend.fakes.orders import OrderState, OrderStoreFake
from northwind_backend.fakes.payment import (
    DEFAULT_ORDER_LIMITS,
    SANDBOX_APPROVED,
    PaymentFake,
    PaymentState,
)
from northwind_backend.ports import Money, OrderStatus, PaymentStatus
from northwind_backend.recovery import RetryPolicy
from northwind_llm_replay.scripted import ScriptedLLM

from ._placement import World, first_of, total_for
from .test_be05_place_scripted import HAPPY, place, script

pytestmark = pytest.mark.req("BE-05")

FAST = RetryPolicy(max_attempts=3, deadline_seconds=5.0, backoff_seconds=())
FAST_WRITE = RetryPolicy(
    max_attempts=3,
    deadline_seconds=5.0,
    backoff_seconds=(),
    retry_codes=frozenset({"service_unavailable", "audit_unavailable"}),
)
BEFORE_AUTHORIZE = ["validate_order", "assess_fraud"]
BEFORE_CAPTURE = [*BEFORE_AUTHORIZE, "authorize_payment"]
BEFORE_CONFIRM = [*BEFORE_CAPTURE, "capture_payment"]


def code_of(text: dict[str, Any]) -> str:
    return str(json.loads(text["content"][0]["text"]).get("code"))


def body_of(text: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(text["content"][0]["text"])
    return body


async def run_calls(
    world: World,
    task: OrderPlacementTask,
    calls: list[str],
    *,
    ports: Ports | None = None,
    correlation_id: str = "run-1",
    finish_run: bool = True,
) -> tuple[Any, list[dict[str, Any]], PlacementResult | None]:
    handlers = build_handlers(
        task,
        ports or world.ports(),
        world.audit(),
        correlation_id=correlation_id,
        read_policy=FAST,
        write_policy=FAST_WRITE,
    )
    replies = [await getattr(handlers, c)({"order_id": task.order_id}) for c in calls]
    result = None
    if finish_run:
        stub = AgentResult(RunStatus.COMPLETE, (), (), handlers.audit.correlation_id)
        result = await finish(handlers, stub)
    return handlers, replies, result


def drive(
    world: World, task: OrderPlacementTask, calls: list[str], **kw: Any
) -> tuple[Any, list[dict[str, Any]], PlacementResult | None]:
    return asyncio.run(run_calls(world, task, calls, **kw))


# ---- the order store's own refusals and dry runs -------------------------------------------


def _store(world: World) -> OrderStoreFake:
    return OrderStoreFake(state=world.orders)


def _is_error(result: Result[Any], code: str) -> bool:
    return isinstance(result, ToolError) and result.code == code


def test_confirming_twice_with_different_keys_makes_one_confirmed_order() -> None:
    world, row = World.make(), first_of("clean")
    store, oid = _store(world), row["order_id"]
    first = store.confirm(
        idempotency_key="k1", order_id=oid, intent_id="pi_000001", amount=total_for(row)
    )
    second = store.confirm(
        idempotency_key="k2", order_id=oid, intent_id="pi_000002", amount=total_for(row)
    )
    assert isinstance(first, Ok) and _is_error(second, "already_confirmed")
    assert [e.operation for e in store.effects] == ["confirm"]
    assert world.orders.orders[oid].intent_id == "pi_000001"


def test_an_order_cannot_be_escalated_after_it_is_confirmed_or_the_reverse() -> None:
    world = World.make()
    confirmed, escalated = first_of("clean"), first_of("stock_out")
    store = _store(world)
    store.confirm(
        idempotency_key="c1",
        order_id=confirmed["order_id"],
        intent_id="pi_000001",
        amount=total_for(confirmed),
    )
    late = store.escalate(
        idempotency_key="e1", order_id=confirmed["order_id"], reason="agent_requested", context={}
    )
    assert _is_error(late, "order_not_pending")
    store.escalate(
        idempotency_key="e2", order_id=escalated["order_id"], reason="out_of_stock", context={}
    )
    after = store.confirm(
        idempotency_key="c2",
        order_id=escalated["order_id"],
        intent_id="pi_000002",
        amount=total_for(escalated),
    )
    assert _is_error(after, "order_not_pending")
    assert world.orders.orders[confirmed["order_id"]].status is OrderStatus.CONFIRMED
    assert world.orders.orders[escalated["order_id"]].status is OrderStatus.ESCALATED


def test_a_second_escalation_with_another_key_adds_no_second_escalation() -> None:
    world, row = World.make(), first_of("stock_out")
    a, b = _store(world), _store(world)  # two instances, one state
    oid = row["order_id"]
    assert isinstance(
        a.escalate(idempotency_key="e1", order_id=oid, reason="out_of_stock", context={"n": 1}), Ok
    )
    again = b.escalate(idempotency_key="e2", order_id=oid, reason="out_of_stock", context={"n": 2})
    assert _is_error(again, "already_escalated")
    assert [e.operation for e in world.orders.effects] == ["escalate"]
    assert world.orders.escalations[oid] == {"n": 1}


def test_two_store_instances_confirm_one_order_once() -> None:
    world, row = World.make(), first_of("clean")
    a, b = _store(world), _store(world)
    amount, oid = total_for(row), row["order_id"]
    assert isinstance(
        a.confirm(idempotency_key="k1", order_id=oid, intent_id="pi_000001", amount=amount), Ok
    )
    assert _is_error(
        b.confirm(idempotency_key="k2", order_id=oid, intent_id="pi_000009", amount=amount),
        "already_confirmed",
    )
    assert len(world.orders.effects) == 1


def _one_order_state(postal: str, region: str, on_file: tuple[str, str]) -> OrderState:
    order = {
        "order_id": "O000001",
        "shopper_id": "S000001",
        "region": region,
        "lines": [{"product_id": "P1", "variant_id": "V1", "quantity": 1}],
        "payment_token": SANDBOX_APPROVED,
        "shipping_postal_code": postal,
    }
    shopper = {
        "shopper_id": "S000001",
        "address": {"region": on_file[0], "postal_code": on_file[1]},
    }
    return OrderState([order], [shopper])


@pytest.mark.parametrize(
    ("on_file", "code"),
    [
        (("CA", "94105"), "no_usable_address_on_file"),  # another region than the order's
        (("US", "XX999"), "no_usable_address_on_file"),  # undeliverable too
    ],
)
def test_an_address_on_file_in_another_region_or_undeliverable_is_not_used(
    on_file: tuple[str, str], code: str
) -> None:
    state = _one_order_state("XX123", "US", on_file)
    store = OrderStoreFake(state=state)
    result = store.use_address_on_file(idempotency_key="a1", order_id="O000001")
    assert _is_error(result, code)
    assert state.orders["O000001"].shipping_postal_code == "XX123"
    assert not state.orders["O000001"].address_on_file and store.effects == ()


def test_a_deliverable_address_is_left_alone() -> None:
    state = _one_order_state("94105", "US", ("US", "10001"))
    result = OrderStoreFake(state=state).use_address_on_file(
        idempotency_key="a1", order_id="O000001"
    )
    assert _is_error(result, "address_already_deliverable")
    assert state.orders["O000001"].shipping_postal_code == "94105"


def test_dry_runs_on_the_order_store_change_nothing_and_do_not_use_the_key() -> None:
    world, fixed, clean, stock = (
        World.make(),
        first_of("address_error"),
        first_of("clean"),
        first_of("stock_out"),
    )
    store = _store(world)
    fix = store.use_address_on_file(idempotency_key="d1", order_id=fixed["order_id"], dry_run=True)
    done = store.confirm(
        idempotency_key="d2",
        order_id=clean["order_id"],
        intent_id="pi_000001",
        amount=total_for(clean),
        dry_run=True,
    )
    esc = store.escalate(
        idempotency_key="d3",
        order_id=stock["order_id"],
        reason="out_of_stock",
        context={"a": 1},
        dry_run=True,
    )
    for dry in (fix, done, esc):
        assert isinstance(dry, Ok) and dry.value.dry_run
    assert store.effects == () and world.orders.escalations == {}
    assert world.orders.orders[fixed["order_id"]].shipping_postal_code.startswith("XX")
    assert world.orders.orders[clean["order_id"]].status is OrderStatus.PENDING
    assert world.orders.orders[stock["order_id"]].status is OrderStatus.PENDING
    # The keys were not recorded: the real calls under the same keys run.
    assert isinstance(
        store.use_address_on_file(idempotency_key="d1", order_id=fixed["order_id"]), Ok
    )
    assert isinstance(
        store.confirm(
            idempotency_key="d2",
            order_id=clean["order_id"],
            intent_id="pi_000001",
            amount=total_for(clean),
        ),
        Ok,
    )


def test_a_dry_run_assessment_records_nothing_and_a_real_one_still_runs() -> None:
    world, row = World.make(), first_of("clean")
    fraud = FraudFake(state=world.fraud)
    kw: dict[str, Any] = {"order_id": row["order_id"], "shopper_id": row["shopper_id"]}
    dry = fraud.assess(idempotency_key="f1", dry_run=True, **kw)
    assert isinstance(dry, Ok) and dry.value.dry_run
    assert fraud.effects == ()
    real = fraud.assess(idempotency_key="f1", **kw)
    assert isinstance(real, Ok) and not real.value.dry_run
    assert [e.operation for e in world.fraud.effects] == ["assess"]


def test_a_dry_run_authorize_and_capture_move_no_money() -> None:
    world, row = World.make(), first_of("clean")
    pay, amount, oid = PaymentFake(state=world.payment), total_for(row), row["order_id"]
    ref = world.confirm(row)
    dry = pay.authorize(
        idempotency_key="p1",
        payment_token=SANDBOX_APPROVED,
        amount=amount,
        order_id=oid,
        confirmation_ref=ref,
        dry_run=True,
    )
    assert isinstance(dry, Ok) and dry.value.dry_run
    assert pay.effects == () and world.payment.intents == {}
    real = pay.authorize(
        idempotency_key="p1",
        payment_token=SANDBOX_APPROVED,
        amount=amount,
        order_id=oid,
        confirmation_ref=ref,
    )
    assert isinstance(real, Ok)
    dry_capture = pay.capture(
        idempotency_key="p2", intent_id=real.value.intent_id, confirmation_ref=ref, dry_run=True
    )
    assert isinstance(dry_capture, Ok)
    assert world.payment.intents[real.value.intent_id].status is PaymentStatus.AUTHORIZED


# ---- the confirmation rule: gaps ------------------------------------------------------------


def _authorize_for(pay: PaymentFake, row: dict[str, Any], ref: str, key: str) -> Result[Any]:
    return pay.authorize(
        idempotency_key=key,
        payment_token=SANDBOX_APPROVED,
        amount=total_for(row),
        order_id=row["order_id"],
        confirmation_ref=ref,
    )


def test_capture_above_the_confirmed_amount_is_refused() -> None:
    world, row = World.make(), first_of("clean")
    pay, total = PaymentFake(state=world.payment), total_for(row)
    ref = world.confirm(row)
    intent = _authorize_for(pay, row, ref, "a1")
    assert isinstance(intent, Ok)
    over = pay.capture(
        idempotency_key="c1",
        intent_id=intent.value.intent_id,
        confirmation_ref=ref,
        amount=Money(total.amount_minor + 1, total.currency),
    )
    assert isinstance(over, ToolError)
    assert [e.operation for e in pay.effects] == ["authorize"]
    # The exact confirmed amount is still capturable with the same reference.
    assert isinstance(
        pay.capture(idempotency_key="c2", intent_id=intent.value.intent_id, confirmation_ref=ref),
        Ok,
    )


def test_capture_on_another_instance_needs_the_authorizing_reference() -> None:
    world, row = World.make(), first_of("clean")
    a, b = PaymentFake(state=world.payment), PaymentFake(state=world.payment)
    ref, fresh = world.confirm(row), world.confirm(row)
    intent = _authorize_for(a, row, ref, "a1")
    assert isinstance(intent, Ok)
    refused = b.capture(
        idempotency_key="c1", intent_id=intent.value.intent_id, confirmation_ref=fresh
    )
    assert _is_error(refused, "confirmation_mismatch")
    accepted = b.capture(
        idempotency_key="c2", intent_id=intent.value.intent_id, confirmation_ref=ref
    )
    assert isinstance(accepted, Ok) and accepted.value.status is PaymentStatus.CAPTURED


def test_a_voided_intent_cannot_be_captured_and_a_new_authorization_needs_a_new_reference() -> None:
    world, row = World.make(), first_of("clean")
    pay = PaymentFake(state=world.payment)
    ref = world.confirm(row)
    intent = _authorize_for(pay, row, ref, "a1")
    assert isinstance(intent, Ok)
    assert isinstance(pay.void(idempotency_key="v1", intent_id=intent.value.intent_id), Ok)
    assert _is_error(_authorize_for(pay, row, ref, "a2"), "confirmation_used")
    assert isinstance(_authorize_for(pay, row, world.confirm(row), "a3"), Ok)
    captured = pay.capture(
        idempotency_key="c1", intent_id=intent.value.intent_id, confirmation_ref=ref
    )
    assert isinstance(captured, ToolError)


def _race_authorize(world: World, row: dict[str, Any]) -> list[Result[Any]]:
    refs = [world.confirm(row), world.confirm(row)]
    instances = [PaymentFake(state=world.payment), PaymentFake(state=world.payment)]
    barrier = threading.Barrier(2)
    results: list[Result[Any] | None] = [None, None]

    def attempt(n: int) -> None:
        barrier.wait()
        results[n] = _authorize_for(instances[n], row, refs[n], f"key-{n}")

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return [r for r in results if r is not None]


def test_two_fresh_references_for_one_order_authorize_once_even_at_once() -> None:
    for _ in range(5):
        world, row = World.make(), first_of("clean")
        results = _race_authorize(world, row)
        assert len(results) == 2 and len([r for r in results if isinstance(r, Ok)]) == 1
        live = [i for i in world.payment.intents.values() if i.status is not PaymentStatus.VOIDED]
        assert len(live) == 1
        assert [e.operation for e in world.payment.effects] == ["authorize"]


@pytest.mark.parametrize("currency", sorted(DEFAULT_ORDER_LIMITS))
def test_the_default_limit_is_per_currency_and_exact(currency: str) -> None:
    world = World.make()
    pay = PaymentFake(state=world.payment)
    limit = DEFAULT_ORDER_LIMITS[currency]
    for n, (amount, ok) in enumerate(((limit, True), (limit + 1, False))):
        oid = f"O00000{n + 1}"
        ref = world.payment.confirmations.issue(oid, Money(amount, currency))
        result = pay.authorize(
            idempotency_key=f"k{n}",
            payment_token=SANDBOX_APPROVED,
            amount=Money(amount, currency),
            order_id=oid,
            confirmation_ref=ref,
        )
        assert isinstance(result, Ok) is ok
        if not ok:
            assert _is_error(result, "over_order_limit")


# ---- the agent: escalation reasons and context from run state ------------------------------


def _check_context(result: PlacementResult, world: World, row: dict[str, Any], reason: str) -> None:
    assert result.outcome == "escalated" and not result.touchless
    assert result.reason == reason
    context = result.context
    assert context is not None
    assert context["order_id"] == row["order_id"] and context["reason"] == reason
    assert context["recommended_action"] and context["correlation_id"] == "run-1"
    assert world.orders.escalations[row["order_id"]] == json.loads(json.dumps(context))
    assert world.orders.orders[row["order_id"]].escalation == reason


def test_an_order_over_the_per_order_limit_is_escalated_and_charged_nothing() -> None:
    world, row = World.make(), first_of("clean")
    total = total_for(row)
    world.payment = PaymentState(order_limits={total.currency: total.amount_minor - 1})
    _, _, result = drive(world, world.task(row), BEFORE_CAPTURE + ["escalate_order"])
    assert result is not None
    _check_context(result, world, row, "over_order_limit")
    assert world.payment.effects == [] and world.payment.intents == {}


def test_an_order_in_a_currency_with_no_limit_is_escalated() -> None:
    world, row = World.make(), first_of("clean")
    world.payment = PaymentState(order_limits={})
    _, _, result = drive(world, world.task(row), BEFORE_CAPTURE + ["escalate_order"])
    assert result is not None
    _check_context(result, world, row, "over_order_limit")
    assert world.payment.effects == []


def test_a_confirmation_for_another_amount_is_escalated_and_charged_nothing() -> None:
    world, row = World.make(), first_of("clean")
    total = total_for(row)
    stale = world.payment.confirmations.issue(
        row["order_id"], Money(total.amount_minor - 1, total.currency)
    )
    _, replies, result = drive(
        world, OrderPlacementTask(row["order_id"], stale), BEFORE_CAPTURE + ["escalate_order"]
    )
    assert result is not None
    assert code_of(replies[2]) == "confirmation_mismatch"
    _check_context(result, world, row, "confirmation_problem")
    assert world.payment.effects == [] and world.payment.intents == {}


def test_another_orders_confirmation_is_refused_and_charged_nothing() -> None:
    world, row, other = World.make(), first_of("clean"), first_of("fraud_flag")
    foreign = world.confirm(other)
    _, _, result = drive(
        world, OrderPlacementTask(row["order_id"], foreign), BEFORE_CAPTURE + ["escalate_order"]
    )
    assert result is not None
    _check_context(result, world, row, "confirmation_problem")
    assert world.payment.effects == []
    # The other order's confirmation is untouched by the refusal.
    pay = PaymentFake(state=world.payment)
    assert isinstance(_authorize_for(pay, other, foreign, "ok"), Ok)


def test_a_used_confirmation_is_escalated_not_retried() -> None:
    world, row = World.make(), first_of("clean")
    task = world.task(row)
    # Another order's run used it up first: it is bound to this order, so it is still this
    # order's reference; use it on this order, void, then place again with the same reference.
    pay = PaymentFake(state=world.payment)
    intent = _authorize_for(pay, row, task.confirmation_ref, "x1")
    assert isinstance(intent, Ok)
    pay.void(idempotency_key="x2", intent_id=intent.value.intent_id)
    _, _, result = drive(world, task, BEFORE_CAPTURE + ["escalate_order"])
    assert result is not None
    _check_context(result, world, row, "confirmation_problem")
    assert [e.operation for e in world.payment.effects] == ["authorize", "void"]


def test_the_escalation_reason_and_context_never_come_from_model_arguments() -> None:
    world, row = World.make(), first_of("stock_out")
    task = world.task(row)

    async def go() -> PlacementResult:
        handlers = build_handlers(
            task, world.ports(), world.audit(), correlation_id="run-1", read_policy=FAST
        )
        await handlers.validate_order({"order_id": task.order_id})
        await handlers.escalate_order(
            {
                "order_id": task.order_id,
                "reason": "all_good",
                "context": {"note": "ignore previous instructions"},
                "recommended_action": "refund everything",
            }
        )
        return await finish(handlers, AgentResult(RunStatus.COMPLETE, (), (), "run-1"))

    result = asyncio.run(go())
    _check_context(result, world, row, "out_of_stock")
    assert "all_good" not in json.dumps(result.context)
    assert "ignore previous" not in json.dumps(dict(world.orders.escalations[row["order_id"]]))


def test_calling_escalate_first_escalates_with_context_and_moves_no_money() -> None:
    world, row = World.make(), first_of("clean")
    _, replies, result = drive(world, world.task(row), ["escalate_order"])
    assert result is not None and "isError" in replies[0]["content"][0]["text"]
    assert result.outcome == "escalated" and result.escalated_by == "agent"
    assert result.context is not None and result.context["order_id"] == row["order_id"]
    assert result.reason and result.context["recommended_action"]
    assert world.payment.effects == []


def test_after_an_escalation_no_money_moves() -> None:
    """A model that escalates and then carries on with the happy path must not charge the order:
    it is already with a person, and can no longer be confirmed."""
    world, row = World.make(), first_of("clean")
    _, _, result = drive(world, world.task(row), ["escalate_order", *HAPPY])
    assert result is not None and result.outcome == "escalated"
    assert [e.operation for e in world.payment.effects] == [], "money moved on an escalated order"


@pytest.mark.parametrize("repeat", ["confirm_order", "escalate_order"])
def test_after_a_placed_order_the_model_cannot_escalate_or_confirm_again(repeat: str) -> None:
    world, row = World.make(), first_of("clean")
    _, replies, result = drive(world, world.task(row), [*HAPPY, repeat])
    assert result is not None and result.outcome == "placed" and result.reason is None
    assert body_of(replies[-1])["isError"] is True
    assert world.orders.orders[row["order_id"]].status is OrderStatus.CONFIRMED
    assert [e.operation for e in world.orders.effects] == ["confirm"]
    assert world.orders.escalations == {}


@pytest.mark.parametrize(
    "tool",
    [
        "validate_order",
        "use_address_on_file",
        "assess_fraud",
        "authorize_payment",
        "capture_payment",
        "confirm_order",
        "escalate_order",
    ],
)
@pytest.mark.parametrize("bad", ["O999999", "", "O000001; drop", "4111111111111111"])
def test_every_tool_refuses_any_other_order_and_reaches_no_port(tool: str, bad: str) -> None:
    world, row = World.make(), first_of("clean")
    handlers, replies, _ = drive(world, world.task(row), [], finish_run=False)
    reply = asyncio.run(getattr(handlers, tool)({"order_id": bad}))
    assert body_of(reply)["isError"] is True
    assert code_of(reply) == "not_permitted"
    assert not any(
        e.phase == "attempt" and e.action != "agent.refused" for e in world.sink.read_all()
    )
    assert not list(world.effects())
    assert replies == []


def test_a_missing_or_non_string_order_id_is_refused() -> None:
    world, row = World.make(), first_of("clean")
    handlers, _, _ = drive(world, world.task(row), [], finish_run=False)
    for args in ({}, {"order_id": None}, {"order_id": 5}, {"order_id": [row["order_id"]]}):
        reply = asyncio.run(handlers.validate_order(args))
        assert body_of(reply)["isError"] is True
    assert not list(world.effects())


# ---- timeouts, outages and audit_incomplete -------------------------------------------------


class FailingOutcomeSink(InMemoryAuditSink):
    """Fails the write of one action's outcome, then works again."""

    def __init__(self, action: str) -> None:
        super().__init__()
        self.action, self.failed = action, False

    def append(self, draft: Any) -> AuditEvent:
        if (
            not self.failed
            and draft.get("phase") == "outcome"
            and draft.get("action") == self.action
        ):
            self.failed = True
            raise OSError("disk full")
        return super().append(draft)


@pytest.mark.parametrize("operation", ["authorize", "capture"])
def test_an_audit_incomplete_write_is_never_called_again_and_is_escalated(operation: str) -> None:
    world, row = World.make(), first_of("clean")
    sink = FailingOutcomeSink(f"payment.{operation}")
    world.sink = sink
    tool = f"{operation}_payment"
    calls = [*HAPPY[: HAPPY.index(tool) + 1], tool, "confirm_order", "escalate_order"]
    _, replies, result = drive(world, world.task(row), calls)
    assert sink.failed
    at = HAPPY.index(tool)
    assert code_of(replies[at]) == "audit_incomplete"
    text = body_of(replies[at])
    assert text["isRetryable"] is False
    noun = {"authorize": "payment authorization", "capture": "payment capture"}[operation]
    assert f"The {noun} did not finish and may have taken effect" in text["message"]
    attempts = [
        e
        for e in world.sink.read_all()
        if e.action == f"payment.{operation}" and e.phase == "attempt"
    ]
    assert len(attempts) == 1, "an audit_incomplete write was called again"
    assert [e.operation for e in world.payment.effects].count(operation) == 1
    assert result is not None
    assert result.outcome == "escalated" and result.reason == "uncertain_write"
    assert [a for a, _ in result.uncertain] == [f"payment.{operation}"]
    assert not any(e.operation == "confirm" for e in world.orders.effects)


@pytest.mark.parametrize(
    ("tool", "operation", "prefix", "kind", "text"),
    [
        (
            "use_address_on_file",
            "use_address_on_file",
            ["validate_order"],
            "address_error",
            "The address change did not finish",
        ),
        (
            "assess_fraud",
            "assess",
            ["validate_order"],
            "clean",
            "The fraud assessment did not finish",
        ),
        (
            "authorize_payment",
            "authorize",
            BEFORE_AUTHORIZE,
            "clean",
            "The payment authorization did not finish",
        ),
        (
            "capture_payment",
            "capture",
            BEFORE_CAPTURE,
            "clean",
            "The payment capture did not finish",
        ),
        (
            "confirm_order",
            "confirm",
            BEFORE_CONFIRM,
            "clean",
            "The order confirmation did not finish",
        ),
        (
            "escalate_order",
            "escalate",
            ["validate_order"],
            "stock_out",
            "The escalation did not finish",
        ),
    ],
)
def test_each_write_tools_timeout_text_is_specific_and_says_not_to_call_again(
    tool: str, operation: str, prefix: list[str], kind: str, text: str
) -> None:
    world, row = World.make(), first_of(kind)
    ports = world.ports()
    target = {
        "use_address_on_file": ports.orders,
        "assess": ports.fraud,
        "authorize": ports.payment,
        "capture": ports.payment,
        "confirm": ports.orders,
        "escalate": ports.orders,
    }[operation]
    target.faults.inject(operation, FaultKind.TIMEOUT_AFTER, times=1)  # type: ignore[attr-defined]
    handlers, _, _ = drive(world, world.task(row), prefix, ports=ports, finish_run=False)
    reply = asyncio.run(getattr(handlers, tool)({"order_id": row["order_id"]}))
    body = body_of(reply)
    assert body["isError"] is True and body["isRetryable"] is False
    assert text in body["message"] and "may have taken effect" in body["message"]
    assert "same key" not in body["message"] and "retry" not in body["message"].lower()
    assert row["order_id"] not in body["message"]
    if tool != "escalate_order":
        assert (
            asyncio.run(getattr(handlers, tool)({"order_id": row["order_id"]})) is not None
        )  # the call is answered, but see the next test: it must not reach the port
    ops = [e.operation for s in (world.payment, world.fraud, world.orders) for e in s.effects]
    assert ops.count(operation) <= 1


def test_a_read_timeout_reaches_the_model_as_order_check_text_and_nothing_is_written() -> None:
    world, row = World.make(), first_of("clean")
    ports = world.ports()
    ports.orders.faults.inject("get", FaultKind.TIMEOUT_BEFORE)  # type: ignore[attr-defined]
    _, replies, result = drive(world, world.task(row), ["validate_order"], ports=ports)
    body = body_of(replies[0])
    assert body["isError"] is True
    assert "order check did not finish in time" in body["message"]
    assert "do not call it again" in body["message"]
    assert not list(world.effects()) or all(op == "escalate" for op, _ in world.effects())
    assert result is not None and result.outcome in ("escalated", "failed")


def test_a_valid_empty_result_and_a_failed_read_are_told_apart() -> None:
    world, row = World.make(), first_of("clean")
    ports = world.ports()
    ports.orders.outage = True  # type: ignore[attr-defined]
    _, replies, _ = drive(world, world.task(row), ["validate_order"], ports=ports)
    failed = body_of(replies[0])
    assert failed["isError"] is True and failed["errorCategory"] == "transient"
    world2 = World.make()
    _, ok_replies, _ = drive(world2, world2.task(row), ["validate_order"])
    ok = body_of(ok_replies[0])
    assert ok["isError"] is False and ok["value"]["issues"] == []


@pytest.mark.parametrize("after_effect", [True, False])
def test_a_port_that_raises_is_a_doubtful_write_never_called_again(after_effect: bool) -> None:
    world, row = World.make(), first_of("clean")
    ports = world.ports()
    calls: list[int] = []

    class Boom(OrderStoreFake):
        def confirm(self, **kwargs: Any) -> Result[Any]:
            calls.append(1)
            if after_effect:
                super().confirm(**kwargs)
            raise RuntimeError("secret detail 4111111111111111")

    ports = Ports(ports.payment, ports.fraud, ports.catalog, Boom(state=world.orders))
    tools = [*HAPPY, "confirm_order", "confirm_order", "escalate_order"]
    _, replies, result = drive(world, world.task(row), tools, ports=ports)
    assert len(calls) == 1, "a doubtful write was called again"
    assert "4111" not in json.dumps(replies) and "secret detail" not in json.dumps(replies)
    assert result is not None
    assert [a for a, _ in result.uncertain] == ["order.confirm"]
    if after_effect:  # the store is the truth: the confirm did happen
        assert result.outcome == "placed"
    else:
        assert result.outcome == "escalated" and result.reason == "uncertain_write"


# ---- scripted model: hostile orders over the real loop -------------------------------------


def test_a_model_that_escalates_first_then_tries_everything_moves_no_money(tmp_path: Path) -> None:
    world, row = World.make(), first_of("clean")
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], ["escalate_order", *HAPPY], "All placed!")
        result = place(llm, tmp_path, world, world.task(row))
    assert result.outcome == "escalated"
    assert [e.operation for e in world.payment.effects] == [], "money moved after an escalation"


def test_a_model_that_only_calls_the_money_tools_gets_nothing_through(tmp_path: Path) -> None:
    world, row = World.make(), first_of("clean")
    with ScriptedLLM() as llm:
        script(
            llm,
            row["order_id"],
            ["capture_payment", "authorize_payment", "confirm_order", "assess_fraud"],
            "Paid.",
        )
        result = place(llm, tmp_path, world, world.task(row))
    assert result.outcome == "escalated" and result.reason == "not_finished"
    assert result.escalated_by == "code" and result.context is not None
    assert not list(op for op, _ in world.effects() if op != "escalate")


def test_a_run_ending_on_a_confirmed_order_reports_placed_whatever_the_model_says(
    tmp_path: Path,
) -> None:
    world, row = World.make(), first_of("clean")
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], HAPPY, "I could not place this order; escalating.")
        result = place(llm, tmp_path, world, world.task(row))
    assert result.outcome == "placed" and result.reason is None and result.context is None
