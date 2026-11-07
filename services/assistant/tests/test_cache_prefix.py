"""Guards on the recorded `assistant-smoke` requests (RAG-13, decision 0007).

The Agent SDK's CLI sets `cache_control` itself, so nothing in our code would fail if a CLI upgrade
or a volatile value in the prompt stopped caching. A prefix under the model's minimum is ignored
with no error either. These read the recording, which is the only proof of what was really sent,
and need no index, so they run in `make test`.

They read static files, so they only speak at record time: a silent upstream change between
recordings shows at the next one. At runtime, `cache_hit_rate` in the telemetry line has no
threshold yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.tools import ALLOWED_TOOL

REPO = Path(__file__).resolve().parents[3]
SUITE = REPO / "cassettes" / "assistant-smoke"
QUESTIONS = json.loads((REPO / "apps/portal/e2e/questions.json").read_text(encoding="utf-8"))
# The 8 review/spec contradiction questions RAG-03 added to this suite (test_smoke_e2e.py).
CONTRADICTION_QUESTIONS = [
    row["question"]
    for row in json.loads(
        (REPO / "services/assistant/tests/contradictions.json").read_text("utf-8")
    )
]
# The live docs list 512 for Claude Sonnet 5.5, read 2026-11-04.
MIN_CACHEABLE_TOKENS = 512
BILLING_HEADER = "x-anthropic-billing-header"
INJECTION_REVIEW = "R00712"


def recorded() -> list[dict[str, Any]]:
    cassettes = [json.loads(path.read_text(encoding="utf-8")) for path in SUITE.glob("*.json")]
    return sorted(cassettes, key=lambda cassette: (cassette["recorded_at"], cassette["seq"]))


def body(cassette: dict[str, Any]) -> dict[str, Any]:
    raw = cassette["match"]["body"]
    parsed: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else raw
    return parsed


def first_turn(cassette: dict[str, Any]) -> str:
    content = body(cassette)["messages"][0]["content"]
    return content if isinstance(content, str) else content[0]["text"]


def tool_calls(sse: str) -> list[str]:
    """Tools a response called. Only block starts count: `stop_reason` also says `tool_use`."""
    names = []
    for line in sse.splitlines():
        if line.startswith("data:"):
            event = json.loads(line[5:])
            block = event.get("content_block") or {}
            if event.get("type") == "content_block_start" and block.get("type") == "tool_use":
                names.append(block["name"])
    return names


def system_text(cassette: dict[str, Any]) -> list[str]:
    blocks = body(cassette)["system"]
    return [block["text"] for block in blocks if not block["text"].startswith(BILLING_HEADER)]


@pytest.mark.req("RAG-13")
def test_every_request_marks_the_end_of_the_system_prompt_for_caching() -> None:
    cassettes = recorded()
    assert cassettes
    for cassette in cassettes:
        assert body(cassette)["system"][-1].get("cache_control") == {"type": "ephemeral"}


@pytest.mark.req("RAG-13")
def test_the_system_prompt_holds_nothing_that_varies_between_questions() -> None:
    """All recorded with one region. The billing header is left out of this comparison: the SA-02
    recording had a different one per question and still read the prefix, and the current
    recording has one value, so the cache-read guard below is what would catch a change there."""
    first, *rest = recorded()
    assert rest
    assert all(system_text(cassette) == system_text(first) for cassette in rest)
    assert all(body(cassette)["tools"] == body(first)["tools"] for cassette in rest)


@pytest.mark.req("RAG-13")
@pytest.mark.req("NFR-16")
def test_later_questions_read_the_prefix_from_cache() -> None:
    """Below the minimum the markers do nothing and report nothing, so at record time this is the
    only alarm. The first call may be warm too, if an unchanged prompt was recorded minutes ago."""
    _, *rest = recorded()
    assert rest
    for cassette in rest:
        assert cassette["usage"]["cache_read_input_tokens"] >= MIN_CACHEABLE_TOKENS


@pytest.mark.req("SA-02")
def test_the_recording_holds_only_this_design() -> None:
    """Every question is there, in the fenced format, and no older recording is left behind. The
    suite is the 5 portal questions plus RAG-03's 8 contradiction questions, each exactly once."""
    questions = [*QUESTIONS.values(), *CONTRADICTION_QUESTIONS]
    assert len(questions) == 13 and len(set(questions)) == 13
    turns = [first_turn(cassette) for cassette in recorded() if cassette["seq"] == 0]
    assert len(turns) == len(questions)
    assert all("<sources>" in turn and "<shopper_message>" in turn for turn in turns)
    for question in questions:
        wanted = f"<shopper_message>\n{question}\n</shopper_message>"
        assert sum(turn.endswith(wanted) for turn in turns) == 1


@pytest.mark.req("SA-02")
@pytest.mark.req("NFR-03")
def test_the_injection_question_really_put_the_planted_review_in_front_of_the_model() -> None:
    [turn] = [
        first_turn(cassette)
        for cassette in recorded()
        if cassette["seq"] == 0 and QUESTIONS["injection"] in first_turn(cassette)
    ]
    assert f'<source id="{INJECTION_REVIEW}"' in turn
    assert "SYSTEM:" in turn  # the planted label, still inside its own block
    assert turn.index("SYSTEM:") < turn.index("</sources>") < turn.index("<shopper_message>")


@pytest.mark.req("SA-02")
@pytest.mark.req("NFR-03")
def test_no_recorded_response_calls_anything_but_the_one_tool() -> None:
    for cassette in recorded():
        assert [tool["name"] for tool in body(cassette)["tools"]] == [ALLOWED_TOOL]
        assert set(tool_calls(cassette["body"])) <= {ALLOWED_TOOL}


@pytest.mark.req("SA-02")
def test_the_tool_reader_sees_a_call_and_ignores_a_stop_reason() -> None:
    """No current recording calls a tool, so prove the guard above could see one."""
    start = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "tool_use", "id": "t", "name": "Bash", "input": {}},
    }
    delta = {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}
    sse = f"event: a\ndata: {json.dumps(start)}\n\nevent: b\ndata: {json.dumps(delta)}\n"
    assert tool_calls(sse) == ["Bash"]
