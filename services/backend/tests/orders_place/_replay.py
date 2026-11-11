"""The backend replay's checks, shared by the recorded run and the rule-driven run (row #21).

Fixed in the plan before recording: a mismatch after recording is stop 7, never a re-record loop
and never an edited threshold (stop 5)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from northwind_backend.agents.orders_place import RECOMMENDED, PlacementResult
from northwind_backend.audit import AuditEvent, verify_chain
from northwind_backend.ports import OrderStatus, PaymentStatus
from northwind_backend.reconstruct import reconstruct_order

from ._placement import KINDS, World, orders, total_for

CONCURRENCY = 3
TOUCHLESS_TARGET = 0.90
CASSETTES = Path(__file__).resolve().parents[4] / "cassettes" / "backend-replay"
DATA = Path(__file__).resolve().parents[4] / "data" / "backend"
CONTEXT_KEYS = {
    "order_id",
    "shopper_id",
    "reason",
    "recommended_action",
    "issues",
    "fraud",
    "total",
    "payment",
    "error_codes",
    "uncertain",
    "steps_done",
    "failures",
    "correlation_id",
}
# What must never be in a cassette (the model's input and output): tokens, confirmation references,
# postal codes, shopper IDs, emails and names.
FORBIDDEN = [
    re.compile(r"tok_sandbox"),
    re.compile(r"cf_[A-Z]{16}"),
    re.compile(r"\b(?:US|CA|UK|EU|XX)-\d{4}\b"),
    re.compile(r"\bS\d{4}\b"),
    re.compile(r"@example\.com"),
    # Security review F5: no intent, assessment or correlation ID either.
    re.compile(r"\bpi_\d{6}\b"),
    re.compile(r"\bfa_\d{6}\b"),
    re.compile(r"\brun-O\d{6}"),
    re.compile(r"\bcheckout-\d"),
]


def _shopper_text() -> list[str]:
    """Every shopper's name and street line (F5): none may reach the model."""
    with (DATA / "shoppers.jsonl").open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return sorted({r["name"] for r in rows} | {r["address"]["line1"] for r in rows})


SHOPPER_TEXT = _shopper_text()


def replay_slice() -> int:
    raw = os.environ.get("BACKEND_REPLAY_SLICE", "")
    if raw and not raw.isdigit():
        raise ValueError("BACKEND_REPLAY_SLICE must be digits")
    return int(raw) if raw else len(orders())


def labels() -> dict[str, dict[str, Any]]:
    with (DATA / "replay_labels.jsonl").open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return {row["order_id"]: row for row in rows}


def kind_of(row: dict[str, Any]) -> str:
    return row["exception"] or "clean"


Place = Callable[[World, dict[str, Any], str, str], Awaitable[PlacementResult]]


async def run_all(
    world: World, rows: list[dict[str, Any]], place: Place, runs_per_order: int = 1
) -> dict[str, list[PlacementResult]]:
    """`place(world, row, ref, correlation_id)`; at most CONCURRENCY at once, each order's runs in
    sequence. One confirmation per order, as the shopper confirms once."""
    refs = {row["order_id"]: world.confirm(row) for row in rows}
    gate = asyncio.Semaphore(CONCURRENCY)
    results: dict[str, list[PlacementResult]] = {oid: [] for oid in refs}

    async def one(row: dict[str, Any]) -> None:
        oid = row["order_id"]
        for n in range(runs_per_order):
            async with gate:
                result = await place(world, row, refs[oid], f"run-{oid}-{n}")
            results[oid].append(result)

    await asyncio.gather(*(one(row) for row in rows))
    return results


def assert_context(result: PlacementResult, oid: str) -> None:
    context = result.context
    assert context is not None, oid
    assert set(context) == CONTEXT_KEYS, oid
    assert context["order_id"] == oid and context["reason"] == result.reason, oid
    assert context["reason"] in RECOMMENDED and context["recommended_action"], oid
    assert context["shopper_id"], oid


def assert_money(world: World, row: dict[str, Any], placed: bool) -> None:
    oid = row["order_id"]
    intents = [i for i in world.payment.intents.values() if i.order_id == oid]
    live = [i for i in intents if i.status is not PaymentStatus.VOIDED]
    assert len(live) <= 1, oid
    captures = Counter(e.reference for e in world.payment.effects if e.operation == "capture")
    assert all(captures[i.intent_id] <= 1 for i in intents), oid
    order = world.orders.orders[oid]
    confirms = [e for e in world.orders.effects if e.operation == "confirm" and e.reference == oid]
    assert len(confirms) <= 1, oid
    if not placed:
        # Security review F1: an order that wasn't placed has nothing captured, whoever escalated.
        assert all(i.captured_minor == 0 for i in intents), oid
    if placed:
        assert order.status is OrderStatus.CONFIRMED, oid
        assert live[0].captured_minor == order.amount_minor == total_for(row).amount_minor, oid


def _per_run(events: tuple[AuditEvent, ...]) -> dict[str, list[AuditEvent]]:
    grouped: dict[str, list[AuditEvent]] = {}
    for event in events:
        grouped.setdefault(event.correlation_id, []).append(event)
    return grouped


def check_replay(
    world: World,
    rows: list[dict[str, Any]],
    results: dict[str, list[PlacementResult]],
    seconds: float,
) -> dict[str, Any]:
    """Checks 1 to 5 of the plan's §F; returns the report. Check 6 is the cassettes'."""
    expected = labels()
    by_kind: dict[str, Counter[str]] = {kind: Counter() for kind in KINDS}
    for row in rows:
        oid = row["order_id"]
        (result,) = results[oid]
        by_kind[kind_of(row)][result.outcome] += 1
        label = expected[oid]
        if result.outcome == "escalated":  # 2
            assert_context(result, oid)
        if label["escalation"]:  # 3
            assert result.outcome == "escalated", (oid, label["exception"], result.reason)
            assert not [i for i in world.payment.intents.values() if i.order_id == oid], oid
        assert_money(world, row, placed=result.outcome == "placed")  # 4

    placed = sum(c["placed"] for c in by_kind.values())
    touchless = placed / len(rows)
    report = {
        "orders": len(rows),
        "placed": placed,
        "touchless": round(touchless, 4),
        "by_kind": {kind: dict(counts) for kind, counts in by_kind.items()},
        "label_agreement": round(
            sum(
                results[r["order_id"]][0].outcome == expected[r["order_id"]]["expected_outcome"]
                for r in rows
            )
            / len(rows),
            4,
        ),
        "escalation_reasons": dict(
            sorted(Counter(r.reason for rs in results.values() for r in rs if r.reason).items())
        ),
        "seconds": round(seconds, 1),
    }
    out = os.environ.get("BACKEND_REPLAY_REPORT")
    if out:
        Path(out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert touchless >= TOUCHLESS_TARGET, report  # 1

    events = world.sink.read_all()  # 5
    assert verify_chain(events) == []
    per_run = _per_run(events)
    for row in rows:
        oid = row["order_id"]
        mine = tuple(e for cid, es in per_run.items() if cid.startswith(f"run-{oid}-") for e in es)
        record = reconstruct_order(mine, oid)
        order = world.orders.orders[oid]
        assert record.order_status == order.status.value, oid
        if order.status is OrderStatus.CONFIRMED:
            assert record.payment_status == "captured", oid
            assert record.captured_minor == order.amount_minor, oid
        assert record.uncertain == (), oid
    return report


def check_cassettes() -> None:
    """6: no token, reference, postal code, shopper, intent, assessment or correlation ID,
    email, name or street line in any cassette."""
    for path in sorted(CASSETTES.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN:
            assert pattern.search(text) is None, (path.name, pattern.pattern)
        for value in SHOPPER_TEXT:
            assert value not in text, path.name
