"""Test-only helpers for row #24 (BE-01). Not product code; nothing under `src` imports this.

A world is the three fakes seeded from `data/backend/`, plus, for the chosen order, a payment that
was authorized and captured and a fraud assessment, set up straight on the fakes as the portal and
the placement flow would have left them. Amounts are test figures, not prices."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import Any

from northwind_backend.agents.orders_check import FRAUD_TOOL_NAME, PAYMENT_TOOL_NAME
from northwind_backend.agents.returns_track import TRACK_TOOL_NAME
from northwind_backend.audit import AuditEvent, InMemoryAuditSink
from northwind_backend.carriers import tracking_number_for
from northwind_backend.errors import Ok
from northwind_backend.orchestrator import Ports, orchestrate
from northwind_backend.orchestrator.__main__ import fixture_ports
from northwind_backend.orchestrator.aggregate import OrchestratorResult
from northwind_backend.orchestrator.plan import PLAN_TOOL_NAME
from northwind_backend.orchestrator.run import new_run_id
from northwind_backend.ports import Money, PaymentIntent
from northwind_backend.recovery import RetryPolicy
from northwind_common.llm import AgentRuntime, agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM

DATA = Path(__file__).resolve().parents[4] / "data" / "backend"
FIXED_CLOCK = "2026-11-23T00:00:00+00:00"
POLICY = RetryPolicy(max_attempts=3, deadline_seconds=2.0, backoff_seconds=())
CURRENCY = {"US": "USD", "CA": "CAD", "UK": "GBP", "EU": "EUR"}
# Route markers: each agent's system prompt opens with its own name.
PLANNER = "You are the Orchestrator planner"
ORDERS = "You are the Orders subagent"
RETURNS = "You are the Returns subagent"


def fixed_clock() -> str:
    return FIXED_CLOCK


def load_jsonl(name: str) -> list[dict[str, Any]]:
    with (DATA / name).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def scanned_clean_order() -> dict[str, Any]:
    """The first order with no planted exception whose parcel has at least one scan."""
    scanned = {row["order_id"] for row in load_jsonl("tracking.jsonl") if row["events"]}
    return next(
        row
        for row in load_jsonl("orders.jsonl")
        if row["exception"] is None and row["order_id"] in scanned
    )


@dataclass
class World:
    ports: Ports
    order: dict[str, Any]
    intent_id: str
    sink: InMemoryAuditSink = field(default_factory=InMemoryAuditSink)

    @property
    def order_id(self) -> str:
        return str(self.order["order_id"])

    @property
    def tracking_number(self) -> str:
        return tracking_number_for(self.order_id)

    @classmethod
    def make(cls, order: dict[str, Any] | None = None) -> World:
        order = order or scanned_clean_order()
        ports = fixture_ports(DATA)
        order_id = order["order_id"]
        assessed = ports.fraud.assess(
            idempotency_key=f"{order_id}:assess", order_id=order_id, shopper_id=order["shopper_id"]
        )
        assert isinstance(assessed, Ok), assessed
        return cls(ports, order, capture_payment(ports, order))

    def request(self, *, refund: bool = False) -> str:
        text = (
            f"Order {self.order_id}, payment {self.intent_id}: was it charged, and did the fraud "
            "check pass? Where is its parcel now?"
        )
        return text + (" Also please refund it." if refund else "")

    def events(self) -> tuple[AuditEvent, ...]:
        return self.sink.read_all()


def capture_payment(ports: Ports, order: dict[str, Any]) -> str:
    """Authorize and capture a test amount for `order` straight on the fake, as the portal and the
    placement flow would have; returns the intent ID."""
    order_id = order["order_id"]
    amount = Money(5_000, CURRENCY[order["region"]])
    payment: Any = ports.payment  # the fake, with its state
    authorized = payment.authorize(
        idempotency_key=f"{order_id}:authorize",
        payment_token=order["payment_token"],
        amount=amount,
        order_id=order_id,
        confirmation_ref=payment.state.confirmations.issue(order_id, amount),
    )
    assert isinstance(authorized, Ok), authorized
    intent: PaymentIntent = authorized.value
    captured = payment.capture(
        idempotency_key=f"{order_id}:capture",
        intent_id=intent.intent_id,
        confirmation_ref=payment.state.confirmation_of(intent.intent_id),
    )
    assert isinstance(captured, Ok), captured
    return intent.intent_id


def runtimes(root: Path, url: str) -> Iterator[AgentRuntime]:
    for n in count(1):
        yield agent_runtime(base_url=url, root=root / f"rt{n}")


def run(
    world: World,
    request: str,
    llm: ScriptedLLM,
    root: Path,
    *,
    run_id: str | None = None,
    **kwargs: Any,
) -> OrchestratorResult:
    made = runtimes(root, llm.url)
    return asyncio.run(
        orchestrate(
            request,
            ports=world.ports,
            sink=world.sink,
            runtime=lambda: next(made),
            policy=POLICY,
            run_id=run_id or new_run_id(),
            clock=fixed_clock,
            **kwargs,
        )
    )


def script_planner(llm: ScriptedLLM, concerns: list[dict[str, Any]]) -> None:
    route = llm.route(PLANNER)
    route.reply_tool_use(PLAN_TOOL_NAME, {"concerns": concerns}, tool_id="toolu_plan")
    route.reply("planned")


def script_orders(llm: ScriptedLLM, world: World, with_intent: bool = True) -> None:
    route = llm.route(ORDERS)
    route.reply_tool_use(FRAUD_TOOL_NAME, {"order_id": world.order_id}, tool_id="toolu_fraud")
    if with_intent:
        route.reply_tool_use(PAYMENT_TOOL_NAME, {"intent_id": world.intent_id}, tool_id="toolu_pay")
    route.reply("Captured, and the fraud check approved it.")


def script_returns(llm: ScriptedLLM, world: World, number: str | None = None) -> None:
    route = llm.route(RETURNS)
    route.reply_tool_use(
        TRACK_TOOL_NAME, {"tracking_number": number or world.tracking_number}, tool_id="toolu_track"
    )
    route.reply("The parcel has been scanned.")


def full_plan(world: World, *, refund: bool = False) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = [
        {"kind": "payment_fraud", "order_id": world.order_id, "intent_id": world.intent_id},
        {"kind": "tracking", "order_id": world.order_id},
    ]
    if refund:
        plan.append({"kind": "unsupported", "topic": "refund_request", "order_id": world.order_id})
    return plan
