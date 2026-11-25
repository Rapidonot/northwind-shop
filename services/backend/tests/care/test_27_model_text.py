"""Bundled into row #27 (decision 0021 rule 6): the BE-07 card checks search only what the model
reads, never the whole request body. The CLI puts a random `metadata.user_id` (a device hash and a
session UUID) in every request, so a check like `"1111" not in json.dumps(bodies)` failed at random
(it failed #11's `make ci`). `model_text` is the system prompt and the message contents: text,
`tool_use` input and `tool_result` content. No metadata, model name or ID."""

from __future__ import annotations

import json
from typing import Any

import pytest

from ._driver import model_text

pytestmark = [pytest.mark.req("BE-07"), pytest.mark.req("PAY-05")]


def body(*, user_id: str, text: str = "Hello, my payment failed.") -> dict[str, Any]:
    return {
        "model": "claude-sonnet-5-5",
        "metadata": {"user_id": json.dumps({"device_id": user_id, "session_id": "x"})},
        "system": [{"type": "text", "text": "You are the care agent."}],
        "messages": [
            {"role": "user", "content": text},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me look."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1111",
                        "name": "get_order",
                        "input": {"o": "O000001"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1111",
                        "content": [{"type": "text", "text": "on hold"}],
                    }
                ],
            },
        ],
    }


def test_a_1111_only_in_the_clis_metadata_is_not_model_input() -> None:
    request = {"body": body(user_id="ab1111cd")}
    assert "1111" in json.dumps([request["body"]])  # what the old whole-body check flagged
    text = model_text([request])
    assert "1111" not in text
    for read in ("You are the care agent.", "Hello, my payment failed.", "Let me look.", "on hold"):
        assert read in text
    assert "O000001" in text  # tool_use input is model input


def test_a_card_fragment_in_a_message_is_still_found() -> None:
    request = {"body": body(user_id="abcd", text="my number starts 4111 11")}
    text = model_text([request])
    assert "4111" in text
    assert "411111" in "".join(ch for ch in text if ch.isdigit())


def test_a_string_body_or_plain_tool_result_is_read_too() -> None:
    plain = body(user_id="abcd")
    plain["system"] = "System text 4111"
    plain["messages"][2]["content"][0]["content"] = "result 1111"
    assert "4111" in model_text([{"body": plain}]) and "1111" in model_text([{"body": plain}])
