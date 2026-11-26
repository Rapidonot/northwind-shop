"""M12 (row #25, BE-05): `validate_order` reports `invalid_line` for an order line with a quantity
below 1 or a variant on two lines. Such an order is escalated with reason `invalid_line` and never
authorized or captured. Two lines for one variant that each fit stock but overflow when summed must
not pass. Handlers are driven directly; no model, no spend."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest

from northwind_backend.agents.orders_place import PlacementResult, build_handlers, finish
from northwind_backend.errors import AgentResult, RunStatus
from northwind_backend.ports import OrderLine, OrderStatus
from northwind_backend.recovery import RetryPolicy

from ._placement import World, catalog_state, first_of, orders

pytestmark = pytest.mark.req("BE-05")

FAST = RetryPolicy(max_attempts=3, deadline_seconds=5.0, backoff_seconds=())
MONEY_PATH = ["validate_order", "assess_fraud", "authorize_payment", "capture_payment"]


def _with_lines(world: World, row: dict[str, Any], lines: list[tuple[str, int]]) -> None:
    """Replace the stored order's lines. Product IDs are the variants' own."""
    order = world.orders.orders[row["order_id"]]
    world.orders.orders[row["order_id"]] = replace(
        order, lines=tuple(OrderLine(v.split("-")[0], v, q) for v, q in lines)
    )


def _run(
    world: World, row: dict[str, Any], calls: list[str]
) -> tuple[list[dict[str, Any]], PlacementResult]:
    task = world.task(row)

    async def go() -> tuple[list[dict[str, Any]], PlacementResult]:
        handlers = build_handlers(
            task, world.ports(), world.audit(), correlation_id="run-1", read_policy=FAST
        )
        replies = [await getattr(handlers, c)({"order_id": task.order_id}) for c in calls]
        done = await finish(handlers, AgentResult(RunStatus.COMPLETE, (), (), "run-1"))
        return replies, done

    return asyncio.run(go())


def _body(reply: dict[str, Any]) -> dict[str, Any]:
    """The `value` of an Ok tool reply: {"issues": [...], "lines": n}."""
    body: dict[str, Any] = json.loads(reply["content"][0]["text"])
    assert body["isError"] is False, body
    value: dict[str, Any] = body["value"]
    return value


def _clean() -> tuple[World, dict[str, Any], str, int]:
    """A clean order, with its first line's variant and that variant's stock in the region."""
    world, row = World.make(), first_of("clean")
    variant = row["lines"][0]["variant_id"]
    on_hand = catalog_state().stock[(variant, row["region"])].on_hand
    assert on_hand >= 1
    return world, row, variant, on_hand


def _assert_escalated_invalid_line(
    world: World, row: dict[str, Any], result: PlacementResult
) -> None:
    assert result.outcome == "escalated" and result.reason == "invalid_line"
    stored = world.orders.orders[row["order_id"]]
    assert stored.status is OrderStatus.ESCALATED and stored.escalation == "invalid_line"
    assert result.context is not None and result.context["order_id"] == row["order_id"]
    # Never authorized, never captured, never confirmed: no money effect of any kind.
    assert world.payment.effects == [] and world.payment.intents == {}
    assert world.orders.orders[row["order_id"]].intent_id is None


# ---- the control ---------------------------------------------------------------------------


def test_a_clean_order_with_distinct_variants_and_positive_quantities_has_no_issue() -> None:
    world, row = World.make(), first_of("clean")
    replies, _ = _run(world, row, ["validate_order"])
    assert _body(replies[0])["issues"] == []


# ---- quantity below 1 ----------------------------------------------------------------------


@pytest.mark.parametrize("quantity", [0, -1, -50])
def test_a_line_with_a_quantity_below_one_is_invalid_line_and_escalated(quantity: int) -> None:
    world, row, variant, _ = _clean()
    _with_lines(world, row, [(variant, quantity)])
    replies, result = _run(world, row, ["validate_order", "escalate_order"])
    assert _body(replies[0])["issues"] == ["invalid_line"]
    _assert_escalated_invalid_line(world, row, result)


def test_a_negative_line_cannot_lower_the_total_of_a_valid_one() -> None:
    world, row, variant, _ = _clean()
    other = row["lines"][1]["variant_id"]
    _with_lines(world, row, [(variant, 1), (other, -3)])
    replies, result = _run(world, row, [*MONEY_PATH, "escalate_order"])
    assert "invalid_line" in _body(replies[0])["issues"]
    _assert_escalated_invalid_line(world, row, result)


def test_a_bad_line_among_good_ones_makes_the_order_invalid() -> None:
    world, row, variant, _ = _clean()
    others = [line["variant_id"] for line in row["lines"][1:]]
    _with_lines(world, row, [(variant, 1), (others[0], 0)])
    replies, _ = _run(world, row, ["validate_order"])
    assert _body(replies[0])["issues"] == ["invalid_line"]


# ---- a variant on two lines ----------------------------------------------------------------


def test_a_variant_on_two_lines_is_invalid_line_and_escalated() -> None:
    world, row, variant, _ = _clean()
    _with_lines(world, row, [(variant, 1), (variant, 1)])
    replies, result = _run(world, row, ["validate_order", "escalate_order"])
    assert _body(replies[0])["issues"] == ["invalid_line"]
    _assert_escalated_invalid_line(world, row, result)


def test_duplicate_lines_that_each_fit_stock_but_overflow_when_summed_do_not_pass() -> None:
    world, row, variant, on_hand = _clean()
    _with_lines(world, row, [(variant, on_hand), (variant, on_hand)])  # each fits; the sum doesn't
    replies, result = _run(world, row, [*MONEY_PATH, "confirm_order", "escalate_order"])
    issues = _body(replies[0])["issues"]
    assert "invalid_line" in issues
    _assert_escalated_invalid_line(world, row, result)


def test_duplicate_lines_that_fit_when_summed_are_still_invalid() -> None:
    world, row, variant, on_hand = _clean()
    assert on_hand >= 2
    _with_lines(world, row, [(variant, 1), (variant, 1)])
    replies, result = _run(world, row, [*MONEY_PATH, "confirm_order", "escalate_order"])
    assert _body(replies[0])["issues"] == ["invalid_line"]
    _assert_escalated_invalid_line(world, row, result)


def test_the_invalid_line_reason_is_code_owned_not_the_models() -> None:
    world, row, variant, _ = _clean()
    _with_lines(world, row, [(variant, 0)])
    task = world.task(row)

    async def go() -> PlacementResult:
        handlers = build_handlers(
            task, world.ports(), world.audit(), correlation_id="run-1", read_policy=FAST
        )
        await handlers.validate_order({"order_id": task.order_id})
        await handlers.escalate_order({"order_id": task.order_id, "reason": "all_good"})
        return await finish(handlers, AgentResult(RunStatus.COMPLETE, (), (), "run-1"))

    result = asyncio.run(go())
    assert result.reason == "invalid_line"
    assert world.orders.orders[row["order_id"]].escalation == "invalid_line"


# ---- the synthetic orders ------------------------------------------------------------------


def test_no_synthetic_order_has_a_duplicate_variant_or_a_quantity_below_one() -> None:
    rows = orders()
    assert len(rows) > 0
    for order in rows:
        variants = [line["variant_id"] for line in order["lines"]]
        assert len(set(variants)) == len(variants), f"{order['order_id']}: a variant on two lines"
        assert all(
            isinstance(line["quantity"], int) and not isinstance(line["quantity"], bool)
            for line in order["lines"]
        )
        assert all(line["quantity"] >= 1 for line in order["lines"]), (
            f"{order['order_id']}: a quantity below 1"
        )
