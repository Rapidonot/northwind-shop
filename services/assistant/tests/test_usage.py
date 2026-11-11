"""Tokens, cache hits and cost of one answer, as one telemetry line per turn (RAG-13, NFR-16).

A scripted model reports the `usage` it is told to, so every figure is known in advance. No
cassette and no spend.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant import turn as turn_module
from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.tools import ALLOWED_TOOL
from northwind_assistant.turn import (
    INSUFFICIENT_EVIDENCE,
    MAX_TURNS,
    Citations,
    TurnError,
    TurnEvent,
    stream_answer,
)
from northwind_assistant.usage import TELEMETRY_LOGGER, TurnUsage
from northwind_common.llm import ASSISTANT_MODEL, AgentRuntime, agent_runtime
from northwind_common.pricing import cost_usd
from northwind_llm_replay.scripted import ScriptedLLM, blocks_sse
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = [pytest.mark.req("RAG-13"), pytest.mark.req("NFR-16")]

US = Caller(region="US")
SONNET = "claude-sonnet-5-5"
SOURCE = RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")
FIELDS = {
    "blocked",  # the output check's counts per class, no text (row #13, NFR-03)
    "event",
    "outcome",
    "calls",
    "input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "output_tokens",
    "cost_usd",
    "cache_hit_rate",
}
M = 1_000_000

# Synthetic figures, chosen so the sums are easy to check by hand.
CALL_A = {
    "input_tokens": 1000,
    "cache_read_input_tokens": 5000,
    "cache_creation_input_tokens": 2000,
    "cache_creation": {"ephemeral_5m_input_tokens": 1500, "ephemeral_1h_input_tokens": 500},
    "output_tokens": 300,
}
CALL_B = {
    "input_tokens": 200,
    "cache_read_input_tokens": 8000,
    "cache_creation_input_tokens": 0,
    "output_tokens": 100,
}


class Fixed:
    def __init__(self, *rounds: Sequence[RetrievedChunk] | Exception) -> None:
        self.rounds = list(rounds) or [[SOURCE]]
        self.calls = 0

    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        item = self.rounds[min(self.calls, len(self.rounds) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


def run(
    message: str, retriever: Fixed, llm: ScriptedLLM, root: Path, caller: Caller = US
) -> list[TurnEvent]:
    runtime: AgentRuntime = agent_runtime(base_url=llm.url, root=root)

    async def collect(rag: RagEndpoint) -> list[TurnEvent]:
        return [e async for e in stream_answer(message, caller, rag, runtime)]

    with serve_retriever(retriever, Caller) as (url, _):
        return asyncio.run(collect(RagEndpoint(url, KEY)))


def script(
    llm: ScriptedLLM,
    blocks: list[dict[str, Any]],
    usage: dict[str, Any],
    *,
    stop: str = "end_turn",
    model: str = SONNET,
) -> None:
    body = blocks_sse(model, blocks, stop, usage, f"msg_u{len(llm.queue)}")
    llm.reply_raw(200, body, "text/event-stream")


def answer(*chunks: str) -> list[dict[str, Any]]:
    return [{"type": "text", "chunks": list(chunks)}]


def tool_call(query: str, n: int = 0) -> list[dict[str, Any]]:
    return [
        {"type": "tool_use", "id": f"toolu_u{n}", "name": ALLOWED_TOOL, "input": {"query": query}}
    ]


@pytest.fixture
def telemetry(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger=TELEMETRY_LOGGER)
    return caplog


def lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == TELEMETRY_LOGGER]


# --- one line, every sum ---------------------------------------------------------------------


def test_a_multi_call_turn_logs_one_line_that_sums_every_call(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    with ScriptedLLM() as llm:
        script(llm, tool_call("more"), CALL_A, stop="tool_use")
        script(llm, answer("Wash cold [FAQ-001]."), CALL_B)
        events = run("How do I wash it?", Fixed(), llm, tmp_path)
    assert events[-1] == Citations(("FAQ-001",))
    [line] = lines(telemetry)
    assert set(line) == FIELDS
    assert line["event"] == "assistant_turn" and line["outcome"] == "answered"
    assert line["calls"] == 2
    assert line["input_tokens"] == 1200
    assert line["cache_read_tokens"] == 13000
    assert line["cache_write_tokens"] == 2000
    assert line["output_tokens"] == 400
    # 13000 / (1200 + 13000 + 2000)
    assert line["cache_hit_rate"] == pytest.approx(13000 / 16200, abs=1e-4)


def test_the_cost_is_the_sum_of_each_calls_price(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    with ScriptedLLM() as llm:
        script(llm, tool_call("more"), CALL_A, stop="tool_use")
        script(llm, answer("ok"), CALL_B)
        run("hi", Fixed(), llm, tmp_path)
    [line] = lines(telemetry)
    assert line["cost_usd"] == pytest.approx(
        cost_usd(SONNET, CALL_A) + cost_usd(SONNET, CALL_B), abs=1e-9
    )


def test_a_calls_cost_uses_the_read_both_write_and_output_rates() -> None:
    # Sonnet per million: input 2.00, read 0.10, 5m write 2.50, 1h write 4.00, output 10.00.
    # 1000 * 2 + 5000 * 0.1 + 1500 * 2.5 + 500 * 4 + 300 * 10 = 11250 -> $0.01125
    assert cost_usd(SONNET, CALL_A) == pytest.approx(0.01125, abs=1e-9)
    usage = TurnUsage()
    usage.call_started({"model": SONNET, "usage": CALL_A})
    usage.report("answered")
    assert usage.cost_usd == pytest.approx(0.01125, abs=1e-9)


def test_a_turn_reports_the_rate_inputs_it_was_told(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    only_cache = {"input_tokens": 0, "cache_read_input_tokens": 900, "output_tokens": 4}
    with ScriptedLLM() as llm:
        script(llm, answer("ok"), only_cache)
        run("hi", Fixed(), llm, tmp_path)
    [line] = lines(telemetry)
    assert line["cache_hit_rate"] == 1.0
    assert line["cost_usd"] == pytest.approx(cost_usd(SONNET, only_cache), abs=1e-9)


def test_a_retried_attempt_is_counted(tmp_path: Path, telemetry: pytest.LogCaptureFixture) -> None:
    with ScriptedLLM() as llm:
        llm.reply_cut_stream("partial ")  # message_start reports 10 input tokens, then the cut
        script(llm, answer("whole [FAQ-001]."), CALL_B)
        events = run("hi", Fixed(), llm, tmp_path)
    assert events[-1] == Citations(("FAQ-001",))
    [line] = lines(telemetry)
    assert line["calls"] == 2
    assert line["input_tokens"] == 10 + 200
    assert line["cache_read_tokens"] == 8000


def test_no_prompt_tokens_gives_a_null_rate_not_an_exception(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    with ScriptedLLM() as llm:
        script(llm, answer("ok"), {"input_tokens": 0, "output_tokens": 3})
        events = run("hi", Fixed(), llm, tmp_path)
    assert isinstance(events[-1], Citations)
    [line] = lines(telemetry)
    assert line["cache_hit_rate"] is None
    assert line["calls"] == 1 and line["output_tokens"] == 3


def test_a_turn_usage_with_no_calls_has_a_null_rate_and_no_exception(
    telemetry: pytest.LogCaptureFixture,
) -> None:
    usage = TurnUsage()
    assert usage.cache_hit_rate is None
    usage.report("internal")
    [line] = lines(telemetry)
    assert line["calls"] == 0 and line["cache_hit_rate"] is None and line["outcome"] == "internal"


def test_an_unknown_model_gives_a_null_cost_and_the_answer_still_arrives(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    with ScriptedLLM() as llm:
        script(llm, answer("Wash cold [FAQ-001]."), CALL_B, model="not-a-priced-model")
        events = run("hi", Fixed(), llm, tmp_path)
    assert events[-1] == Citations(("FAQ-001",))
    assert not any(isinstance(e, TurnError) for e in events)
    [line] = lines(telemetry)
    assert line["cost_usd"] is None
    assert line["input_tokens"] == 200  # the tokens are still reported


def test_one_unknown_model_call_keeps_the_cost_null_for_the_turn(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    with ScriptedLLM() as llm:
        script(llm, tool_call("more"), CALL_A, stop="tool_use", model="not-a-priced-model")
        script(llm, answer("ok"), CALL_B)
        run("hi", Fixed(), llm, tmp_path)
    [line] = lines(telemetry)
    assert line["calls"] == 2 and line["cost_usd"] is None


# --- every outcome logs exactly one line -----------------------------------------------------


def test_an_answered_turn_logs_one_line(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    with ScriptedLLM() as llm:
        script(llm, answer("ok"), CALL_B)
        run("hi", Fixed(), llm, tmp_path)
    assert [line["outcome"] for line in lines(telemetry)] == ["answered"]


def test_the_turn_cap_logs_one_insufficient_evidence_line(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    with ScriptedLLM() as llm:
        for n in range(MAX_TURNS + 2):
            script(llm, tool_call(f"q{n}", n), CALL_B, stop="tool_use")
        events = run("hi", Fixed(), llm, tmp_path)
    assert INSUFFICIENT_EVIDENCE in "".join(getattr(e, "text", "") for e in events)
    found = lines(telemetry)
    assert [line["outcome"] for line in found] == ["insufficient_evidence"]
    assert found[0]["calls"] == MAX_TURNS


def test_an_outage_logs_one_retrieval_failed_line(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    with ScriptedLLM() as llm:
        script(llm, answer("ok"), CALL_B)
        events = run("hi", Fixed(RuntimeError("down")), llm, tmp_path)
    assert events[-1] == TurnError("retrieval_failed")
    assert [line["outcome"] for line in lines(telemetry)] == ["retrieval_failed"]


def test_an_upstream_error_logs_one_model_unavailable_line(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    refusal = {"type": "error", "error": {"type": "invalid_request_error", "message": "no"}}
    with ScriptedLLM() as llm:
        for _ in range(5):
            llm.reply_raw(400, json.dumps(refusal))
        events = run("hi", Fixed(), llm, tmp_path)
    assert events == [TurnError("model_unavailable")]
    [line] = lines(telemetry)
    assert line["outcome"] == "model_unavailable" and set(line) == FIELDS


def test_a_cassette_miss_logs_one_replay_miss_line(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    refusal = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "cassette miss"},
    }
    with ScriptedLLM() as llm:
        for _ in range(5):
            llm.reply_raw(400, json.dumps(refusal))
        events = run("hi", Fixed(), llm, tmp_path)
    assert events == [TurnError("replay_miss")]
    assert [line["outcome"] for line in lines(telemetry)] == ["replay_miss"]


def test_an_unexpected_exception_logs_one_internal_line(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explode(*_: Any, **__: Any) -> Any:
        raise RuntimeError("boom")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(turn_module, "query", explode)
    with ScriptedLLM() as llm:
        events = run("hi", Fixed(), llm, tmp_path)
    assert events == [TurnError("internal")]
    assert [line["outcome"] for line in lines(telemetry)] == ["internal"]


def test_a_cut_stream_with_no_retry_logs_one_line_and_an_error(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    with ScriptedLLM() as llm:
        for _ in range(6):
            llm.reply_cut_stream("partial ")
        events = run("hi", Fixed(), llm, tmp_path)
    assert isinstance(events[-1], TurnError)
    [line] = lines(telemetry)
    assert line["outcome"] == events[-1].code and line["calls"] >= 1


def test_the_default_model_is_priced() -> None:
    """The assistant's configured model must be one the report can price."""
    assert cost_usd(ASSISTANT_MODEL, {"input_tokens": M}) > 0


# --- the line holds no text ------------------------------------------------------------------


def test_the_line_never_holds_shopper_text_queries_source_text_or_ids(
    tmp_path: Path, telemetry: pytest.LogCaptureFixture
) -> None:
    said, queried, text, source_id = "MARK-SAID-31", "MARK-QUERY-32", "MARK-TEXT-33", "MARK-ID-34"
    planted = RetrievedChunk("c9", "document", source_id, "faq", f"Wash cold {text}.")
    with ScriptedLLM() as llm:
        script(llm, tool_call(queried), CALL_A, stop="tool_use")
        script(llm, answer(f"Wash cold [{source_id}]."), CALL_B)
        events = run(f"how do I wash {said}?", Fixed([planted]), llm, tmp_path)
    assert events[-1] == Citations((source_id,))  # the planted material really was in play
    records = [r for r in telemetry.records if r.name == TELEMETRY_LOGGER]
    assert len(records) == 1
    raw = records[0].getMessage() + repr(records[0].args) + repr(records[0].__dict__.get("extra"))
    for marker in (said, queried, text, source_id, "Wash cold"):
        assert marker not in raw
    assert set(json.loads(records[0].getMessage())) == FIELDS


@pytest.mark.parametrize(
    "message",
    [
        "not an object",
        {"model": "claude-sonnet-5-5", "usage": "not an object"},
        {"model": "claude-sonnet-5-5", "usage": {"input_tokens": "many"}},
        {"model": "claude-sonnet-5-5", "usage": {"input_tokens": float("inf")}},
        {"model": "claude-sonnet-5-5", "usage": {"cache_creation": "odd", "input_tokens": 1}},
    ],
)
def test_malformed_upstream_usage_never_raises(message: Any) -> None:
    """A telemetry fault must not cost the shopper their answer."""
    usage = TurnUsage()
    usage.call_started(message)
    usage.call_updated("not an object")
    usage.call_updated({"output_tokens": []})
    usage.report("answered")
