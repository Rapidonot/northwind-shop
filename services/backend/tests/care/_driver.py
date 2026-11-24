"""Test-only helpers for row #26 (BE-07). Not product code; nothing under `src` imports this.

A world is the order store seeded from `data/backend/` and an empty case queue (no catalog: the
Care agent reads no prices, the user's decision on row #26). An order is put in the state the
Orders agent would have left it in straight on the fakes: escalated with a reason, and for some,
a payment reported on its claim. Amounts on a claim are test
figures, not prices."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from northwind_backend.agents.care import (
    ORDER_TOOL_NAME,
    SUMMARY_TOOL_NAME,
    CarePorts,
    CareResult,
    CareTask,
    Role,
    Turn,
    run_care,
)
from northwind_backend.audit import AuditContext, AuditEvent, InMemoryAuditSink
from northwind_backend.cases import CaseLine
from northwind_backend.errors import Ok
from northwind_backend.fakes.cases import CaseQueueFake, CaseState
from northwind_backend.fakes.orders import OrderState, OrderStoreFake
from northwind_backend.ports import PaymentIntent, PaymentStatus
from northwind_backend.recovery import RetryPolicy
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM

DATA = Path(__file__).resolve().parents[4] / "data" / "backend"
FIXED_CLOCK = "2026-11-24T00:00:00+00:00"
POLICY = RetryPolicy(max_attempts=3, deadline_seconds=2.0, backoff_seconds=())
CURRENCY = {"US": "USD", "CA": "CAD", "UK": "GBP", "EU": "EUR"}
TEST_PAN = "4111 1111 1111 1111"  # the public sandbox test number


def fixed_clock() -> str:
    return FIXED_CLOCK


@cache
def orders() -> tuple[dict[str, Any], ...]:
    with (DATA / "orders.jsonl").open(encoding="utf-8") as handle:
        return tuple(json.loads(line) for line in handle if line.strip())


def first_of(exception: str | None, skip: int = 0) -> dict[str, Any]:
    return [o for o in orders() if o["exception"] == exception][skip]


def lines_of(order: dict[str, Any]) -> tuple[CaseLine, ...]:
    return tuple(CaseLine(line["variant_id"], line["quantity"]) for line in order["lines"])


@dataclass
class World:
    order_state: OrderState
    case_state: CaseState = field(default_factory=CaseState)
    sink: InMemoryAuditSink = field(default_factory=InMemoryAuditSink)
    order_fake: OrderStoreFake | None = None
    case_fake: CaseQueueFake | None = None

    @classmethod
    def make(cls) -> World:
        world = cls(OrderState.from_data(DATA))
        world.order_fake = OrderStoreFake(state=world.order_state)
        world.case_fake = CaseQueueFake(state=world.case_state)
        return world

    def ports(self) -> CarePorts:
        assert self.order_fake and self.case_fake
        return CarePorts(orders=self.order_fake, cases=self.case_fake)

    def escalate(self, order: dict[str, Any], reason: str) -> None:
        assert self.order_fake is not None
        done = self.order_fake.escalate(
            idempotency_key=f"{order['order_id']}:escalate",
            order_id=order["order_id"],
            reason=reason,
            context={"reason": reason},
        )
        assert isinstance(done, Ok), done

    def report_capture(
        self, order: dict[str, Any], captured_minor: int, currency: str | None = None
    ) -> None:
        """The claim and its captured payment, as the placement run would have reported it."""
        assert self.order_fake is not None
        oid = order["order_id"]
        run_id = f"run-{oid}-place"
        claimed = self.order_fake.claim_payment(order_id=oid, run_id=run_id)
        assert isinstance(claimed, Ok), claimed
        intent = PaymentIntent(
            intent_id="pi_000901",
            order_id=oid,
            status=PaymentStatus.CAPTURED,
            currency=currency or CURRENCY[order["region"]],
            authorized_minor=captured_minor,
            captured_minor=captured_minor,
        )
        reported = self.order_fake.claim_payment(order_id=oid, run_id=run_id, payment=intent)
        assert isinstance(reported, Ok), reported

    def events(self) -> tuple[AuditEvent, ...]:
        return self.sink.read_all()


def transcript(*texts: str) -> tuple[Turn, ...]:
    roles: tuple[Role, Role] = ("shopper", "assistant")
    return tuple(Turn(roles[i % 2], text) for i, text in enumerate(texts))


def plain_transcript() -> tuple[Turn, ...]:
    return transcript(
        "My order still says it is on hold and nobody has told me why.",
        "I'm an AI assistant. I can't see the reason, so I'm passing this to a person.",
        "Please sort it out, I need it by next week.",
    )


def script_care(
    llm: ScriptedLLM,
    order_id: str,
    *summaries: str,
    ask_order: str | None = None,
) -> None:
    """One `get_order`, then each summary in turn (a refused one is retried with the next)."""
    llm.reply_tool_use(ORDER_TOOL_NAME, {"order_id": ask_order or order_id}, tool_id="toolu_o1")
    if ask_order is not None and ask_order != order_id:
        llm.reply_tool_use(ORDER_TOOL_NAME, {"order_id": order_id}, tool_id="toolu_o2")
    for n, summary in enumerate(summaries, start=1):
        llm.reply_tool_use(SUMMARY_TOOL_NAME, {"summary": summary}, tool_id=f"toolu_s{n}")
    llm.reply("Summary submitted.")


def run(
    world: World,
    task: CareTask,
    llm: ScriptedLLM,
    root: Path,
    *,
    correlation_id: str = "run-care-test",
) -> CareResult:
    audit = AuditContext(
        world.sink, correlation_id="staff-test", actor="test-staff", clock=fixed_clock
    )
    return asyncio.run(
        run_care(
            task,
            ports=world.ports(),
            audit=audit,
            correlation_id=correlation_id,
            runtime=agent_runtime(base_url=llm.url, root=root / correlation_id),
            policy=POLICY,
        )
    )
