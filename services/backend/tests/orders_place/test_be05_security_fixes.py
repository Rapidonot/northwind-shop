"""Regressions for row #21's first security review
(`docs/reviews/2026-11-11-BE-05-security-review.md`).

F1: an order another run escalated (or placed) gets no write from a later run, so a rerun or a
duplicate never moves money on an escalated order. F2: a doubtful escalation is never written again,
and the reason reported is the store's. F3: capture and confirm follow the latest validation. F4: no
repr shows a confirmation reference."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from northwind_backend.agents.orders_place import (
    OrderPlacementTask,
    PlacementResult,
    Ports,
    build_handlers,
    finish,
)
from northwind_backend.confirmations import Confirmation
from northwind_backend.errors import AgentResult, RunStatus
from northwind_backend.fakes._base import FaultKind
from northwind_backend.fakes.catalog import CatalogState, CatalogTableFake
from northwind_backend.ports import Money, StockLevel
from northwind_backend.recovery import RetryPolicy

from ._placement import World, first_of

pytestmark = [pytest.mark.req("BE-05"), pytest.mark.req("CHK-05")]

FAST = RetryPolicy(max_attempts=3, deadline_seconds=5.0, backoff_seconds=())
FAST_WRITE = RetryPolicy(
    max_attempts=3,
    deadline_seconds=5.0,
    backoff_seconds=(),
    retry_codes=frozenset({"service_unavailable", "audit_unavailable"}),
)
HAPPY = ["validate_order", "assess_fraud", "authorize_payment", "capture_payment", "confirm_order"]


async def drive(
    world: World,
    task: OrderPlacementTask,
    calls: list[str],
    run: str,
    ports: Ports | None = None,
) -> PlacementResult:
    h = build_handlers(
        task,
        ports or world.ports(),
        world.audit(),
        correlation_id=run,
        read_policy=FAST,
        write_policy=FAST_WRITE,
    )
    for call in calls:
        await getattr(h, call)({"order_id": task.order_id})
    return await finish(h, AgentResult(RunStatus.COMPLETE, (), (), run))


def money(world: World, order_id: str) -> list[str]:
    intents = {i for i, it in world.payment.intents.items() if it.order_id == order_id}
    return [e.operation for e in world.payment.effects if e.reference in intents]


def codes(world: World, run: str) -> list[Any]:
    return [
        (e.outcome or {}).get("code")
        for e in world.sink.read_all()
        if e.correlation_id == run and e.action == "agent.refused" and e.phase == "outcome"
    ]


@pytest.mark.parametrize(
    "first",
    [
        ["escalate_order"],  # the model escalated at once
        ["validate_order", "assess_fraud"],  # it stopped short: `not_finished`, by code
    ],
)
def test_a_later_run_moves_no_money_on_an_order_an_earlier_run_escalated(first: list[str]) -> None:
    world, row = World.make(), first_of("clean")
    task = world.task(row)

    async def both() -> tuple[PlacementResult, PlacementResult]:
        return await drive(world, task, first, "run-1"), await drive(world, task, HAPPY, "run-2")

    one, two = asyncio.run(both())
    assert one.outcome == two.outcome == "escalated"
    assert money(world, row["order_id"]) == []
    assert "order_escalated" in codes(world, "run-2")
    assert two.reason == one.reason  # the store's reason, not a new one


def test_a_duplicate_with_a_fresh_confirmation_moves_no_money_after_an_escalation() -> None:
    world, row = World.make(), first_of("clean")

    async def both() -> PlacementResult:
        await drive(world, world.task(row), ["escalate_order"], "run-1")
        return await drive(world, world.task(row), HAPPY, "run-2")  # a fresh reference

    assert asyncio.run(both()).outcome == "escalated"
    assert money(world, row["order_id"]) == []


@pytest.mark.parametrize("operation", ["assess", "authorize"])
def test_a_rerun_after_a_doubtful_write_moves_no_more_money(operation: str) -> None:
    world, row = World.make(), first_of("clean")
    task = world.task(row)
    ports = world.ports()
    service: Any = ports.fraud if operation == "assess" else ports.payment
    service.faults.inject(operation, FaultKind.TIMEOUT_AFTER, times=1)

    async def both() -> tuple[PlacementResult, PlacementResult]:
        one = await drive(world, task, HAPPY, "run-1", ports)
        return one, await drive(world, task, HAPPY, "run-2")

    one, two = asyncio.run(both())
    assert one.reason == "uncertain_write" and two.outcome == "escalated"
    # The doubtful authorize happened (one effect); nothing captured it later.
    assert money(world, row["order_id"]) == ([] if operation == "assess" else ["authorize"])


def test_a_rerun_of_a_placed_order_writes_nothing_and_reports_placed() -> None:
    world, row = World.make(), first_of("clean")
    task = world.task(row)

    async def both() -> PlacementResult:
        await drive(world, task, HAPPY, "run-1")
        return await drive(world, task, [*HAPPY, "escalate_order"], "run-2")

    before = len(world.payment.effects) + len(world.orders.effects) + len(world.fraud.effects)
    two = asyncio.run(both())
    after = len(world.payment.effects) + len(world.orders.effects) + len(world.fraud.effects)
    assert two.outcome == "placed" and two.reason is None
    assert after - before == 4  # run 1's assess, authorize, capture, confirm only
    assert "order_already_placed" in codes(world, "run-2")
    escalations = [
        e
        for e in world.sink.read_all()
        if e.correlation_id == "run-2" and e.action == "order.escalate"
    ]
    assert escalations == []  # neither the model's nor finish()'s


def test_a_doubtful_escalation_is_never_written_again() -> None:
    world, row = World.make(), first_of("stock_out")
    ports = world.ports()
    ports.orders.faults.inject("escalate", FaultKind.TIMEOUT_AFTER, times=1)  # type: ignore[attr-defined]
    result = asyncio.run(
        drive(
            world,
            world.task(row),
            ["validate_order", "escalate_order", "escalate_order"],
            "run-1",
            ports,
        )
    )
    attempts = [
        e for e in world.sink.read_all() if e.action == "order.escalate" and e.phase == "attempt"
    ]
    assert len(attempts) == 1
    assert result.outcome == "escalated"
    assert result.reason == world.orders.orders[row["order_id"]].escalation == "out_of_stock"


def test_capture_follows_a_later_validation_that_found_an_issue() -> None:
    """F3: with live stock, a second validation can find an issue after authorize."""
    world, row = World.make(), first_of("clean")
    catalog = CatalogState()  # a private table, so the shared one is never edited
    ports = world.ports()
    ports = Ports(
        payment=ports.payment,
        fraud=ports.fraud,
        catalog=CatalogTableFake(state=catalog),
        orders=ports.orders,
    )
    task = world.task(row)
    h = build_handlers(
        task,
        ports,
        world.audit(),
        correlation_id="run-1",
        read_policy=FAST,
        write_policy=FAST_WRITE,
    )

    async def run() -> PlacementResult:
        arg = {"order_id": task.order_id}
        for call in ("validate_order", "assess_fraud", "authorize_payment"):
            await getattr(h, call)(arg)
        line = row["lines"][0]
        key = (line["variant_id"], row["region"])
        catalog.stock[key] = StockLevel(key[0], key[1], 0)
        await h.validate_order(arg)
        await h.capture_payment(arg)
        await h.confirm_order(arg)
        return await finish(h, AgentResult(RunStatus.COMPLETE, (), (), "run-1"))

    result = asyncio.run(run())
    assert "capture" not in money(world, row["order_id"])
    assert result.outcome == "escalated" and result.reason == "out_of_stock"


def test_no_repr_shows_a_confirmation_reference() -> None:
    ref = "cf_ABCDEFGHIJKLMNOP"
    assert ref not in repr(OrderPlacementTask("O000001", ref))
    assert ref not in repr(Confirmation(ref, "O000001", Money(100, "USD")))


# ---- the scoped re-review (`docs/reviews/2026-11-11-BE-05-security-rereview.md`) ------------


def test_a_failed_escalation_still_reports_context() -> None:
    """N1: an escalation that times out before its effect leaves the order pending; the run is
    `failed`, and still reports context built from what it saw."""
    world, row = World.make(), first_of("stock_out")
    ports = world.ports()
    ports.orders.faults.inject("escalate", FaultKind.TIMEOUT_BEFORE)  # type: ignore[attr-defined]
    result = asyncio.run(
        drive(world, world.task(row), ["validate_order", "escalate_order"], "run-1", ports)
    )
    assert result.outcome == "failed"
    assert result.context is not None and result.context["reason"] == "out_of_stock"
    assert result.context["uncertain"] == [
        {"action": "order.escalate", "idempotency_key": f"{row['order_id']}:order.escalate"}
    ]


def test_a_failed_store_read_does_not_stop_the_code_escalation() -> None:
    """N1 (b): finish() still tries to escalate when it can't read the order."""
    world, row = World.make(), first_of("clean")
    ports = world.ports()

    async def run() -> PlacementResult:
        h = build_handlers(
            world.task(row),
            ports,
            world.audit(),
            correlation_id="run-1",
            read_policy=FAST,
            write_policy=FAST_WRITE,
        )
        ports.orders.faults.inject("get", FaultKind.OUTAGE, times=3)  # type: ignore[attr-defined]
        return await finish(h, AgentResult(RunStatus.COMPLETE, (), (), "run-1"))

    result = asyncio.run(run())
    assert result.outcome == "escalated" and result.reason == "not_finished"


def test_no_repr_of_an_order_shows_its_token_or_postal_code() -> None:
    """N2."""
    world, row = World.make(), first_of("address_error")
    order = world.orders.orders[row["order_id"]]
    assert row["payment_token"] not in repr(order)
    assert row["shipping_postal_code"] not in repr(order)


def test_after_a_doubtful_escalation_the_model_is_told_to_stop_not_to_escalate() -> None:
    """N3: no loop of escalate calls telling the model to escalate."""
    from northwind_backend.agents.orders_place import ESCALATION_DOUBTFUL

    world, row = World.make(), first_of("stock_out")
    ports = world.ports()
    ports.orders.faults.inject("escalate", FaultKind.TIMEOUT_AFTER, times=1)  # type: ignore[attr-defined]

    async def run() -> str:
        h = build_handlers(
            world.task(row),
            ports,
            world.audit(),
            correlation_id="run-1",
            read_policy=FAST,
            write_policy=FAST_WRITE,
        )
        arg = {"order_id": row["order_id"]}
        await h.validate_order(arg)
        await h.escalate_order(arg)
        again = await h.escalate_order(arg)
        return str(again["content"][0]["text"])

    text = asyncio.run(run())
    assert ESCALATION_DOUBTFUL.message in text and "Escalate the order" not in text


def test_capture_follows_a_later_validation_that_moved_the_total() -> None:
    """F3's price variant: the authorized amount no longer matches the latest total."""
    world, row = World.make(), first_of("clean")
    catalog = CatalogState()
    base = world.ports()
    ports = Ports(base.payment, base.fraud, CatalogTableFake(state=catalog), base.orders)
    task = world.task(row)
    h = build_handlers(
        task,
        ports,
        world.audit(),
        correlation_id="run-1",
        read_policy=FAST,
        write_policy=FAST_WRITE,
    )

    async def run() -> None:
        arg = {"order_id": task.order_id}
        for call in ("validate_order", "assess_fraud", "authorize_payment"):
            await getattr(h, call)(arg)
        line = row["lines"][0]
        key = (line["variant_id"], row["region"])
        old = catalog.prices[key]
        catalog.prices[key] = type(old)(
            old.variant_id, old.region, old.amount_minor + 1, old.currency
        )
        await h.validate_order(arg)
        await h.capture_payment(arg)

    asyncio.run(run())
    assert "capture" not in money(world, row["order_id"])


def test_a_concurrent_duplicates_hold_is_reported_in_the_escalation() -> None:
    """The re-review's hold variant, interleaved by hand. Run A stops; run B authorizes; run A
    escalates; B's capture is refused. The stored escalation must report B's live authorization.
    Closed by the payment claim (F1, the user's Option 1); the strict xfail it carried until then is
    removed and its assertions are unchanged."""
    world, row = World.make(), first_of("clean")
    task = world.task(row)
    a, b = (
        build_handlers(
            task,
            world.ports(),
            world.audit(),
            correlation_id=f"run-{n}",
            read_policy=FAST,
            write_policy=FAST_WRITE,
        )
        for n in ("a", "b")
    )
    arg = {"order_id": task.order_id}

    async def interleave() -> None:
        await a.validate_order(arg)
        await a.assess_fraud(arg)
        await b.validate_order(arg)
        await b.assess_fraud(arg)
        await b.authorize_payment(arg)
        await a.escalate_order(arg)
        await b.capture_payment(arg)

    asyncio.run(interleave())
    live = [i for i in world.payment.intents.values() if i.order_id == task.order_id]
    assert live and "capture" not in money(world, task.order_id)  # the hold is real
    stored = world.orders.escalations[task.order_id]
    assert stored["payment"] is not None, "the escalation doesn't mention the live authorization"


def test_a_run_identity_never_looks_like_card_data() -> None:
    """Found while fixing F1: a hex run identity held 13 digits in a row in about 1 run in 40, the
    card-data screen refused its claim, and the order ended `failed`."""
    from northwind_backend.audit import card_data

    world, row = World.make(), first_of("clean")
    for n in range(2_000):
        h = build_handlers(world.task(row), world.ports(), world.audit(), correlation_id=f"r-{n}")
        assert not card_data(h.run_id), h.run_id
