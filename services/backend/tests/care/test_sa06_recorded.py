"""Row #23 (SA-06) on the real model (Haiku writes the summary), recorded once in the user's
`backend-care` window (phase C) and replayed after (decision 0013; the user's change 4).

Each test's planned outcome is fixed here before the window: a structured case with a model
summary. A recorded outcome that does not match is stop 7, never a re-record loop and never an edit
of the expectation (stop 5). After about 2026-12-22, a transcript that was not recorded is a
cassette miss, so the Care run fails and the case is queued with `summary_missing`, by design
(decision 0025).

Synthetic transcripts and fixture order IDs only. The demo handoff (`DEMO_TURNS`) is fixed text, so
its summary replays."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.agents.care import CareTask, check_summary, run_summary
from northwind_backend.agents.escalation import RECOMMENDED
from northwind_backend.audit import AuditContext
from northwind_backend.errors import Ok
from northwind_backend.fakes.handoffs import HandoffQueueFake, HandoffState
from northwind_backend.handoff import (
    HANDOFF_ACTIONS,
    SHOPPER_REQUESTED,
    CartLine,
    CartSnapshot,
    HandoffCase,
    HandoffDesk,
    HandoffRequest,
    Identity,
    missing_for_staff,
)
from northwind_common.llm import agent_runtime
from northwind_llm_replay.pytest_plugin import LLMProxyHandle

from ._driver import POLICY, World, first_of, fixed_clock, transcript

pytestmark = [pytest.mark.req("SA-06"), pytest.mark.llm_suite("backend-care")]

SESSION = Identity("session", "mnopabcdmnopabcdmnopabcdmnopabcd", "US")
CART = CartSnapshot((CartLine("P0102-HGY-REG-M", 1, 8999),), 8999, "USD")
# The fixed demo handoff (the user's change 4): the same shopper words with an order and without.
DEMO_TURNS = (
    "Which of your rain jackets packs smallest for hiking?",
    "I'm an AI assistant. The sources list two packable shells; I can compare them if you like.",
    "Honestly I'd rather talk to a person about this, can you connect me?",
)


def harness(proxy: LLMProxyHandle, root: Path, world: World) -> tuple[HandoffDesk, HandoffState]:
    state = HandoffState()
    audit = AuditContext(
        world.sink, correlation_id="gw-rec", actor="test-gateway", clock=fixed_clock
    )
    runs = iter(range(1, 100))
    desk = HandoffDesk(
        queue=HandoffQueueFake(state=state),
        care=world.ports(),
        audit=audit,
        runtime=lambda: agent_runtime(base_url=proxy.base_url, root=root / f"rt{next(runs)}"),
        policy=POLICY,
    )
    return desk, state


def handed_off(desk: HandoffDesk, state: HandoffState, request: HandoffRequest) -> HandoffCase:
    async def main() -> Any:
        receipt = await desk.request(request)
        await desk.settle()
        return receipt

    receipt = asyncio.run(main())
    assert isinstance(receipt, Ok)
    case = state.cases[receipt.value.reference]
    assert missing_for_staff(case) == []
    return case


def no_transcript_in(case: HandoffCase, turns: tuple[str, ...]) -> None:
    body = json.dumps(case.to_dict())
    assert not any(t in body for t in turns if len(t) > 20)


def test_rec_sa06_1_run_summary_with_no_order(llm_proxy: LLMProxyHandle, tmp_path: Path) -> None:
    task = CareTask(None, transcript(*DEMO_TURNS))
    audit = AuditContext(
        World.make().sink, correlation_id="staff-rec", actor="test-staff", clock=fixed_clock
    )
    result = asyncio.run(
        run_summary(
            task,
            audit=audit,
            correlation_id="run-sum-rec1",
            runtime=agent_runtime(base_url=llm_proxy.base_url, root=tmp_path / "rt1"),
            policy=POLICY,
        )
    )
    assert result.summary is not None, result.failures
    assert check_summary(result.summary) == result.summary


def test_rec_sa06_2_demo_handoff_without_an_order(
    llm_proxy: LLMProxyHandle, tmp_path: Path
) -> None:
    world = World.make()
    desk, state = harness(llm_proxy, tmp_path, world)
    case = handed_off(
        desk, state, HandoffRequest(SESSION, "message", transcript(*DEMO_TURNS), CART)
    )
    assert case.summary_source == "model" and "summary_missing" not in case.flags
    assert case.root_cause == SHOPPER_REQUESTED
    assert case.intent == "product_question"
    assert case.recommended_action == HANDOFF_ACTIONS["product_question"]
    assert case.amounts.source == "cart" and case.amounts.cart == CART
    no_transcript_in(case, DEMO_TURNS)


def test_rec_sa06_3_demo_handoff_with_the_shoppers_own_order(
    llm_proxy: LLMProxyHandle, tmp_path: Path
) -> None:
    world = World.make()
    order = first_of("fraud_flag", skip=15)
    world.escalate(order, "fraud_review")
    me = Identity("shopper", order["shopper_id"], order["region"])
    turns = (f"My order {order['order_id']} has been on hold for days.", *DEMO_TURNS[1:])
    desk, state = harness(llm_proxy, tmp_path, world)
    case = handed_off(desk, state, HandoffRequest(me, "message", transcript(*turns)))
    assert case.order_id == order["order_id"] and case.order_case_id is not None
    assert case.root_cause == "fraud_review"
    assert case.recommended_action == RECOMMENDED["fraud_review"]
    assert case.amounts.source == "order" and case.summary_source == "model"
    [be07] = world.case_state.cases
    assert be07.case_id == case.order_case_id and "transcript_unverified" in be07.flags
    no_transcript_in(case, turns)
