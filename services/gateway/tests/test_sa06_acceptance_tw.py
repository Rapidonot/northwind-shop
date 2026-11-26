"""POST /api/handoff end to end on the real desk and a scripted model (row #23, SA-06, NFR-08;
hard rules 1, 7, 8, 9, 10). Written from the criteria: the case that staff get has identity, intent,
root cause, amounts and recommended action; a repeat press is the same handoff; the shopper is told
a person takes over while still told they are talking to an AI."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import northwind_gateway.handoff as handoff_module
from northwind_assistant.caller import Caller
from northwind_backend.agents.care import SUMMARY_TOOL_NAME, CarePorts
from northwind_backend.audit import AuditContext, InMemoryAuditSink
from northwind_backend.fakes.cases import CaseQueueFake, CaseState
from northwind_backend.fakes.handoffs import HandoffQueueFake, HandoffState
from northwind_backend.fakes.orders import DATA, OrderState, OrderStoreFake
from northwind_backend.handoff import INTENTS, HandoffDesk, missing_for_staff
from northwind_backend.recovery import RetryPolicy
from northwind_common.llm import agent_runtime
from northwind_common.mcp_token import KEY_ENV
from northwind_gateway.app import app
from northwind_gateway.handoff import NOTICE, cart_reader, handoff_desk
from northwind_gateway.handoff_trigger import asks_for_person
from northwind_gateway.settings import shopper_caller
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_catalog import testing as catalog_testing

pytestmark = [pytest.mark.req("SA-06"), pytest.mark.req("NFR-08")]

SESSION = "fedcba9876543210fedcba9876543210"
TEST_PAN = "4111 1111 1111 1111"  # the public sandbox test number
SUMMARY = "The shopper asks for a person about a jacket."
CART_VIEW = {
    "lines": [{"variant_id": "P0102-HGY-REG-M", "quantity": 2, "line_total": "179.98"}],
    "subtotal": "179.98",
    "currency": "USD",
}
ENVELOPE_KEYS = {"isError", "errorCategory", "isRetryable", "message"}


class Cart:
    async def read(self, caller: Caller) -> Any:
        return CART_VIEW


class Rig:
    def __init__(self, llm: ScriptedLLM, root: Path) -> None:
        self.state = HandoffState()
        self.cases = CaseState()
        self.runs = 0
        care = CarePorts(
            orders=OrderStoreFake(state=OrderState.from_data(DATA)),
            cases=CaseQueueFake(state=self.cases),
        )

        def runtime() -> Any:
            self.runs += 1
            return agent_runtime(base_url=llm.url, root=root / f"rt{self.runs}")

        self.desk = HandoffDesk(
            queue=HandoffQueueFake(state=self.state),
            care=care,
            audit=AuditContext(InMemoryAuditSink(), correlation_id="gw", actor="shopper-portal"),
            runtime=runtime,
            policy=RetryPolicy(max_attempts=2, deadline_seconds=2.0, backoff_seconds=()),
        )

    def post(self, caller: Caller, *bodies: dict[str, Any]) -> list[httpx.Response]:
        app.dependency_overrides[handoff_desk] = lambda: self.desk
        app.dependency_overrides[cart_reader] = lambda: Cart()
        app.dependency_overrides[shopper_caller] = lambda: caller

        async def main() -> list[httpx.Response]:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                out = [await client.post("/api/handoff", json=b) for b in bodies]
            await self.desk.settle()
            return out

        try:
            return asyncio.run(main())
        finally:
            app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, catalog_testing.KEY.decode())


def turns(*texts: str) -> list[dict[str, str]]:
    roles = ("shopper", "assistant")
    return [{"role": roles[i % 2], "text": t} for i, t in enumerate(texts)]


def script_summary(llm: ScriptedLLM) -> None:
    llm.reply_tool_use(SUMMARY_TOOL_NAME, {"summary": SUMMARY}, tool_id="toolu_1")
    llm.reply("Done.")


def test_the_queued_case_has_the_five_acceptance_fields_and_the_reply_has_none_of_them(
    tmp_path: Path,
) -> None:
    with ScriptedLLM() as llm:
        script_summary(llm)
        rig = Rig(llm, tmp_path)
        [response] = rig.post(
            Caller(region="US", session=SESSION),
            {"turns": turns("is this jacket warm? get me a person", "I'm an AI.")},
        )
    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"status", "reference", "notice"} and body["status"] == "queued"
    [case] = rig.state.cases.values()
    assert case.reference == body["reference"]
    payload = case.to_dict()
    for name in ("identity", "intent", "root_cause", "amounts", "recommended_action"):
        assert payload[name], name
    assert payload["intent"] in INTENTS and payload["amounts"]["source"] == "cart"
    assert missing_for_staff(case) == []
    assert SESSION not in json.dumps(payload) and SESSION not in repr(case.identity)


def test_the_notice_says_a_person_takes_over_and_that_it_is_still_an_ai_for_now(
    tmp_path: Path,
) -> None:
    with ScriptedLLM() as llm:
        script_summary(llm)
        [response] = Rig(llm, tmp_path).post(Caller(region="US", session=SESSION), {"turns": []})
    notice = response.json()["notice"]
    assert notice == NOTICE
    assert "care team" in notice and "take over" in notice
    assert "AI assistant" in notice  # NFR-08: not yet a person, and never pretends to be one
    assert "has joined" not in notice and "joined" not in notice


def test_pressing_twice_or_asking_by_message_is_one_handoff(tmp_path: Path) -> None:
    with ScriptedLLM() as llm:
        script_summary(llm)
        rig = Rig(llm, tmp_path)
        first, second, third = rig.post(
            Caller(region="US", session=SESSION),
            {"turns": turns("a person please")},
            {"turns": turns("a person please"), "trigger": "message"},
            {"turns": []},
        )
    refs = {r.json()["reference"] for r in (first, second, third)}
    assert len(refs) == 1 and len(rig.state.cases) == 1 and rig.runs == 1


def test_two_sessions_get_two_handoffs(tmp_path: Path) -> None:
    with ScriptedLLM() as llm:
        script_summary(llm)
        script_summary(llm)
        rig = Rig(llm, tmp_path)
        [a] = rig.post(Caller(region="US", session=SESSION), {"turns": []})
        [b] = rig.post(Caller(region="US", session="0" * 32), {"turns": []})
    assert a.json()["reference"] != b.json()["reference"] and len(rig.state.cases) == 2


def test_a_signed_in_shopper_is_named_by_id_and_another_shoppers_order_gets_no_case(
    tmp_path: Path,
) -> None:
    rig_order = None
    for line in (DATA / "orders.jsonl").read_text().splitlines():
        order = json.loads(line)
        if order["shopper_id"] != "S0103":
            rig_order = order
            break
    assert rig_order is not None
    with ScriptedLLM() as llm:
        script_summary(llm)
        rig = Rig(llm, tmp_path)
        [response] = rig.post(
            Caller(region="US", session=SESSION, shopper_id="S0103"),
            {"turns": turns(f"where is {rig_order['order_id']}? a person please")},
        )
    assert response.status_code == 202
    [case] = rig.state.cases.values()
    assert case.identity.kind == "shopper" and case.identity.ref == "S0103"
    assert case.order_id is None and case.order_case_id is None
    assert not rig.cases.cases and case.root_cause == "shopper_requested"


@pytest.mark.parametrize(
    "texts",
    [
        (f"card {TEST_PAN} a person",),
        ("card 4111", "1111 1111", "1111"),
        ("ok", "ok"),  # control: no card data, accepted below
    ],
)
def test_card_data_never_reaches_the_desk_or_a_case(tmp_path: Path, texts: tuple[str, ...]) -> None:
    with ScriptedLLM() as llm:
        script_summary(llm)
        rig = Rig(llm, tmp_path)
        [response] = rig.post(Caller(region="US", session=SESSION), {"turns": turns(*texts)})
        seen = json.dumps(llm.requests)
    if texts == ("ok", "ok"):
        assert response.status_code == 202
        return
    assert response.status_code == 422 and set(response.json()) == ENVELOPE_KEYS
    assert "4111" not in response.text and "4111" not in seen
    assert not rig.state.opened and not rig.state.cases and rig.runs == 0


def test_a_fullwidth_card_number_is_refused_and_not_queued(tmp_path: Path) -> None:
    wide = "".join(chr(ord(c) + 0xFEE0) if c.isdigit() else c for c in TEST_PAN)
    with ScriptedLLM() as llm:
        rig = Rig(llm, tmp_path)
        [response] = rig.post(Caller(region="US", session=SESSION), {"turns": turns(wide)})
        assert not llm.requests
    assert response.status_code == 422 and set(response.json()) == ENVELOPE_KEYS
    assert not rig.state.opened and not rig.state.cases


def test_every_refusal_is_the_structured_envelope_with_no_echo(tmp_path: Path) -> None:
    marker = "echo-me-9f3"
    with ScriptedLLM() as llm:
        rig = Rig(llm, tmp_path)
        [bad, no_session] = [
            rig.post(Caller(region="US", session=SESSION), {"turns": turns(marker), "x": 1})[0],
            rig.post(Caller(region="US"), {"turns": turns(marker)})[0],
        ]
    for response in (bad, no_session):
        assert response.status_code >= 400
        body = response.json()
        assert set(body) == ENVELOPE_KEYS and body["isError"] is True
        assert marker not in response.text
    assert bad.json()["errorCategory"] == "validation" and bad.json()["isRetryable"] is False
    assert no_session.json()["errorCategory"] == "transient" and no_session.json()["isRetryable"]


def test_the_endpoint_takes_post_only() -> None:
    methods = {
        m for route in handoff_module.router.routes for m in getattr(route, "methods", set())
    }
    assert methods == {"POST"}


def test_nothing_in_the_gateways_handoff_module_can_build_a_model_client() -> None:
    tree = ast.parse(Path(handoff_module.__file__ or "").read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update([node.module or "", *(a.name for a in node.names)])
    assert not any("claude_agent_sdk" in n or n == "anthropic" for n in imported), imported
    assert "agent_options" not in imported and "anthropic_client" not in imported


# ---- the trigger: behaviours its docstring promises, not a phrase list --------------------------


def test_the_trigger_is_pure_and_total() -> None:
    for text in ("", " ", "\u0000", "a" * 50_000, "talk to a human " * 2000, "\U0001f600"):
        assert asks_for_person(text) is asks_for_person(text)
        assert isinstance(asks_for_person(text), bool)


def test_the_trigger_ignores_case_and_compatibility_forms() -> None:
    plain = "I want to talk to a person"
    assert asks_for_person(plain)
    assert asks_for_person(plain.upper())
    assert asks_for_person("".join(chr(ord(c) + 0xFEE0) if "a" <= c <= "z" else c for c in plain))


def test_the_trigger_declines_a_negation_and_a_product() -> None:
    assert not asks_for_person("I don't want to talk to a person")
    assert not asks_for_person("do you sell a two-person tent")
