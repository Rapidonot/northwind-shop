"""Cassette read L1 (row #21): a placement result's summary is built from the store's outcome,
never the model's text, which once called an already-placed order escalated."""

from __future__ import annotations

from pathlib import Path

import pytest

from ._placement import World, first_of
from .test_be05_place_scripted import HAPPY, place, script

pytestmark = pytest.mark.req("BE-05")


@pytest.mark.parametrize(
    ("kind", "tools", "text", "expected"),
    [
        ("clean", HAPPY, "The order was escalated, not placed.", "is placed."),
        (
            "stock_out",
            ["validate_order"],
            "The order was placed and paid.",
            "is escalated (out_of_stock).",
        ),
    ],
)
def test_the_summary_is_the_stores_outcome_not_the_models_words(
    tmp_path: Path, kind: str, tools: list[str], text: str, expected: str
) -> None:
    from northwind_llm_replay.scripted import ScriptedLLM

    world, row = World.make(), first_of(kind)
    with ScriptedLLM() as llm:
        script(llm, row["order_id"], tools, text)
        result = place(llm, tmp_path, world, world.task(row))
    assert result.agent.summary == f"Order {row['order_id']} {expected}"
    assert text not in result.agent.summary
