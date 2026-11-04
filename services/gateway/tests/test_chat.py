"""POST /api/chat through the real CLI and a scripted model (SA-02; hard rules 1, 7, 10)."""

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
from northwind_gateway.chat import CARD_MESSAGE, INVALID_MESSAGE, ChatServices, chat_services
from northwind_gateway.settings import current_caller
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("SA-02")

CARD = "4242" * 4
MARKER = "PLANTED-MARKER-9c1e"
FIXED_CODES = {"retrieval_failed", "model_unavailable", "replay_miss", "turn_limit", "internal"}
SOURCE = RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")


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
    with ScriptedLLM() as llm:
        services = ChatServices(retriever, agent_runtime(base_url=llm.url, root=tmp_path / "rt"))
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


def test_an_answer_streams_as_ordered_events(stack: Stack) -> None:
    stack.llm.reply_tool_use(ALLOWED_TOOL, {"query": "wash"})
    stack.llm.reply_stream("Wash it ", "cold [FAQ-001].")
    response = stack.client.post("/api/chat", json={"message": "How do I wash it?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert events(response.text) == [
        ("token", {"text": "Wash it "}),
        ("token", {"text": "cold [FAQ-001]."}),
        ("citations", {"source_ids": ["FAQ-001"]}),
        ("done", {}),
    ]


def test_the_region_comes_from_server_config_not_the_request(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEV_SHOPPER_REGION", "EU")
    stack.llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"})
    stack.llm.reply_stream("ok")
    stack.client.post(
        "/api/chat?region=UK&audience=staff",
        json={"message": "hi"},
        headers={"X-Region": "UK", "X-Audience": "staff"},
    )
    assert stack.retriever.callers == [Caller(region="EU", audience="shopper")]


@pytest.mark.parametrize("region", ["ALL", "", "us", "AU"])
def test_a_bad_server_region_is_refused(monkeypatch: pytest.MonkeyPatch, region: str) -> None:
    monkeypatch.setenv("DEV_SHOPPER_REGION", region)
    with pytest.raises(ValueError, match="DEV_SHOPPER_REGION"):
        current_caller()


REJECTED_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("card in message", {"json": {"message": f"my card is {CARD}"}}),
    ("spaced card", {"json": {"message": " ".join([CARD[:4]] * 4)}}),
    ("card in an extra field", {"json": {"message": "hi", "note": CARD}}),
    ("audience field", {"json": {"message": CARD, "audience": "staff"}}),
    ("region field", {"json": {"message": CARD, "region": "UK"}}),
    ("wrong type", {"json": {"message": {"card": CARD}}}),
    ("list body", {"json": [CARD]}),
    ("too long", {"json": {"message": CARD + "x" * 3000}}),
    (
        "malformed JSON",
        {"content": f'{{"message": "{CARD}"', "headers": {"content-type": "application/json"}},
    ),
    ("not JSON", {"content": CARD, "headers": {"content-type": "text/plain"}}),
    ("empty message", {"json": {"message": ""}}),
]


@pytest.mark.parametrize(
    ("name", "request_args"), REJECTED_BODIES, ids=[r[0] for r in REJECTED_BODIES]
)
def test_a_rejected_body_is_never_echoed_or_logged_and_reaches_no_model(
    stack: Stack, caplog: pytest.LogCaptureFixture, name: str, request_args: dict[str, Any]
) -> None:
    caplog.set_level(logging.DEBUG)
    response = stack.client.post("/api/chat", **request_args)
    assert response.status_code == 422
    assert CARD not in response.text and CARD[:4] not in response.text
    expected = CARD_MESSAGE if name in {"card in message", "spaced card"} else INVALID_MESSAGE
    assert response.json() == {
        "isError": True,
        "errorCategory": "validation",
        "isRetryable": False,
        "message": expected,
    }
    assert CARD not in caplog.text and CARD[:4] not in caplog.text
    assert stack.llm.requests == [] and stack.retriever.callers == []


def test_an_upstream_failure_is_only_a_fixed_code(stack: Stack) -> None:
    error = {"type": "error", "error": {"type": "invalid_request_error", "message": MARKER}}
    for _ in range(4):
        stack.llm.reply_raw(400, json.dumps(error))
    response = stack.client.post("/api/chat", json={"message": "hi"})
    assert events(response.text) == [("error", {"code": "model_unavailable"})]
    assert MARKER not in response.text


def test_the_turn_cap_is_a_fixed_code(stack: Stack) -> None:
    for n in range(6):
        stack.llm.reply_tool_use(ALLOWED_TOOL, {"query": f"q{n}"}, tool_id=f"toolu_{n}")
    response = stack.client.post("/api/chat", json={"message": "hi"})
    assert events(response.text) == [("error", {"code": "turn_limit"})]


def test_an_unexpected_exception_is_only_a_fixed_code(
    stack: Stack, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def explode(*_: Any, **__: Any) -> Any:
        raise RuntimeError(f"secret {MARKER}")

    monkeypatch.setattr("northwind_gateway.chat.stream_answer", explode)
    response = stack.client.post("/api/chat", json={"message": "hi"})
    [(name, data)] = events(response.text)
    assert name == "error" and data == {"code": "internal"} and data["code"] in FIXED_CODES
    assert MARKER not in response.text and MARKER not in caplog.text


def test_an_accepted_message_is_not_logged(stack: Stack, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    stack.llm.reply_stream("hello")
    stack.client.post("/api/chat", json={"message": f"do you stock {MARKER}?"})
    assert MARKER not in caplog.text
