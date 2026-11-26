"""Row #23 (SA-06): BE-07's Care agent with an optional order.

With an order, everything the model sees is as row #26 built it, byte for byte, so the recorded
`backend-care` cassettes still replay: the hashes below were taken on main at `5d74f0a`, before the
change. With no order, the Care agent gets one tool, `submit_summary`, and no order or write."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from northwind_backend.agents import care as c
from northwind_backend.agents.care import (
    SUMMARY_TOOL_NAME,
    SYSTEM_PROMPT_NO_ORDER,
    CareTask,
    Turn,
    run_care,
    run_summary,
)
from northwind_backend.audit import AuditContext, InMemoryAuditSink
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM

from ._driver import POLICY, TEST_PAN, World, fixed_clock, model_text, transcript

pytestmark = pytest.mark.req("SA-06")

MAIN_HASHES = {
    "system": "894ea3bdfe2f8d67a1e9ef16c3d9986681ab1167d0d087f369faceb24b93ed43",
    "order_desc": "e109237c443f925c5abe991ffe27f71d74d67711af6da7d7baa3eaef483c2931",
    "order_schema": "943700876d9333b6e91d9d42a9ab24b8fd812d4377c29a75c561f1e83cd0ebb9",
    "summary_desc": "c3a6bdac9012f13f75a929006060b594211bb920f02c95e9696ec3785a2cf356",
    "summary_schema": "d3c0d2c7d0a4f15cb44a4b6ec72ba4fc7156259f6168cd42a20f53663051c3ed",
    "prompt": "3d26ecf44a34cff3c6b55c9bd7081e8d4d255b8afff5195f168504cf4e5d1724",
    "steps": "1ed0775bc8b98911c7f93b63c62664678ec495ca72a4f12dc3b66cb6262a7df8",
    "max_turns": "ef2d127de37b942baad06145e54b0c619a1f22327b2ebbcfbec78f5564afe39d",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_the_order_bound_care_input_is_byte_identical_to_main() -> None:
    task = CareTask(
        "O000002",
        (
            Turn("shopper", "My order O000002 has not arrived, call 555 1234"),
            Turn("assistant", "I am an AI."),
        ),
    )
    now = {
        "system": c.SYSTEM_PROMPT,
        "order_desc": c.ORDER_DESCRIPTION,
        "order_schema": json.dumps(c.ORDER_SCHEMA, sort_keys=True),
        "summary_desc": c.SUMMARY_DESCRIPTION,
        "summary_schema": json.dumps(c.SUMMARY_SCHEMA, sort_keys=True),
        "prompt": task.prompt(),
        "steps": repr(task.steps()),
        "max_turns": str(c.MAX_TURNS),
    }
    assert {name: _sha(text) for name, text in now.items()} == MAIN_HASHES


def test_a_task_with_no_order_has_only_the_summary_step_and_its_own_prompt() -> None:
    task = CareTask(None, transcript("Can I talk to a person about sizing?", "I'm an AI."))
    assert [step.name for step in task.steps()] == ["summary"]
    assert task.prompt().startswith("Write the handoff summary for a shopper who asked")
    assert "get_order" not in SYSTEM_PROMPT_NO_ORDER
    assert "You are an AI system." in SYSTEM_PROMPT_NO_ORDER


def test_each_runner_refuses_the_other_variant() -> None:
    audit = AuditContext(InMemoryAuditSink(), correlation_id="t", actor="test", clock=fixed_clock)
    world = World.make()
    with pytest.raises(ValueError):
        asyncio.run(run_care(CareTask(None), ports=world.ports(), audit=audit, correlation_id="r"))
    with pytest.raises(ValueError):
        asyncio.run(run_summary(CareTask("O000002"), audit=audit, correlation_id="r"))


def _summary(llm: ScriptedLLM, task: CareTask, root: Path) -> c.SummaryResult:
    audit = AuditContext(InMemoryAuditSink(), correlation_id="t", actor="test", clock=fixed_clock)
    return asyncio.run(
        run_summary(
            task,
            audit=audit,
            correlation_id="run-sum",
            runtime=agent_runtime(base_url=llm.url, root=root / "rt"),
            policy=POLICY,
        )
    )


def test_run_summary_offers_one_tool_and_keeps_the_first_accepted_summary(tmp_path: Path) -> None:
    task = CareTask(None, transcript("I want a person to help me pick boots.", "I'm an AI."))
    with ScriptedLLM() as llm:
        llm.reply_tool_use(SUMMARY_TOOL_NAME, {"summary": "It costs $40."}, tool_id="toolu_1")
        llm.reply_tool_use(
            SUMMARY_TOOL_NAME, {"summary": "The shopper wants help choosing boots."}, tool_id="t2"
        )
        llm.reply("Done.")
        result = _summary(llm, task, tmp_path)
        bodies = [r["body"] for r in llm.requests]
    assert result.summary == "The shopper wants help choosing boots."
    tools = {tool["name"] for body in bodies for tool in body.get("tools", [])}
    assert tools == {SUMMARY_TOOL_NAME}, tools
    systems = [json.dumps(body["system"]) for body in bodies]
    assert systems and all("A shopper asked to talk to a person." in s for s in systems)
    assert not any("get_order(order_id)" in s for s in systems)


def test_card_data_in_a_no_order_transcript_never_reaches_the_model(tmp_path: Path) -> None:
    task = CareTask(None, transcript(f"my card is {TEST_PAN}, get me a human", "I'm an AI."))
    with ScriptedLLM() as llm:
        llm.reply_tool_use(SUMMARY_TOOL_NAME, {"summary": "Shopper asked for a person."})
        llm.reply("Done.")
        _summary(llm, task, tmp_path)
        seen = model_text(llm.requests)
    assert "4111" not in seen and c.WITHHELD in seen
