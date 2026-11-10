"""The assistant's tool surface is frozen (CC-10 C6, C7'; the user's Q5: fixed now, and #12 and #16
add tools before RECORD-ALL, each changing this list and re-recording).

"Frozen" is the transport, the server name, the tool name, its description and its schema, as the
CLI sends them to the model. A change here changes every recorded request, so it is a reviewed
change with a re-record, never a drive-by.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.turn import assistant_options, stream_answer
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("SA-02")]

REPO = Path(__file__).resolve().parents[3]
# (name, sha256 of the description, sha256 of the canonical input schema), as sent.
GOLDEN = [
    (
        "mcp__northwind-rag__retrieve_context",
        "13fb0ff71833847d3e516c26c831160281a8eb974e5ae920e9a11a97244b8483",
        "417da8f2c6b94ea7f2a3fbb85d0e309c5378b35ce28ca011eb616ce3a0e7d16a",
    )
]
RECORDED_SUITES = ("assistant-smoke", "assistant-redteam")


class One:
    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        return [RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")]


def surface(tools: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    def digest(value: Any) -> str:
        text = (
            value
            if isinstance(value, str)
            else json.dumps(value, sort_keys=True, separators=(",", ":"))
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    return [(t["name"], digest(t["description"]), digest(t["input_schema"])) for t in tools]


def test_the_assistant_tool_surface_is_frozen(tmp_path: Path) -> None:
    with ScriptedLLM() as llm, serve_retriever(One(), Caller) as (url, _):
        llm.reply_stream("ok")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path)

        async def go() -> None:
            async for _ in stream_answer("hi", Caller(region="US"), RagEndpoint(url, KEY), runtime):
                pass

        asyncio.run(go())
    assert surface(llm.requests[0]["body"]["tools"]) == GOLDEN


def test_the_transport_and_server_are_frozen(tmp_path: Path) -> None:
    options = assistant_options(
        Caller(region="US"), "http://127.0.0.1:9/mcp", "t", agent_runtime(root=tmp_path)
    )
    assert options.mcp_servers == {
        "northwind-rag": {
            "type": "http",
            "url": "http://127.0.0.1:9/mcp",
            "headers": {"Authorization": "Bearer t"},
        }
    }
    assert options.allowed_tools == ["mcp__northwind-rag__retrieve_context"]
    assert options.tools == []


@pytest.mark.parametrize("suite", RECORDED_SUITES)
def test_every_recorded_request_offers_the_frozen_surface(suite: str) -> None:
    """The recordings are what replays: they must have been made on this surface (D)."""
    cassettes = sorted((REPO / "cassettes" / suite).glob("*.json"))
    assert cassettes, f"no cassettes in {suite}"
    for path in cassettes:
        raw = json.loads(path.read_text(encoding="utf-8"))["match"]["body"]
        body = json.loads(raw) if isinstance(raw, str) else raw
        assert surface(body["tools"]) == GOLDEN, path.name
