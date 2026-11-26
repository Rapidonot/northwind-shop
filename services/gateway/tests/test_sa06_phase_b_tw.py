"""Row #23 (SA-06) phase B, written from the criteria: "an explicit request for a person is honored
immediately" through POST /api/chat.

A matched message gets exactly `handoff` {} then `done` {}, with no search and no model call; a
message the trigger does not match goes to the assistant as before; chat.py never reaches the
handoff desk. Harness: the real CLI, a scripted model and a fake retriever behind mcp-rag."""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import RagEndpoint
from northwind_common.llm import agent_runtime
from northwind_common.mcp_token import KEY_ENV
from northwind_gateway import chat as chat_module
from northwind_gateway.app import app
from northwind_gateway.chat import CARD_MESSAGE, MAX_MESSAGE_CHARS, ChatServices, chat_services
from northwind_gateway.session import (
    COOKIE,
    DEMO_SIGNIN_ENV,
    SHOPPER_COOKIE,
    session_key,
    sign,
    sign_shopper,
)
from northwind_gateway.signin import DEMO_SHOPPER
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("SA-06")

SOURCE = RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")
ASK = "Can I talk to a real person?"
ORDINARY = "How do I wash the fleece?"
FORBIDDEN_EVENTS = {"token", "citations", "reset", "error"}


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
def stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Stack]:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
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


def idle(stack: Stack) -> bool:
    return stack.llm.requests == [] and stack.retriever.callers == []


@pytest.mark.parametrize(
    "message",
    [
        "Can I talk to a real person?",
        "I want to speak to a human please",
        "TALK TO A PERSON!!!",
        "Can you connect me with customer service?",
    ],
)
def test_a_request_for_a_person_gets_exactly_handoff_then_done(stack: Stack, message: str) -> None:
    response = stack.client.post("/api/chat", json={"message": message})
    assert response.status_code == 200
    assert events(response.text) == [("handoff", {}), ("done", {})]
    assert idle(stack)


def test_a_handoff_turn_has_the_status_and_headers_of_an_ordinary_turn(stack: Stack) -> None:
    stack.llm.reply_stream("Wash it cold [FAQ-001].")
    ordinary = stack.client.post("/api/chat", json={"message": ORDINARY})
    handoff = stack.client.post("/api/chat", json={"message": ASK})
    assert handoff.status_code == ordinary.status_code == 200
    assert handoff.headers["content-type"] == ordinary.headers["content-type"]
    assert handoff.headers["content-type"].startswith("text/event-stream")
    assert handoff.headers.get("cache-control") == ordinary.headers.get("cache-control")


def test_a_handoff_turn_carries_no_token_citation_reset_or_error(stack: Stack) -> None:
    response = stack.client.post("/api/chat", json={"message": ASK})
    names = {name for name, _ in events(response.text)}
    assert not names & FORBIDDEN_EVENTS
    assert "event: token" not in response.text and "event: error" not in response.text


def test_a_message_the_trigger_does_not_match_goes_to_the_assistant(stack: Stack) -> None:
    stack.llm.reply_stream("Wash it cold [FAQ-001].")
    response = stack.client.post("/api/chat", json={"message": ORDINARY})
    names = [name for name, _ in events(response.text)]
    assert "handoff" not in names
    assert names[0] == "token" and names[-1] == "done" and "citations" in names
    assert stack.llm.requests and stack.retriever.callers


@pytest.mark.parametrize(
    "message",
    [
        "Is this a two-person tent?",
        "I don't want to talk to a person, just answer my question about the fleece.",
    ],
)
def test_a_product_or_negated_mention_is_not_a_handoff(stack: Stack, message: str) -> None:
    stack.llm.reply_stream("Here is what I found [FAQ-001].")
    response = stack.client.post("/api/chat", json={"message": message})
    assert "handoff" not in [name for name, _ in events(response.text)]
    assert stack.llm.requests


def test_card_data_is_refused_before_the_trigger(stack: Stack) -> None:
    for message in (
        "my card is 4242 4242 4242 4242, let me talk to a person",
        "4242424242424242 talk to a human",
    ):
        response = stack.client.post("/api/chat", json={"message": message})
        assert response.status_code == 422
        assert response.json()["message"] == CARD_MESSAGE
        assert "handoff" not in response.text and "event:" not in response.text
    assert idle(stack)


def test_an_over_length_request_for_a_person_is_still_refused(stack: Stack) -> None:
    message = ASK + " " + "x" * MAX_MESSAGE_CHARS
    assert len(message) > MAX_MESSAGE_CHARS
    response = stack.client.post("/api/chat", json={"message": message})
    assert response.status_code == 422 and "handoff" not in response.text
    assert idle(stack)


def test_an_empty_message_is_still_refused(stack: Stack) -> None:
    response = stack.client.post("/api/chat", json={"message": ""})
    assert response.status_code == 422 and "handoff" not in response.text


def test_a_turn_after_a_handoff_in_the_same_session_reaches_the_model(stack: Stack) -> None:
    first = stack.client.post("/api/chat", json={"message": ASK})
    assert events(first.text) == [("handoff", {}), ("done", {})]
    assert idle(stack)
    stack.llm.reply_stream("Wash it cold [FAQ-001].")
    second = stack.client.post("/api/chat", json={"message": ORDINARY})  # same client, same cookie
    names = [name for name, _ in events(second.text)]
    assert "handoff" not in names and names[0] == "token" and names[-1] == "done"
    assert stack.llm.requests and stack.retriever.callers


def test_a_second_request_for_a_person_is_honored_again(stack: Stack) -> None:
    for _ in range(2):
        response = stack.client.post("/api/chat", json={"message": ASK})
        assert events(response.text) == [("handoff", {}), ("done", {})]
    assert idle(stack)


def test_a_signed_in_shopper_is_handed_off_at_once_too(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEMO_SIGNIN_ENV, "1")
    session = "e" * 32
    signed = sign_shopper(session_key(KEY), session, DEMO_SHOPPER)
    cookie = f"{COOKIE}={sign(session_key(KEY), session)}; {SHOPPER_COOKIE}={signed}"
    response = stack.client.post("/api/chat", json={"message": ASK}, headers={"cookie": cookie})
    assert response.status_code == 200
    assert events(response.text) == [("handoff", {}), ("done", {})]
    assert idle(stack)


def test_the_handoff_event_data_is_empty_not_a_transcript_or_identity(stack: Stack) -> None:
    response = stack.client.post("/api/chat", json={"message": ASK})
    assert events(response.text)[0] == ("handoff", {})
    assert DEMO_SHOPPER not in response.text and "notice" not in response.text


def test_chat_py_never_reaches_the_handoff_desk() -> None:
    tree = ast.parse(Path(chat_module.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            modules.add(base)
            modules |= {f"{base}.{alias.name}" for alias in node.names}
    handoff = {m for m in modules if "handoff" in m.lower()}
    assert handoff <= {
        "northwind_gateway.handoff_trigger",
        "northwind_gateway.handoff_trigger.asks_for_person",
    }
    assert not {m for m in modules if m.startswith("northwind_backend")}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert not {n for n in names if "desk" in n.lower() or "queue" in n.lower()}
