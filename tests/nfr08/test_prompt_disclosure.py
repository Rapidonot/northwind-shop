"""NFR-08, hard rule 11: the model is told it is an AI and must never claim to be a person.

The portal banner covers the page; this covers what the model says. It reads the assistant's prompt
and changes nothing, so no cassette moves. It checks the text the model gets, in every shopper
region, and that `turn.py` sends that text as the system prompt. Whether the model obeys is not
checked here: that needs a recorded answer, which this row does not make.
"""

from __future__ import annotations

import ast
import inspect
from typing import cast

import pytest

from northwind_assistant import prompt, turn
from northwind_rag.retrieval import SHOPPER_REGIONS, ShopperRegion

pytestmark = pytest.mark.req("NFR-08")

AI_LINE = "You are the AI shopping assistant for Northwind Outfitters."
SAYS_AI = "You are an AI"
NEVER_A_PERSON = "never claim to be a person"


def assert_discloses(text: str) -> None:
    assert text.startswith(AI_LINE), "the system prompt must open by saying the assistant is an AI"
    assert SAYS_AI in text
    assert NEVER_A_PERSON in text


def assert_passes_system_prompt(source: str) -> None:
    """Parsed, not searched: a comment or docstring holding the text doesn't count."""
    tree = ast.parse(source)
    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "northwind_assistant.prompt"
        and any(alias.name == "system_prompt" and alias.asname is None for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imported, "turn.py must import system_prompt from northwind_assistant.prompt"
    passed = [
        kw for kw in ast.walk(tree) if isinstance(kw, ast.keyword) and kw.arg == "system_prompt"
    ]
    assert len(passed) == 1, "turn.py must pass system_prompt= exactly once"
    value = passed[0].value
    assert (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "system_prompt"
    ), "turn.py must send prompt.system_prompt() to the model"


def turn_source() -> str:
    return inspect.getsource(turn)


@pytest.mark.parametrize("region", sorted(SHOPPER_REGIONS))
def test_system_prompt_says_it_is_an_ai_in_every_region(region: str) -> None:
    assert_discloses(prompt.system_prompt(cast(ShopperRegion, region)))


def test_turn_sends_the_checked_system_prompt() -> None:
    assert_passes_system_prompt(turn_source())
