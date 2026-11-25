"""Bundled into row #27 (decision 0021 rule 6): the BE-07, BE-01, BE-02 and BE-05 checks of what the
model reads search the whole request body except its top-level `metadata`. The CLI puts a random
`metadata.user_id` (a device hash and a session UUID) in every request, so a check like
`"1111" not in json.dumps(bodies)` failed at random (it failed #11's `make ci`). It is the only
field left out: an allow-list of block types would miss a card in any other part of the request.
Every copy of `model_text` is held to the same cases."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.req("BE-07"), pytest.mark.req("PAY-05")]

TESTS = Path(__file__).resolve().parents[1]
ModelText = Callable[[Any], str]


DRIVERS = {
    "care": "_driver",
    "orchestrator": "_driver",
    "backend_audit": "_driver",
    "orders_place": "_placement",
}


@pytest.fixture(params=sorted(DRIVERS))
def model_text(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> ModelText:
    """Each folder's own copy, loaded by path as its own package (the drivers import siblings)."""
    name = f"model_text_{request.param}"
    folder = TESTS / request.param
    spec = importlib.util.spec_from_file_location(
        name, folder / "__init__.py", submodule_search_locations=[str(folder)]
    )
    assert spec is not None
    monkeypatch.setitem(sys.modules, name, importlib.util.module_from_spec(spec))
    try:
        found: ModelText = importlib.import_module(f"{name}.{DRIVERS[request.param]}").model_text
    finally:
        for loaded in [m for m in sys.modules if m.startswith(f"{name}.")]:
            del sys.modules[loaded]
    return found


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
                        "id": "toolu_a",
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
                        "tool_use_id": "toolu_a",
                        "content": [{"type": "text", "text": "on hold"}],
                    }
                ],
            },
        ],
    }


def test_a_1111_only_in_the_clis_metadata_is_not_model_input(model_text: ModelText) -> None:
    request = {"body": body(user_id="ab1111cd")}
    assert "1111" in json.dumps([request["body"]])  # what the old whole-body check flagged
    text = model_text([request])
    assert "1111" not in text
    for read in ("You are the care agent.", "Hello, my payment failed.", "Let me look.", "on hold"):
        assert read in text
    assert "O000001" in text  # tool_use input is model input


def test_a_card_fragment_in_a_message_is_still_found(model_text: ModelText) -> None:
    request = {"body": body(user_id="abcd", text="my number starts 4111 11")}
    text = model_text([request])
    assert "4111" in text
    assert "411111" in "".join(ch for ch in text if ch.isdigit())


def test_a_string_body_or_plain_tool_result_is_read_too(model_text: ModelText) -> None:
    plain = body(user_id="abcd")
    plain["system"] = "System text 4111"
    plain["messages"][2]["content"][0]["content"] = "result 1111"
    assert "4111" in model_text([{"body": plain}]) and "1111" in model_text([{"body": plain}])


CARD = "4111 1111 1111 1111"
HIDING_PLACES: dict[str, Callable[[dict[str, Any]], Any]] = {
    "thinking_block": lambda b: b["messages"][1]["content"].append(
        {"type": "thinking", "thinking": CARD, "signature": "x"}
    ),
    "document_block": lambda b: b["messages"][0].update(
        content=[{"type": "document", "source": {"type": "text", "data": CARD}, "title": "t"}]
    ),
    "unknown_block": lambda b: b["messages"][0].update(content=[{"type": "new", "body": [CARD]}]),
    "unknown_block_in_a_tool_result": lambda b: b["messages"][2]["content"][0].update(
        content=[{"type": "search_result", "title": CARD}]
    ),
    "tool_use_name": lambda b: b["messages"][1]["content"][1].update(name=CARD),
    "tool_use_id": lambda b: b["messages"][1]["content"][1].update(id=CARD),
    "tool_use_input_key": lambda b: b["messages"][1]["content"][1].update(input={CARD: 1}),
    "tool_description": lambda b: b.update(tools=[{"name": "t", "description": CARD}]),
    "tool_schema_enum": lambda b: b.update(tools=[{"input_schema": {"enum": [CARD]}}]),
    "stop_sequence": lambda b: b.update(stop_sequences=[CARD]),
    "new_top_level_field": lambda b: b.update(context_management={"note": CARD}),
    "nested_metadata": lambda b: b["messages"][1]["content"][1].update(
        input={"metadata": {"user_id": CARD}}
    ),
}


@pytest.mark.parametrize("place", sorted(HIDING_PLACES))
def test_a_card_anywhere_but_the_top_level_metadata_is_found(
    model_text: ModelText, place: str
) -> None:
    request = {"body": body(user_id="abcd")}
    assert CARD not in model_text([request])
    HIDING_PLACES[place](request["body"])
    assert CARD in model_text([request])


def test_a_bare_number_is_found(model_text: ModelText) -> None:
    request = {"body": body(user_id="abcd")}
    request["body"]["messages"][1]["content"][1]["input"] = {"n": 4111111111111111}
    assert "4111111111111111" in model_text([request])


@pytest.mark.parametrize(
    "spelled", ["4111 1111\n1111 1111", 'say "4111"', "４１１１ １１１１", "4111\u200b1111"]
)
def test_text_is_searched_as_the_model_reads_it_not_json_escaped(
    model_text: ModelText, spelled: str
) -> None:
    assert spelled in model_text([{"body": body(user_id="abcd", text=spelled)}])


def test_the_request_itself_is_not_changed(model_text: ModelText) -> None:
    request = {"body": body(user_id="ab1111cd")}
    before = json.dumps(request)
    model_text([request])
    assert json.dumps(request) == before
