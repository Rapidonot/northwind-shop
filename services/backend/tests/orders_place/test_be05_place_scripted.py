"""BE-05 on a scripted model (row #21): the Orders agent validates, scores, takes payment and
confirms; routine exceptions are resolved and the rest escalated with context; the gates, keys,
bindings and the no-second-write rule hold whatever the model does. No spend, no cassette."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.agents.orders_place import (
    ACTOR,
    SYSTEM_PROMPT,
    TOOL_NAMES,
    OrderPlacementTask,
    PlacementResult,
    Ports,
    run_order_placement,
)
from northwind_backend.errors import Result
from northwind_backend.fakes._base import FaultKind
from northwind_backend.ports import PaymentPort
from northwind_backend.reconstruct import reconstruct_order
from northwind_backend.recovery import RetryPolicy
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM

from ._placement import World, first_of

pytestmark = pytest.mark.req("BE-05")

FAST = RetryPolicy(max_attempts=3, deadline_seconds=5.0, backoff_seconds=())
FAST_WRITE = RetryPolicy(
    max_attempts=3,
    deadline_seconds=5.0,
    backoff_seconds=(),
    retry_codes=frozenset({"service_unavailable", "audit_unavailable"}),
)
KEY = re.compile(r"O\d{6}:[a-z_.]+(?::\d)?", re.ASCII)
HAPPY = ["validate_order", "assess_fraud", "authorize_payment", "capture_payment", "confirm_order"]


def name(tool: str) -> str:
    return f"mcp__northwind_orders__{tool}"


def script(llm: ScriptedLLM, order_id: str, tools: list[str], text: str = "Done.") -> None:
    for n, tool in enumerate(tools):
        llm.reply_tool_use(name(tool), {"order_id": order_id}, tool_id=f"toolu_{n:02d}")
    llm.reply(text)


def place(
    llm: ScriptedLLM,
    root: Path,
    world: World,
    task: OrderPlacementTask,
    *,
    ports: Ports | None = None,
    correlation_id: str = "run-1",
) -> PlacementResult:
    runtime = agent_runtime(base_url=llm.url, root=root)
    return asyncio.run(
        run_order_placement(
            task,
            ports=ports or world.ports(),
            audit=world.audit(),
            correlation_id=correlation_id,
            runtime=runtime,
            read_policy=FAST,
            write_policy=FAST_WRITE,
        )
    )


def keys(world: World) -> list[str]:
    return [
        e.inputs["idempotency_key"]
        for e in world.sink.read_all()
        if e.phase == "attempt" and isinstance(e.inputs.get("idempotency_key"), str)
    ]


def model_text(llm: ScriptedLLM) -> str:
    return "\n".join(
        json.dumps(r["body"]) if not isinstance(r["body"], str) else r["body"] for r in llm.requests
    )


# ---- the flow and the routine resolutions --------------------------------------------------


def test_a_clean_order_is_validated_scored_paid_and_confirmed(tmp_path: Path) -> None:
    world, row = World.make(), first_of("clean")
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], HAPPY, "The order was placed.")
        result = place(llm, tmp_path, world, world.task(row))
    assert result.outcome == "placed" and result.touchless
    assert result.reason is None and result.context is None
    assert [r.step for r in result.agent.results] == HAPPY
    assert sorted(op for op, _ in world.effects()) == ["assess", "authorize", "capture", "confirm"]
    order = world.orders.orders[row["order_id"]]
    assert order.status.value == "confirmed" and order.intent_id is not None
    intent = world.payment.intents[order.intent_id]
    assert intent.captured_minor == order.amount_minor > 0


def test_only_the_seven_placement_tools_are_offered(tmp_path: Path) -> None:
    world, row = World.make(), first_of("clean")
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], HAPPY)
        place(llm, tmp_path, world, world.task(row))
    offered = {t["name"] for t in llm.requests[0]["body"]["tools"]}
    assert offered == set(TOOL_NAMES)
    assert not any(word in t for t in offered for word in ("refund", "void", "shipment"))


def test_an_address_typo_is_resolved_from_the_address_on_file(tmp_path: Path) -> None:
    world, row = World.make(), first_of("address_error")
    tools = ["validate_order", "use_address_on_file", *HAPPY]
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], tools)
        result = place(llm, tmp_path, world, world.task(row))
    assert result.outcome == "placed"
    order = world.orders.orders[row["order_id"]]
    assert order.address_on_file and not order.shipping_postal_code.startswith("XX")
    # The model never saw either address.
    assert row["shipping_postal_code"] not in model_text(llm)
    assert order.shipping_postal_code not in model_text(llm)


def test_a_soft_decline_is_resolved_by_one_more_authorization(tmp_path: Path) -> None:
    world, row = World.make(), first_of("soft_decline")
    tools = ["validate_order", "assess_fraud", "authorize_payment", "authorize_payment"]
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], [*tools, "capture_payment", "confirm_order"])
        result = place(llm, tmp_path, world, world.task(row))
    assert result.outcome == "placed"
    oid = row["order_id"]
    assert [k for k in keys(world) if "authorize" in k] == [
        f"{oid}:payment.authorize:1",
        f"{oid}:payment.authorize:2",
    ]


@pytest.mark.parametrize(
    ("kind", "tools", "reason"),
    [
        ("stock_out", ["validate_order", "escalate_order"], "out_of_stock"),
        ("fraud_flag", ["validate_order", "assess_fraud", "escalate_order"], "fraud_review"),
    ],
)
def test_the_rest_is_escalated_with_context_and_moves_no_money(
    tmp_path: Path, kind: str, tools: list[str], reason: str
) -> None:
    world, row = World.make(), first_of(kind)
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], tools)
        result = place(llm, tmp_path, world, world.task(row))
    assert result.outcome == "escalated" and not result.touchless
    assert result.reason == reason and result.escalated_by == "agent"
    context = result.context
    assert context is not None
    assert context["order_id"] == row["order_id"] and context["reason"] == reason
    assert context["recommended_action"] and context["shopper_id"] == row["shopper_id"]
    assert context["total"]["total_minor"] > 0
    assert world.orders.escalations[row["order_id"]] == json.loads(json.dumps(context))
    assert not any(op in ("authorize", "capture") for op, _ in world.effects())


def test_a_second_soft_decline_is_escalated_as_a_decline(tmp_path: Path) -> None:
    world, row = World.make(soft_decline_retry_approves=False), first_of("soft_decline")
    tools = ["validate_order", "assess_fraud", "authorize_payment", "authorize_payment"]
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], [*tools, "authorize_payment", "escalate_order"])
        result = place(llm, tmp_path, world, world.task(row))
    assert result.outcome == "escalated" and result.reason == "payment_declined"
    # The third attempt never reached the port.
    assert [k for k in keys(world) if "authorize" in k][-1].endswith(":2")
    refused = [
        e for e in world.sink.read_all() if e.action == "agent.refused" and e.phase == "outcome"
    ]
    assert [(e.outcome or {}).get("code") for e in refused] == ["no_more_attempts"]


def test_a_run_that_stops_early_is_escalated_by_code(tmp_path: Path) -> None:
    world, row = World.make(), first_of("clean")
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], ["validate_order"], "I validated it.")
        result = place(llm, tmp_path, world, world.task(row))
    assert result.outcome == "escalated"
    assert result.reason == "not_finished" and result.escalated_by == "code"


def test_the_outcome_never_comes_from_the_summary(tmp_path: Path) -> None:
    world, row = World.make(), first_of("stock_out")
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], ["validate_order"], "The order was placed and paid.")
        result = place(llm, tmp_path, world, world.task(row))
    assert result.outcome == "escalated" and result.reason == "out_of_stock"


# ---- gates and bindings --------------------------------------------------------------------


def test_another_orders_id_is_refused_audited_and_never_reaches_a_port(tmp_path: Path) -> None:
    world, row = World.make(), first_of("clean")
    other = first_of("fraud_flag")["order_id"]
    with ScriptedLLM() as llm:
        llm.reply_tool_use(name("authorize_payment"), {"order_id": other}, tool_id="toolu_x")
        script(llm, row["order_id"], HAPPY)
        result = place(llm, tmp_path, world, world.task(row))
    assert result.outcome == "placed"
    assert all(ref != other for _, ref in world.effects())
    refused = [
        e for e in world.sink.read_all() if e.action == "agent.refused" and e.phase == "outcome"
    ]
    assert [(e.inputs["order_id"], (e.outcome or {}).get("code")) for e in refused] == [
        (other, "not_permitted")
    ]


@pytest.mark.parametrize(
    "tools",
    [
        ["authorize_payment"],  # before validating
        ["validate_order", "authorize_payment"],  # before the fraud score
        ["validate_order", "capture_payment"],  # before authorizing
        ["validate_order", "assess_fraud", "confirm_order"],  # before capturing
        ["validate_order", "use_address_on_file"],  # nothing to fix
    ],
)
def test_steps_out_of_order_are_refused_before_any_port(tmp_path: Path, tools: list[str]) -> None:
    world, row = World.make(), first_of("clean")
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], tools)
        place(llm, tmp_path, world, world.task(row))
    assert not any(
        op in ("authorize", "capture", "confirm", "use_address_on_file")
        for op, _ in world.effects()
    )


def test_every_write_key_comes_from_the_order_id_alone(tmp_path: Path) -> None:
    world, row = World.make(), first_of("soft_decline")
    tools = ["validate_order", "assess_fraud", "authorize_payment", "authorize_payment"]
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], [*tools, "capture_payment", "confirm_order"])
        place(llm, tmp_path, world, world.task(row))
    written = keys(world)
    assert written and all(KEY.fullmatch(k) and k.startswith(row["order_id"]) for k in written)


def test_the_model_sees_no_amount_id_token_address_or_reference(tmp_path: Path) -> None:
    world, row = World.make(), first_of("clean")
    task = world.task(row)
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], HAPPY)
        result = place(llm, tmp_path, world, task, correlation_id="run-secret-1")
    seen = model_text(llm)
    order = world.orders.orders[row["order_id"]]
    assert order.intent_id is not None
    for hidden in (
        task.confirmation_ref,
        row["payment_token"],
        row["shopper_id"],
        row["shipping_postal_code"],
        order.intent_id,
        "fa_0",
        "run-secret-1",
        "checkout-1",
        str(order.amount_minor),
    ):
        assert hidden not in seen, hidden
    assert result.outcome == "placed"


def test_the_prompt_names_only_the_order() -> None:
    task = OrderPlacementTask("O000123", "cf_ABCDEFGHIJKLMNOP")
    assert task.prompt() == "Place order O000123."
    assert "AI system" in SYSTEM_PROMPT
    with pytest.raises(ValueError):
        OrderPlacementTask("O000123", "4111111111111111")
    with pytest.raises(ValueError):
        OrderPlacementTask("4111111111111111", "cf_ABCDEFGHIJKLMNOP")


# ---- no second write after doubt (U2) ------------------------------------------------------


class _RaiseAfter:
    """Delegates to the shared payment fake, then raises after the effect of one operation."""

    def __init__(self, inner: PaymentPort, operation: str) -> None:
        self._inner, self._operation, self.calls = inner, operation, 0

    def __getattr__(self, attr: str) -> Callable[..., Result[Any]]:
        real = getattr(self._inner, attr)
        if attr != self._operation:
            return real  # type: ignore[no-any-return]

        def call(**kwargs: Any) -> Result[Any]:
            self.calls += 1
            real(**kwargs)
            raise RuntimeError("after the effect")

        return call


@pytest.mark.parametrize("operation", ["authorize", "capture"])
@pytest.mark.parametrize("fault", ["timeout_after", "exception_after"])
def test_a_doubtful_write_is_never_called_again_and_is_escalated(
    tmp_path: Path, operation: str, fault: str
) -> None:
    world, row = World.make(), first_of("clean")
    ports = world.ports()
    if fault == "timeout_after":
        ports.payment.faults.inject(operation, FaultKind.TIMEOUT_AFTER)  # type: ignore[attr-defined]
        port: Any = ports.payment
    else:
        port = _RaiseAfter(ports.payment, operation)
    ports = Ports(payment=port, fraud=ports.fraud, catalog=ports.catalog, orders=ports.orders)
    tools = HAPPY[: HAPPY.index(f"{operation}_payment") + 1]
    with ScriptedLLM() as llm:
        # The model even tries the same write again and then the next step: all refused.
        script(llm, row["order_id"], [*tools, f"{operation}_payment", "confirm_order"])
        result = place(llm, tmp_path, world, world.task(row), ports=ports)
    attempts = [
        e
        for e in world.sink.read_all()
        if e.action == f"payment.{operation}" and e.phase == "attempt"
    ]
    assert len(attempts) == 1, "a doubtful write was called again"
    if fault == "exception_after":
        assert port.calls == 1
    assert [e.operation for e in world.payment.effects].count(operation) == 1  # it did happen
    assert result.outcome == "escalated" and result.reason == "uncertain_write"
    assert result.uncertain == (
        (
            f"payment.{operation}",
            f"{row['order_id']}:payment.{operation}" + (":1" if operation == "authorize" else ""),
        ),
    )
    assert not any(e.operation == "confirm" for e in world.orders.effects)
    record = reconstruct_order(world.sink.read_all(), row["order_id"])
    assert [u.action for u in record.uncertain] == [f"payment.{operation}"]
    assert record.uncertain[0].settled == "open"
    assert record.escalated and record.escalation_reason == "uncertain_write"


def test_an_outage_before_the_effect_is_retried_with_the_same_key_once_effective(
    tmp_path: Path,
) -> None:
    world, row = World.make(), first_of("clean")
    ports = world.ports()
    ports.payment.faults.inject("authorize", FaultKind.OUTAGE, times=2)  # type: ignore[attr-defined]
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], HAPPY)
        result = place(llm, tmp_path, world, world.task(row), ports=ports)
    assert result.outcome == "placed"
    tries = [
        e for e in world.sink.read_all() if e.action == "payment.authorize" and e.phase == "attempt"
    ]
    assert len(tries) == 3 and {e.inputs["idempotency_key"] for e in tries} == {
        f"{row['order_id']}:payment.authorize:1"
    }
    assert [e.operation for e in world.payment.effects].count("authorize") == 1


def test_a_write_timeout_reaches_the_model_as_tool_specific_text(tmp_path: Path) -> None:
    world, row = World.make(), first_of("clean")
    ports = world.ports()
    ports.payment.faults.inject("capture", FaultKind.TIMEOUT_AFTER)  # type: ignore[attr-defined]
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], HAPPY[:4] + ["escalate_order"])
        place(llm, tmp_path, world, world.task(row), ports=ports)
    seen = model_text(llm)
    assert "The payment capture did not finish and may have taken effect" in seen
    assert "same key" not in seen


def test_the_run_is_audited_under_its_own_actor(tmp_path: Path) -> None:
    world, row = World.make(), first_of("clean")
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], HAPPY)
        place(llm, tmp_path, world, world.task(row))
    run_events = [e for e in world.sink.read_all() if e.correlation_id == "run-1"]
    assert run_events and {e.actor for e in run_events} == {ACTOR}
    assert {e.parent_id for e in run_events} == {"checkout-1"}
    record = reconstruct_order(world.sink.read_all(), row["order_id"])
    assert record.order_status == "confirmed" and record.payment_status == "captured"
    assert record.captured_minor == world.orders.orders[row["order_id"]].amount_minor
