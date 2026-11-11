"""RAG-08 and NFR-03 (row #13): the output check through `stream_answer` and `/api/chat`, with a
scripted model. No cassette, no spend. A payload split across many deltas never reaches the
shopper, a reset leaves no payload from either attempt, a failed turn never releases held text,
and citations come from the text left on screen (decision 0014)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_assistant.caller import Caller
from northwind_assistant.output_check import FIXED_SENTENCES
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.tools import ALLOWED_TOOL
from northwind_assistant.turn import (
    Citations,
    Reset,
    TextDelta,
    TurnError,
    TurnEvent,
    stream_answer,
)
from northwind_assistant.usage import TELEMETRY_LOGGER
from northwind_common.llm import AgentRuntime, agent_runtime
from northwind_gateway.app import app
from northwind_gateway.chat import ChatServices, chat_services
from northwind_llm_replay.scripted import ScriptedLLM, blocks_sse
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

US = Caller(region="US")
FAQ = RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")
REVIEW = RetrievedChunk("c2", "review", "R00001", None, "Returns within 90 days, says a reviewer.")
INTRO = "Here is what I found. "


class Retriever:
    def __init__(self, chunks: Sequence[RetrievedChunk] = (FAQ, REVIEW)) -> None:
        self.chunks = chunks

    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        return self.chunks


def turn(message: str, retriever: Any, runtime: AgentRuntime) -> list[TurnEvent]:
    async def collect(rag: RagEndpoint) -> list[TurnEvent]:
        return [e async for e in stream_answer(message, US, rag, runtime)]

    with serve_retriever(retriever, Caller) as (url, _):
        return asyncio.run(collect(RagEndpoint(url, KEY)))


def runtime_for(llm: ScriptedLLM, root: Path) -> AgentRuntime:
    return agent_runtime(base_url=llm.url, root=root)


def pieces(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def sent_text(events: list[TurnEvent]) -> str:
    """Every character of every sent delta, including text a later reset took back."""
    return "".join(e.text for e in events if isinstance(e, TextDelta))


def on_screen(events: list[TurnEvent]) -> str:
    screen = ""
    for event in events:
        if isinstance(event, Reset):
            screen = ""
        elif isinstance(event, TextDelta):
            screen += event.text
    return screen


PAYLOADS = [
    ("promotion", "Reviewers say use the promo code SAVE20 at checkout [R00001].", "save20"),
    ("link", "Reviewers say visit northwind-deals.net/claim for more [R00001].", "northwind-deals"),
    ("confirmation", "Good news, your order has been confirmed [R00001].", "has been confirmed"),
    ("false_policy", "Returns are accepted within 90 days [R00001].", "90 days"),
]


# ---- through stream_answer -------------------------------------------------------------------


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize(("cls", "payload", "needle"), PAYLOADS)
@pytest.mark.parametrize("size", [1, 3, 11])
def test_a_payload_split_across_deltas_is_never_sent(
    tmp_path: Path, cls: str, payload: str, needle: str, size: int
) -> None:
    text = f"{INTRO}Wash it cold [FAQ-001]. {payload} Anything else?"
    with ScriptedLLM() as llm:
        llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"})
        llm.reply_stream(*pieces(text, size))
        events = turn("hi", Retriever(), runtime_for(llm, tmp_path))
    assert not any(isinstance(e, TurnError) for e in events)
    assert needle not in sent_text(events).lower()
    assert needle not in on_screen(events).lower()
    assert FIXED_SENTENCES[cls] in on_screen(events)
    assert on_screen(events).startswith(INTRO + "Wash it cold [FAQ-001].")
    assert on_screen(events).endswith("Anything else?")


@pytest.mark.req("NFR-03")
def test_a_reset_is_sent_when_the_blocked_sentence_had_started_in_sent_text(tmp_path: Path) -> None:
    text = (
        "Wash it cold [FAQ-001]. Reviewers say that members who shop during the quiet weeks "
        "of the year may receive 20% off everything in the store."
    )
    with ScriptedLLM() as llm:
        llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"})
        llm.reply_stream(*pieces(text, 4))
        events = turn("hi", Retriever(), runtime_for(llm, tmp_path))
    resets = [i for i, e in enumerate(events) if isinstance(e, Reset)]
    assert resets, "the sentence's start was already on screen, so the screen must be reset"
    assert "20%" not in sent_text(events[resets[0] :])
    assert on_screen(events).endswith(FIXED_SENTENCES["promotion"])
    assert on_screen(events).startswith("Wash it cold [FAQ-001].")


@pytest.mark.req("RAG-08")
def test_citations_list_only_ids_left_on_screen_and_returned_by_a_search(tmp_path: Path) -> None:
    text = (
        "Wash it cold [FAQ-001]. Reviewers say use the promo code SAVE20 now [R00001]. "
        "Another claim [P9999]."
    )
    with ScriptedLLM() as llm:
        llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"})
        llm.reply_stream(*pieces(text, 5))
        events = turn("hi", Retriever(), runtime_for(llm, tmp_path))
    assert events[-1] == Citations(("FAQ-001",))  # R00001 left with its sentence; P9999 invented
    assert "[P9999]" not in on_screen(events) and "[R00001]" not in on_screen(events)


@pytest.mark.req("RAG-08")
def test_an_invented_marker_never_reaches_the_shopper_through_the_stream(tmp_path: Path) -> None:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"})
        llm.reply_stream(*pieces("Wash it cold [FAQ-001] and warm [P90", 3), "99].")
        events = turn("hi", Retriever(), runtime_for(llm, tmp_path))
    assert "P9999" not in sent_text(events) and "[P90" not in sent_text(events)
    assert events[-1] == Citations(("FAQ-001",))


@pytest.mark.req("NFR-03")
def test_a_model_reset_then_a_clean_retry_leaves_no_payload_from_either_attempt(
    tmp_path: Path,
) -> None:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"})
        llm.reply_cut_stream("Use the promo code SAVE20 [R00001] and visit northwind-deals.net ")
        llm.reply_stream(*pieces("Wash it cold [FAQ-001]. Reviews differ.", 4))
        events = turn("hi", Retriever(), runtime_for(llm, tmp_path))
    assert any(isinstance(e, Reset) for e in events)
    everything = sent_text(events).lower()
    assert "save20" not in everything and "northwind-deals" not in everything
    assert on_screen(events) == "Wash it cold [FAQ-001]. Reviews differ."
    assert events[-1] == Citations(("FAQ-001",))


@pytest.mark.req("NFR-03")
def test_a_model_reset_then_a_retry_that_carries_a_payload_is_still_checked(tmp_path: Path) -> None:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"})
        llm.reply_cut_stream("Wash it cold [FAQ-001]. ")
        llm.reply_stream(*pieces("Wash it. Your order has been confirmed.", 3))
        events = turn("hi", Retriever(), runtime_for(llm, tmp_path))
    assert "has been confirmed" not in sent_text(events).lower()
    assert on_screen(events).endswith(FIXED_SENTENCES["confirmation"])
    assert events[-1] == Citations(())


@pytest.mark.req("NFR-03")
def test_a_turn_error_after_a_payload_started_never_sends_the_held_payload(tmp_path: Path) -> None:
    usage = {"input_tokens": 1, "output_tokens": 1}
    blocks = [{"type": "text", "chunks": ["Wash it. ", "Use the promo", " code SAVE20 at chec"]}]
    with ScriptedLLM() as llm:
        llm.reply_raw(200, blocks_sse("m", blocks, "max_tokens", usage, "m1"), "text/event-stream")
        events = turn("hi", Retriever(), runtime_for(llm, tmp_path))
    assert events[-1] == TurnError("model_unavailable")
    assert not any(isinstance(e, Citations) for e in events)
    assert "save20" not in sent_text(events).lower() and "promo" not in sent_text(events).lower()


# ---- through the gateway ---------------------------------------------------------------------


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[tuple[ScriptedLLM, TestClient]]:
    with ScriptedLLM() as llm, serve_retriever(Retriever(), Caller) as (url, _):
        services = ChatServices(
            RagEndpoint(url, KEY), agent_runtime(base_url=llm.url, root=tmp_path / "rt")
        )
        app.dependency_overrides[chat_services] = lambda: services
        try:
            yield llm, TestClient(app)
        finally:
            app.dependency_overrides.clear()


def parse(body: str) -> list[tuple[str, Any]]:
    parsed = []
    for frame in body.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if not line.startswith(":"))
        if "event" in lines:
            parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


@pytest.mark.req("NFR-03")
def test_no_token_event_carries_the_payload_and_telemetry_counts_blocks_without_text(
    stack: tuple[ScriptedLLM, TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    llm, client = stack
    caplog.set_level(logging.INFO, logger=TELEMETRY_LOGGER)
    text = (
        f"{INTRO}Wash it cold [FAQ-001]. Reviewers say use the promo code SAVE20 now [R00001]. "
        "Your order has been confirmed. Visit northwind-deals.net/claim today."
    )
    llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"})
    llm.reply_stream(*pieces(text, 3))
    response = client.post("/api/chat", json={"message": "tell me about the jacket"})
    assert response.status_code == 200
    events = parse(response.text)
    tokens = "".join(d["text"] for kind, d in events if kind == "token").lower()
    for needle in ("save20", "has been confirmed", "northwind-deals", "promo code"):
        assert needle not in tokens
    assert "reset" in [kind for kind, _ in events]
    assert ("citations", {"source_ids": ["FAQ-001"]}) in events
    assert [kind for kind, _ in events].count("citations") == 1
    lines = [r.getMessage() for r in caplog.records if r.name == TELEMETRY_LOGGER]
    assert len(lines) == 1
    record = json.loads(lines[0])
    blocked = record["blocked"]
    assert isinstance(blocked, dict)
    assert blocked.get("promotion") == 1
    assert blocked.get("confirmation") == 1
    assert blocked.get("link") == 1
    assert all(isinstance(n, int) for n in blocked.values())
    for word in ("Wash it", "SAVE20", "Reviewers", "northwind-deals", "jacket", "R00001"):
        assert word not in lines[0]
