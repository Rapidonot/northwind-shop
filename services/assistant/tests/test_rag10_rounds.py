"""RAG-10: no answer needs more than 3 retrieval rounds, and a thin first search can be refined.

The cap itself is enforced and tested in `test_first_request.py` and mcp-rag's `test_search_cap.py`.
Here: every recorded assistant turn stayed within it (the server's first search is round 1, decision
0007), and a refinement works end to end on a scripted model. No cassette is written, no spend.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.tools import ALLOWED_TOOL, MAX_SEARCHES, NO_SOURCES
from northwind_assistant.turn import Citations, TextDelta, TurnEvent, stream_answer
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("RAG-10")

REPO = Path(__file__).resolve().parents[3]
# The suites that record the assistant. One going missing must fail, not shrink the evidence.
ASSISTANT_SUITES = {
    "a2a-05-partner",
    "assistant-cart",
    "assistant-intents",
    "assistant-orders",
    "assistant-redteam",
    "assistant-smoke",
    "eval-answers",
    # Row #18 (RECORD-ALL): the variance runs record assistant turns into their own suites.
    "eval-answers-r2",
    "eval-answers-r3",
}


# ---- every recorded turn -------------------------------------------------------------------------


def _request(cassette: dict[str, Any]) -> dict[str, Any]:
    raw = cassette["match"]["body"]
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    assert isinstance(parsed, dict)
    return parsed


def _is_assistant(request: dict[str, Any]) -> bool:
    """It offers the search tool, or its history holds a search. Both, or it is not a turn."""
    offers = ALLOWED_TOOL in {tool.get("name") for tool in request.get("tools", [])}
    searched = any(
        _is_search(block) for message in request["messages"] for block in _blocks(message)
    )
    assert offers or not searched, "a turn that searched without offering the tool"
    return offers


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message["content"]
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


def _is_search(block: dict[str, Any]) -> bool:
    return bool(block.get("type") == "tool_use" and block.get("name") == ALLOWED_TOOL)


def _response_searches(sse: str) -> int:
    events: list[dict[str, Any]] = [
        json.loads(line[5:]) for line in sse.splitlines() if line.startswith("data:")
    ]
    return sum(
        event.get("type") == "content_block_start" and _is_search(event["content_block"])
        for event in events
    )


def searches(cassette: dict[str, Any]) -> int:
    """Searches this turn had asked for once this response ended: the server's own, every one the
    model called before, and those in this response. A refused call counts too, so this can only
    overstate the rounds. A later request carries the earlier ones, so each turn's last cassette
    holds its total."""
    messages = _request(cassette)["messages"]
    own: int = sum(
        block.get("text", "").count("<sources>")
        for message in messages
        if message["role"] == "user"
        for block in _blocks(message)
        if block.get("type") == "text"
    )
    asked = sum(_is_search(block) for message in messages for block in _blocks(message))
    assert own == 1, "the server's first search is in every recorded turn (decision 0007)"
    return own + asked + _response_searches(cassette["body"])


def _recorded() -> dict[str, dict[str, Any]]:
    found = {}
    for path in sorted((REPO / "cassettes").glob("*/*.json")):
        text = path.read_text(encoding="utf-8")
        if path.parent.name not in ASSISTANT_SUITES:
            assert ALLOWED_TOOL not in text, f"{path}: an assistant turn in another suite"
            continue
        cassette = json.loads(text)
        if _is_assistant(_request(cassette)):
            assert cassette["status"] == 200 and cassette["kind"] == "sse", path
            found[f"{path.parent.name}/{path.name}"] = cassette
    return found


def test_no_recorded_turn_searches_more_than_three_times() -> None:
    recorded = _recorded()
    per_suite = Counter(name.split("/")[0] for name in recorded)
    assert set(per_suite) == ASSISTANT_SUITES
    counts = {name: searches(cassette) for name, cassette in recorded.items()}
    assert MAX_SEARCHES == 3
    assert {name: n for name, n in counts.items() if n > 3} == {}
    assert max(counts.values()) > 1, "no recorded refinement: the count would prove nothing"


def _cassette(called: int, in_response: int) -> dict[str, Any]:
    search = {"type": "tool_use", "id": "t", "name": ALLOWED_TOOL, "input": {"query": "q"}}
    start = {"type": "content_block_start", "index": 0, "content_block": search}
    return {
        "match": {
            "body": {
                "tools": [{"name": ALLOWED_TOOL}],
                "messages": [
                    {"role": "user", "content": "<sources>\n</sources>"},
                    {"role": "assistant", "content": [search] * called},
                ],
            }
        },
        "body": f"event: x\ndata: {json.dumps(start)}\n\n" * in_response,
    }


@pytest.mark.parametrize(
    ("called", "in_response", "total"), [(0, 0, 1), (2, 0, 3), (3, 0, 4), (2, 1, 4), (0, 3, 4)]
)
def test_the_counter_sees_a_fourth_search(called: int, in_response: int, total: int) -> None:
    assert searches(_cassette(called, in_response)) == total


# ---- a refinement, end to end --------------------------------------------------------------------


class ThinThenFound:
    """Nothing for the shopper's own words, a guide for the refined query."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Caller]] = []

    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        self.calls.append((query, caller))
        if query != "washing merino wool":
            return []
        return [RetrievedChunk("c9", "document", "CG-MERINO", "care_guide", "Wash merino cold.")]


def test_a_thin_first_search_is_refined_and_the_answer_cites_the_second(tmp_path: Path) -> None:
    retriever, caller = ThinThenFound(), Caller(region="US")
    with ScriptedLLM() as llm:
        llm.reply_tool_use(ALLOWED_TOOL, {"query": "washing merino wool"})
        llm.reply_stream("Wash it ", "cold [CG-MERINO].")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path)

        async def collect(rag: RagEndpoint) -> list[TurnEvent]:
            message = "my sweater smells, what now?"
            return [e async for e in stream_answer(message, caller, rag, runtime)]

        with serve_retriever(retriever, Caller) as (url, _):
            events = asyncio.run(collect(RagEndpoint(url, KEY)))

        first, second = (json.dumps(r["body"]["messages"]) for r in llm.requests)
    assert retriever.calls == [
        ("my sweater smells, what now?", caller),
        ("washing merino wool", caller),
    ]
    assert NO_SOURCES in first and "CG-MERINO" not in first
    assert "CG-MERINO" in second
    assert events == [
        TextDelta("Wash it "),
        TextDelta("cold [CG-MERINO]."),
        Citations(("CG-MERINO",)),
    ]
