"""Q2 and Q6 (the user's decisions, 2026-11-09): a first search that fails for any reason but
validation ends the turn with `retrieval_failed` and no model call, logged by category only
(CC-10 C10, L7, L12; decision 0007, hard rule 10).

Each way a first search can fail is here: mcp-rag unreachable, an HTTP 5xx, a refused token (401),
a transient envelope, a permission envelope, and a business envelope, which on a first search can
only be the type refusal (the cap cannot trip on the first of three).
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Sequence
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.turn import TurnError, TurnEvent, stream_answer
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM, ServerThread
from northwind_mcp_rag.server import Backend, RagServer
from northwind_mcp_rag.testing import KEY, CallerSearcher, running, serve_retriever
from northwind_rag.retrieval import RetrievedChunk
from northwind_rag.testing import FakeEmbedder

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("SA-02"), pytest.mark.req("RAG-10")]

US = Caller(region="US")
MESSAGE = "FIRST-SEARCH-MARKER do these towels shrink?"


class Raises:
    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        raise RuntimeError("postgresql://user:hunter2@db/prod")


class Unclassified:
    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        return [RetrievedChunk("c1", "policy-note", "D-1", None, "a type nobody classified")]


def answer(rag: RagEndpoint, root: Path) -> tuple[list[TurnEvent], ScriptedLLM]:
    with ScriptedLLM() as llm:
        llm.reply_stream("must not be sent")
        runtime = agent_runtime(base_url=llm.url, root=root)

        async def collect() -> list[TurnEvent]:
            return [e async for e in stream_answer(MESSAGE, US, rag, runtime)]

        events = asyncio.run(collect())
    return events, llm


def assert_closed(
    events: list[TurnEvent], llm: ScriptedLLM, caplog: pytest.LogCaptureFixture, category: str
) -> None:
    assert events == [TurnError("retrieval_failed")]
    assert llm.requests == []  # no model call
    assert f"first search failed: {category}" in caplog.text
    assert "FIRST-SEARCH-MARKER" not in caplog.text and "hunter2" not in caplog.text


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(autouse=True)
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


def test_mcp_rag_unreachable(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    rag = RagEndpoint(f"http://127.0.0.1:{free_port()}/mcp", KEY)
    events, llm = answer(rag, tmp_path)
    assert_closed(events, llm, caplog, "transient")


def test_an_http_5xx(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def boom(request: Request) -> Response:
        return Response("upstream exploded hunter2", status_code=503)

    server = ServerThread(Starlette(routes=[Route("/mcp", boom, methods=["GET", "POST"])]))
    server.start()
    try:
        events, llm = answer(RagEndpoint(f"{server.url}/mcp", KEY), tmp_path)
    finally:
        server.stop()
    assert_closed(events, llm, caplog, "transient")


def test_a_refused_token(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with serve_retriever(Raises(), Caller) as (url, server):
        events, llm = answer(RagEndpoint(url, b"w" * 40), tmp_path)  # signed with another key
    assert len(server.ledger) == 0  # refused before any tool ran
    assert_closed(events, llm, caplog, "transient")


def test_a_transient_envelope(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with serve_retriever(Raises(), Caller) as (url, _):
        events, llm = answer(RagEndpoint(url, KEY), tmp_path)
    assert_closed(events, llm, caplog, "transient")


def test_a_permission_envelope(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A server that does not offer the tool: the assistant's token is refused `not_allowed`."""
    server = RagServer(
        Backend(lambda: None, FakeEmbedder()),  # type: ignore[arg-type, return-value]
        key=KEY,
        tools={},
        searcher=CallerSearcher(Raises(), Caller),
    )
    with running(server) as url:
        events, llm = answer(RagEndpoint(url, KEY), tmp_path)
    assert_closed(events, llm, caplog, "permission")


def test_a_business_envelope_the_type_refusal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with serve_retriever(Unclassified(), Caller) as (url, _):
        events, llm = answer(RagEndpoint(url, KEY), tmp_path)
    assert_closed(events, llm, caplog, "business")


def test_a_validation_refusal_is_still_internal_not_retrieval_failed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Unchanged by Q2/Q6: a message the search will not take never reaches the model."""
    with serve_retriever(Raises(), Caller) as (url, _), ScriptedLLM() as llm:
        llm.reply_stream("must not be sent")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path)

        async def collect() -> list[TurnEvent]:
            return [e async for e in stream_answer("   ", US, RagEndpoint(url, KEY), runtime)]

        events = asyncio.run(collect())
    assert events == [TurnError("internal")] and llm.requests == []


def test_an_empty_first_search_is_not_a_failure(tmp_path: Path) -> None:
    class Empty:
        def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
            return []

    with serve_retriever(Empty(), Caller) as (url, _):
        events, llm = answer(RagEndpoint(url, KEY), tmp_path)
    assert len(llm.requests) == 1  # the model was called
    assert TurnError("retrieval_failed") not in events
