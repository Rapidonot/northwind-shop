"""A tool result that is the CLI's own error text, not mcp-rag's envelope (CC-10 C4'; SA-02, hard
rule 10). mcp-rag is made to fail the model's `retrieve_context` call at the transport, after the
server's own first search found nothing, so the CLI writes the error result itself. That is a
failed search, so with no source behind the answer the turn ends as `retrieval_failed`, and what
the CLI wrote reaches no log or event.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.tools import ALLOWED_TOOL
from northwind_assistant.turn import Reset, TextDelta, TurnError, TurnEvent, stream_answer
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_rag.retrieve import TOOLS
from northwind_mcp_rag.server import Backend, RagServer
from northwind_mcp_rag.testing import KEY, CallerSearcher, running
from northwind_rag.retrieval import RetrievedChunk
from northwind_rag.testing import FakeEmbedder

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("SA-02")]

US = Caller(region="US")
SOURCE = RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")


class Empty:
    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        return []


class Found:
    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        return [SOURCE]


class FailsLaterCalls(RagServer):
    """Passes the first `tools/call` (the assistant's own search) and answers every later one
    with a 503, as a server that dropped mid-turn would."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tool_calls = 0

    def app(self) -> ASGIApp:
        inner = super().app()

        async def wrapper(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http" and scope["method"] == "POST":
                body, more = b"", True
                while more:
                    message = await receive()
                    body += message.get("body", b"")
                    more = message.get("more_body", False)
                if b'"tools/call"' in body:
                    self.tool_calls += 1
                    if self.tool_calls >= 2:
                        await send(
                            {
                                "type": "http.response.start",
                                "status": 503,
                                "headers": [(b"content-type", b"text/plain")],
                            }
                        )
                        await send({"type": "http.response.body", "body": b"upstream dropped"})
                        return
                sent = False

                async def replay() -> Message:
                    nonlocal sent
                    if sent:
                        return await receive()  # the real disconnect, whenever it comes
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}

                await inner(scope, replay, send)
                return
            await inner(scope, receive, send)

        return wrapper


def run_turn_against(first: Any, tmp_path: Path) -> tuple[list[TurnEvent], ScriptedLLM, int]:
    server = FailsLaterCalls(
        Backend(lambda: None, FakeEmbedder()),  # type: ignore[arg-type, return-value]
        key=KEY,
        tools=TOOLS,
        searcher=CallerSearcher(first, Caller),
    )
    with running(server) as url, ScriptedLLM() as llm:
        llm.reply_tool_use(ALLOWED_TOOL, {"query": "a refinement"})
        llm.reply_stream("Here is an unsupported answer.")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path)

        async def collect() -> list[TurnEvent]:
            return [
                e
                async for e in stream_answer(
                    "do these towels shrink?", US, RagEndpoint(url, KEY), runtime
                )
            ]

        events = asyncio.run(collect())
    return events, llm, server.tool_calls


def test_a_transport_failure_on_the_models_search_with_no_source_is_retrieval_failed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    events, llm, tool_calls = run_turn_against(Empty(), tmp_path)
    assert tool_calls >= 2  # the CLI really tried, and was refused
    assert len(llm.requests) == 2  # the model saw the failed result and answered
    assert events[-1] == TurnError("retrieval_failed")
    assert Reset() in events  # the ungrounded text is withdrawn
    assert "upstream dropped" not in caplog.text
    assert not any(isinstance(e, TextDelta) and "upstream" in e.text for e in events)


def test_the_same_failure_after_a_found_source_is_not_an_outage(tmp_path: Path) -> None:
    """With a source from the first search, the answer is grounded: no `retrieval_failed`."""
    events, _, _ = run_turn_against(Found(), tmp_path)
    assert TurnError("retrieval_failed") not in events
