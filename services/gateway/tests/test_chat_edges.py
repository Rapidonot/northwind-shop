"""More ways into POST /api/chat, and every error code over SSE (SA-02; hard rules 1, 10)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_assistant.caller import Caller
from northwind_assistant.tools import ALLOWED_TOOL
from northwind_common.llm import agent_runtime
from northwind_gateway.app import app
from northwind_gateway.chat import (
    CARD_MESSAGE,
    INVALID_MESSAGE,
    MAX_MESSAGE_CHARS,
    ChatServices,
    chat_services,
)
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("SA-02")

CARD = "4242" * 4
MARKER = "PLANTED-MARKER-31bb"
SOURCE = RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")
FIXED_CODES = {"retrieval_failed", "model_unavailable", "replay_miss", "turn_limit", "internal"}
REFUSAL = {"isError": True, "errorCategory": "validation", "isRetryable": False}


class Retriever:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"postgresql://user:hunter2@db/prod {MARKER}")
        return [SOURCE]


class Stack:
    def __init__(self, llm: ScriptedLLM, client: TestClient, retriever: Retriever) -> None:
        self.llm, self.client, self.retriever = llm, client, retriever


def make_stack(tmp_path: Path, retriever: Retriever) -> Iterator[Stack]:
    with ScriptedLLM() as llm:
        services = ChatServices(retriever, agent_runtime(base_url=llm.url, root=tmp_path / "rt"))
        app.dependency_overrides[chat_services] = lambda: services
        try:
            yield Stack(llm, TestClient(app), retriever)
        finally:
            app.dependency_overrides.clear()


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    yield from make_stack(tmp_path, Retriever())


@pytest.fixture
def broken_stack(tmp_path: Path) -> Iterator[Stack]:
    yield from make_stack(tmp_path, Retriever(fail=True))


def events(text: str) -> list[tuple[str, Any]]:
    parsed = []
    for frame in text.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if not line.startswith(":"))
        if "event" in lines:
            parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def refusal_with(message: str) -> dict[str, Any]:
    return {**REFUSAL, "message": message}


# --- every error code, over SSE --------------------------------------------------------------


def test_a_cassette_miss_is_the_replay_miss_code(stack: Stack) -> None:
    error = {"type": "invalid_request_error", "message": f"cassette miss {MARKER}"}
    text = json.dumps({"type": "error", "error": error})
    for _ in range(4):
        stack.llm.reply_raw(400, text)
    response = stack.client.post("/api/chat", json={"message": "hi"})
    assert events(response.text) == [("error", {"code": "replay_miss"})]
    assert MARKER not in response.text


def test_a_failed_search_is_the_retrieval_failed_code(broken_stack: Stack) -> None:
    broken_stack.llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"})
    broken_stack.llm.reply_stream("I can't look that up.")
    response = broken_stack.client.post("/api/chat", json={"message": "hi"})
    names = [name for name, _ in events(response.text)]
    assert events(response.text)[-1] == ("error", {"code": "retrieval_failed"})
    assert "done" not in names and "citations" not in names
    assert MARKER not in response.text and "hunter2" not in response.text


@pytest.mark.parametrize("scenario", ["upstream", "turn_limit", "retrieval"])
def test_an_error_event_has_only_a_code_and_nothing_follows_it(
    stack: Stack, broken_stack: Stack, scenario: str
) -> None:
    if scenario == "upstream":
        use = stack
        for _ in range(4):
            use.llm.reply_raw(401, json.dumps({"type": "error", "error": {"message": MARKER}}))
    elif scenario == "turn_limit":
        use = stack
        for n in range(6):
            use.llm.reply_tool_use(ALLOWED_TOOL, {"query": f"q{n}"}, tool_id=f"toolu_{n}")
    else:
        use = broken_stack
        use.llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"})
        use.llm.reply_stream("sorry")
    parsed = events(use.client.post("/api/chat", json={"message": "hi"}).text)
    assert parsed[-1][0] == "error"
    assert set(parsed[-1][1]) == {"code"} and parsed[-1][1]["code"] in FIXED_CODES
    assert [name for name, _ in parsed].count("error") == 1


def test_a_second_request_is_independent_of_the_first(stack: Stack) -> None:
    stack.llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"}, tool_id="t1")
    stack.llm.reply_stream("Wash cold [FAQ-001].")
    stack.llm.reply_stream("No sources used [FAQ-001].")
    first = events(stack.client.post("/api/chat", json={"message": "hi"}).text)
    second = events(stack.client.post("/api/chat", json={"message": "hi"}).text)
    assert ("citations", {"source_ids": ["FAQ-001"]}) in first
    assert ("citations", {"source_ids": []}) in second


# --- the request body ------------------------------------------------------------------------


def test_the_length_limit_is_exact(stack: Stack) -> None:
    stack.llm.reply_stream("ok")
    ok = stack.client.post("/api/chat", json={"message": "a" * MAX_MESSAGE_CHARS})
    assert ok.status_code == 200 and events(ok.text)[-1] == ("done", {})
    too_long = stack.client.post("/api/chat", json={"message": "a" * (MAX_MESSAGE_CHARS + 1)})
    assert too_long.status_code == 422 and too_long.json() == refusal_with(INVALID_MESSAGE)


MORE_REJECTED: list[tuple[str, dict[str, Any], str]] = [
    ("null message", {"json": {"message": None}}, INVALID_MESSAGE),
    ("number message", {"json": {"message": 4242424242424242}}, INVALID_MESSAGE),
    ("no message", {"json": {}}, INVALID_MESSAGE),
    ("null body", {"json": None}, INVALID_MESSAGE),
    ("no body", {}, INVALID_MESSAGE),
    ("card as a field name", {"json": {"message": "hi", CARD: "x"}}, INVALID_MESSAGE),
    ("card in a nested list", {"json": {"message": ["hi", CARD]}}, INVALID_MESSAGE),
    (
        "dotted card",
        {"json": {"message": ".".join([CARD[i : i + 4] for i in range(0, 16, 4)])}},
        CARD_MESSAGE,
    ),
    (
        "newline card",
        {"json": {"message": "\n".join([CARD[i : i + 4] for i in range(0, 16, 4)])}},
        CARD_MESSAGE,
    ),
    (
        "double-space card",
        {"json": {"message": "  ".join([CARD[i : i + 4] for i in range(0, 16, 4)])}},
        CARD_MESSAGE,
    ),
    ("fullwidth card", {"json": {"message": "４２４２" * 4}}, CARD_MESSAGE),
    (
        "escaped digits",
        {
            "content": '{"message": "' + "".join(f"\\u{ord(c):04x}" for c in CARD) + '"}',
            "headers": {"content-type": "application/json"},
        },
        CARD_MESSAGE,
    ),
    (
        "card at the length limit",
        {"json": {"message": "x" * (MAX_MESSAGE_CHARS - 16) + CARD}},
        CARD_MESSAGE,
    ),
]


@pytest.mark.parametrize(
    ("name", "request_args", "message"), MORE_REJECTED, ids=[r[0] for r in MORE_REJECTED]
)
def test_more_refused_bodies_are_fixed_and_reach_no_model(
    stack: Stack,
    caplog: pytest.LogCaptureFixture,
    name: str,
    request_args: dict[str, Any],
    message: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    response = stack.client.post("/api/chat", **request_args)
    assert response.status_code == 422
    assert response.json() == refusal_with(message)
    assert "input" not in response.text and CARD not in response.text + caplog.text
    assert stack.llm.requests == [] and stack.retriever.calls == 0


@pytest.mark.parametrize("method", ["get", "put", "delete", "patch"])
def test_other_methods_do_not_echo_or_call_a_model(stack: Stack, method: str) -> None:
    response = getattr(stack.client, method)("/api/chat?message=" + CARD)
    assert response.status_code in {404, 405}
    assert CARD not in response.text and stack.llm.requests == []


def test_a_refused_card_never_reaches_the_response_headers(stack: Stack) -> None:
    response = stack.client.post("/api/chat", json={"message": CARD})
    assert CARD not in json.dumps(dict(response.headers))
