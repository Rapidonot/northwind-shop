"""ScriptedLLM's keyed scripts (row #24; control surface, approved by the user on conditions).

With no route, every request takes the next reply from one queue, as before (pinned here). A route
keeps its own queue for requests whose system prompt contains its marker, so agents that run at the
same time each get their own script."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from northwind_common.llm import PLACEHOLDER_API_KEY
from northwind_llm_replay.scripted import ScriptedLLM, system_text

pytestmark = pytest.mark.req("BE-01")


def ask(llm: ScriptedLLM, system: Any = None) -> str:
    body: dict[str, Any] = {
        "model": "claude-haiku-5-5",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
    }
    if system is not None:
        body["system"] = system
    response = httpx.post(
        f"{llm.url}/v1/messages", json=body, headers={"x-api-key": PLACEHOLDER_API_KEY}
    )
    if response.status_code != 200:
        return f"error {response.status_code}"
    return str(response.json()["content"][0]["text"])


def test_with_no_route_replies_are_served_in_order_whatever_the_system_prompt() -> None:
    with ScriptedLLM() as llm:
        llm.reply("one").reply("two").reply("three")
        assert ask(llm, "You are A") == "one"
        assert ask(llm, "You are B") == "two"
        assert ask(llm) == "three"
        assert ask(llm, "You are A") == "error 400"  # script empty


def test_a_route_serves_its_own_queue_and_the_rest_take_the_default() -> None:
    with ScriptedLLM() as llm:
        llm.reply("default-1").reply("default-2")
        llm.route("You are A").reply("a-1").reply("a-2")
        llm.route("You are B").reply("b-1")
        assert ask(llm, "You are B, a test agent") == "b-1"
        assert ask(llm, "Someone else") == "default-1"
        assert ask(llm, "You are A, a test agent") == "a-1"
        assert ask(llm, "You are A, a test agent") == "a-2"
        assert ask(llm, "You are A, a test agent") == "error 400"  # A's queue is empty
        assert ask(llm) == "default-2"


def test_a_route_matches_a_system_prompt_given_as_blocks() -> None:
    blocks = [{"type": "text", "text": "Preamble."}, {"type": "text", "text": "You are A."}]
    assert system_text({"system": blocks}) == "Preamble.\nYou are A."
    with ScriptedLLM() as llm:
        llm.route("You are A").reply("a-1")
        assert ask(llm, blocks) == "a-1"


def test_the_first_matching_route_wins_and_a_route_needs_a_marker() -> None:
    with ScriptedLLM() as llm:
        llm.route("You are").reply("first")
        llm.route("You are A").reply("second")
        assert ask(llm, "You are A") == "first"
        with pytest.raises(ValueError):
            llm.route("")
