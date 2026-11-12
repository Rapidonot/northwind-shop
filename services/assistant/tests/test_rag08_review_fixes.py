"""Row #13, tests that close the mutants left after the security re-review (pass 2): the outage
reset, the view-width lookback (N1), positions after a longer marker rewrite (N3), markers formed
by a cut (N4), and the `aborted` telemetry line (L3). No model call, no spend."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.output_check import FIXED_SENTENCES, OutputCheck, check_text
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.tools import ALLOWED_TOOL
from northwind_assistant.turn import Reset, TextDelta, TurnError, TurnEvent, stream_answer
from northwind_assistant.usage import TELEMETRY_LOGGER, TurnUsage
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("NFR-03")

ZW = "​" * 130


class EmptyThenBroken:
    """The first search finds nothing; the model's own search then fails."""

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("index down")
        return []


def turn(message: str, retriever: Any, llm: ScriptedLLM, root: Path) -> list[TurnEvent]:
    runtime = agent_runtime(base_url=llm.url, root=root)

    async def collect(rag: RagEndpoint) -> list[TurnEvent]:
        return [e async for e in stream_answer(message, Caller(region="US"), rag, runtime)]

    with serve_retriever(retriever, Caller) as (url, _):
        return asyncio.run(collect(RagEndpoint(url, KEY)))


def test_an_outage_sends_no_held_text_before_its_reset(tmp_path: Path) -> None:
    """Text with no sources behind it is discarded, not sent and then taken back (pass 1, L2)."""
    with ScriptedLLM() as llm:
        llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"})
        llm.reply_stream("It is 100% waterproof.")
        events = turn("hi", EmptyThenBroken(), llm, tmp_path)
    assert events[-2:] == [Reset(), TurnError("retrieval_failed")]
    assert not any(isinstance(event, TextDelta) for event in events)


@pytest.mark.parametrize("size", [1, 4])
def test_n1_a_payload_starting_at_a_non_trigger_word_is_seen_across_padding(size: int) -> None:
    """`a` is not a trigger, so it can be sent before the duration completes; the lookback must
    still reach it, in view characters (pass 2, N1)."""
    text = "Every item can be returned after a" + ZW + " full year. Bye."
    check = OutputCheck(set(), [])
    sent: list[str] = []
    for i in range(0, len(text), size):
        sent += [p for p in check.feed(text[i : i + size]) if p is not None]
    sent += [p for p in check.finish() if p is not None]
    assert FIXED_SENTENCES["false_policy"] in check.shown
    assert "full year" not in "".join(sent)


def test_n4_a_marker_formed_by_a_cut_is_removed_even_with_text_after_it() -> None:
    shown = check_text("see [[X1]Y2] now" + " and then some more words" * 4)
    assert "Y2" not in shown and "[" not in shown


def test_n3_positions_hold_when_the_rewrite_is_longer_and_text_follows() -> None:
    seen = {"A1", "B1", "C1", "D1", "E1"}
    check = OutputCheck(seen, [])
    sent: list[str] = []
    for piece in ["Warm [A1,B1,C1,D1,E1,X1]", " and soft."]:
        sent += [p for p in check.feed(piece) if p is not None]
    sent += [p for p in check.finish() if p is not None]
    assert "".join(sent) == check.shown == "Warm [A1, B1, C1, D1, E1] and soft."


def test_l3_a_turn_that_never_reported_logs_one_aborted_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    usage = TurnUsage(deferred=True)
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        usage.flush()
    [line] = [json.loads(r.getMessage()) for r in caplog.records if r.name == TELEMETRY_LOGGER]
    assert line["outcome"] == "aborted"


def test_l3_a_deferred_report_is_logged_once_on_flush(caplog: pytest.LogCaptureFixture) -> None:
    usage = TurnUsage(deferred=True)
    with caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        usage.report("answered")
        assert not [r for r in caplog.records if r.name == TELEMETRY_LOGGER]
        usage.flush()
    [line] = [json.loads(r.getMessage()) for r in caplog.records if r.name == TELEMETRY_LOGGER]
    assert line["outcome"] == "answered"
