"""Row #23 (SA-06): "an explicit request for a person is honored immediately" through /api/chat.

A message the deterministic trigger reads as a request for a person gets `handoff` and `done`, and
reaches neither the search nor the model. Card data is still refused first (hard rule 1). A
near-miss goes to the assistant as before. Same stack as `test_chat.py`: the real CLI, a scripted
model and a fake retriever behind mcp-rag."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import RagEndpoint
from northwind_common.llm import agent_runtime
from northwind_gateway.app import app
from northwind_gateway.chat import CARD_MESSAGE, ChatServices, chat_services
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = [pytest.mark.req("SA-06"), pytest.mark.req("NFR-08")]

SOURCE = RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")
PHRASES = Path(__file__).with_name("handoff_phrases.jsonl")


class FakeRetriever:
    def __init__(self) -> None:
        self.callers: list[Caller] = []

    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        self.callers.append(caller)
        return [SOURCE]


class Stack:
    def __init__(self, llm: ScriptedLLM, client: TestClient, retriever: FakeRetriever) -> None:
        self.llm, self.client, self.retriever = llm, client, retriever


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    retriever = FakeRetriever()
    with ScriptedLLM() as llm, serve_retriever(retriever, Caller) as (url, _):
        services = ChatServices(
            RagEndpoint(url, KEY), agent_runtime(base_url=llm.url, root=tmp_path / "rt")
        )
        app.dependency_overrides[chat_services] = lambda: services
        try:
            yield Stack(llm, TestClient(app), retriever)
        finally:
            app.dependency_overrides.clear()


def events(text: str) -> list[tuple[str, Any]]:
    parsed = []
    for frame in text.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if not line.startswith(":"))
        if "event" in lines:
            parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def _matched_positives() -> list[str]:
    """Labelled requests the trigger catches (55 of 56 in-sample; see test_sa06_trigger.py)."""
    from northwind_gateway.handoff_trigger import asks_for_person

    rows = [json.loads(line) for line in PHRASES.read_text(encoding="utf-8").splitlines()]
    return [r["text"] for r in rows if r["label"] == "handoff" and asks_for_person(r["text"])]


@pytest.mark.parametrize(
    "message", ["Can I talk to a real person?", "agent", "Not you, I want a real person."]
)
def test_a_request_for_a_person_is_handed_off_with_no_search_and_no_model_call(
    stack: Stack, message: str
) -> None:
    response = stack.client.post("/api/chat", json={"message": message})
    assert response.status_code == 200
    assert events(response.text) == [("handoff", {}), ("done", {})]
    assert stack.llm.requests == [] and stack.retriever.callers == []


def test_every_caught_labelled_request_skips_the_model(stack: Stack) -> None:
    caught = _matched_positives()
    assert len(caught) >= 54
    for message in caught:
        response = stack.client.post("/api/chat", json={"message": message})
        assert [name for name, _ in events(response.text)] == ["handoff", "done"], message
    assert stack.llm.requests == [] and stack.retriever.callers == []


def test_card_data_is_refused_before_the_trigger(stack: Stack) -> None:
    response = stack.client.post(
        "/api/chat", json={"message": "my card is 4242424242424242, talk to a person"}
    )
    assert response.status_code == 422 and response.json()["message"] == CARD_MESSAGE
    assert "handoff" not in response.text and stack.llm.requests == []


def test_a_near_miss_still_goes_to_the_assistant(stack: Stack) -> None:
    stack.llm.reply_stream("Wash it cold [FAQ-001].")
    message = "I don't need a person, just tell me how to wash it."
    response = stack.client.post("/api/chat", json={"message": message})
    names = [name for name, _ in events(response.text)]
    assert "handoff" not in names and names[0] == "token" and names[-1] == "done"
    assert stack.llm.requests and stack.retriever.callers
