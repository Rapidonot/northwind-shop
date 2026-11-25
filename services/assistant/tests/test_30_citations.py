"""Row #30, finding M1: one reading of a citation group for the evals, the output check, `turn.py`
and the portal. `apps/portal/src/citationCases.json` holds the shared cases; `citations.test.tsx`
runs the same file. Before this row only the evals split on ";", so `[P0001; P9999]` was a citation
to them and plain text to everything the shopper sees: the invented marker stayed on screen.
Failing first (docs/reviews/2026-11-25-30-plan.md)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.output_check import OutputCheck, check_text, citation_ids
from northwind_assistant.turn import cited_ids

pytestmark = [pytest.mark.req("RAG-08"), pytest.mark.req("SA-02")]

REPO = Path(__file__).resolve().parents[3]
CASES: list[dict[str, Any]] = json.loads(
    (REPO / "apps/portal/src/citationCases.json").read_text(encoding="utf-8")
)
SEEN = {"P0001", "P0011", "R00002", "FAQ-019", "POL-RETURNS-US", "SG-TOWELS", "P0156-CHR-BATHTOWEL"}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["group"])
def test_the_shared_cases(case: dict[str, Any]) -> None:
    assert citation_ids(case["group"]) == case["ids"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["group"])
def test_turn_cites_what_the_shared_cases_say(case: dict[str, Any]) -> None:
    assert list(cited_ids(f"Warm [{case['group']}].", SEEN)) == case["ids"]


def test_an_invented_id_after_a_semicolon_leaves_the_screen() -> None:
    """The user's M1 addition: `[P0001; P9999]` kept the invented marker on screen."""
    assert check_text("Warm [P0001; P9999].", (), SEEN) == "Warm [P0001]."


def test_a_group_of_only_invented_ids_is_removed_whole() -> None:
    assert check_text("Warm [P9998; P9999].", (), SEEN) == "Warm."


def test_a_group_of_real_ids_is_left_as_written() -> None:
    assert check_text("Warm [P0011; R00002].", (), SEEN) == "Warm [P0011; R00002]."


def test_an_invented_id_is_removed_however_the_deltas_fall() -> None:
    text = "Warm [P0001; P9999]. Dry [P0011]."
    for cut in range(1, len(text)):
        check = OutputCheck(set(SEEN), [])
        check.feed(text[:cut])
        check.feed(text[cut:])
        check.finish()
        assert check.shown == "Warm [P0001]. Dry [P0011].", cut


def test_text_in_brackets_that_is_not_a_citation_is_left_alone() -> None:
    text = "It is warm [size chart; see below]."
    assert check_text(text, (), SEEN) == text


def test_an_id_in_a_semicolon_group_is_not_read_as_a_duration() -> None:
    text = "Return it per policy [FAQ-019; POL-RETURNS-US]."
    assert check_text(text, (), SEEN) == text
