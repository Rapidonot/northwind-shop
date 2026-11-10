"""The assistant's mcp-rag client (CC-10 C1'): no shopper message or card-shaped string reaches a
log at any level (NFR-04, hard rule 1), and the per-turn token is a credential.

The MCP client library logs each request, arguments included, at DEBUG; `rag_client` holds its
loggers at WARNING. Every test here runs with the root logger at DEBUG.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

import pytest

from northwind_assistant import rag_client
from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import QUIET_LOGGERS, McpRagSearcher, RagEndpoint
from northwind_assistant.tools import ALLOWED_TOOL
from northwind_assistant.turn import TurnError, TurnEvent, stream_answer
from northwind_common.llm import agent_runtime
from northwind_common.mcp_token import verify
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("NFR-04")]

US = Caller(region="US")
MESSAGE = "do you stock SHOPPER-MESSAGE-MARKER-4d1 towels?"
CARD = "4242" * 4  # synthetic
SOURCE = RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")


class Fake:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        self.queries.append(query)
        return [SOURCE]


def turn(message: str, llm: ScriptedLLM, root: Path, retriever: Fake) -> list[TurnEvent]:
    runtime = agent_runtime(base_url=llm.url, root=root)

    async def collect(rag: RagEndpoint) -> list[TurnEvent]:
        return [e async for e in stream_answer(message, US, rag, runtime)]

    with serve_retriever(retriever, Caller) as (url, _):
        return asyncio.run(collect(RagEndpoint(url, KEY)))


def test_the_client_loggers_stay_at_warning_whatever_the_root_level() -> None:
    logging.getLogger().setLevel(logging.DEBUG)
    rag_client.quiet_client_logs()
    for name in QUIET_LOGGERS:
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


def test_a_whole_turn_logs_no_shopper_message_and_no_card_digits(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    retriever = Fake()
    with ScriptedLLM() as llm:
        llm.reply_tool_use(ALLOWED_TOOL, {"query": f"card {CARD}"}, tool_id="t1")  # refused
        llm.reply_tool_use(ALLOWED_TOOL, {"query": MESSAGE}, tool_id="t2")
        llm.reply_stream("Wash cold [FAQ-001].")
        turn(MESSAGE, llm, tmp_path / "a", retriever)
    assert retriever.queries == [MESSAGE, MESSAGE]  # the searches really ran, with the message
    assert caplog.records  # something was logged, so a clean log is not an empty one
    assert "SHOPPER-MESSAGE-MARKER" not in caplog.text
    assert CARD not in caplog.text and "4242" not in caplog.text


def test_a_card_shaped_message_is_refused_before_mcp_rag_and_logged_nowhere(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    retriever = Fake()
    with ScriptedLLM() as llm:
        llm.reply_stream("must not be sent")
        events = turn(f"my card is {CARD}", llm, tmp_path, retriever)
    assert events == [TurnError("internal")]
    assert retriever.queries == [] and llm.requests == []
    assert CARD not in caplog.text and "4242" not in caplog.text


def test_without_the_quieting_the_message_would_reach_the_debug_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the MCP client really does log arguments at DEBUG, so the tests above check
    something."""
    monkeypatch.setattr(rag_client, "quiet_client_logs", lambda: None)
    for name in QUIET_LOGGERS:
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)
    caplog.set_level(logging.DEBUG)
    with serve_retriever(Fake(), Caller) as (url, _):
        searcher = McpRagSearcher(url, RagEndpoint(url, KEY).mint(US))
        asyncio.run(searcher.search(MESSAGE))
    assert "SHOPPER-MESSAGE-MARKER" in caplog.text


def test_the_endpoint_mints_a_shopper_token_for_the_caller_and_hides_its_key() -> None:
    endpoint = RagEndpoint("http://127.0.0.1:9/mcp", KEY)
    claims = verify(endpoint.mint(Caller(region="EU")), key=KEY)
    assert (claims.aud, claims.region, claims.agent) == ("shopper", "EU", "shopper-assistant")
    assert KEY.decode() not in repr(endpoint)
    searcher = McpRagSearcher("http://127.0.0.1:9/mcp", "v1.secret.token")
    assert "secret" not in repr(searcher)
