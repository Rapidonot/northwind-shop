"""SA-04's 20 prompts and SA-03's 50 conversations are frozen before any scorer runs (#30, #22).

The labels and the guide (which holds the scoring rules) were drafted before any implementation, and
the user reviewed 21 of them. Any change to a pinned file is a label or rule change, which needs the
user (plan `docs/reviews/2026-11-25-30-plan.md`, stop 5): update a pin only with their answer.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
PROMPTS = REPO / "evals/fixtures/sa04_prompts.jsonl"
CONVERSATIONS = REPO / "evals/fixtures/sa03_conversations.jsonl"
GUIDE = REPO / "docs/reviews/2026-11-25-30-labelling-guide.md"

PINS = {
    PROMPTS: "023f4178618fcd333f511518b8dfa0776c469d7d6dfa9e6df7edb9e64c03fba8",
    CONVERSATIONS: "a420b12db0ac74dc6cfb292ab53363eb7d8851f0f2303fbbb78df6d553f355cf",
    GUIDE: "b6077fcf0cfa06f51d166e47bbe3dc6d6043a65800bb046bc6449b503a82d635",
}
INTENTS = ("product_question", "comparison", "recommendation", "availability", "policy")

# The 20 items on the review sheet (`docs/reviews/2026-11-25-30-labels-review.md`) and IC-039, which
# the user marked with IC-040, 2026-11-25.
HUMAN_REVIEWED = frozenset(
    [f"CL-{n}" for n in "002 003 005 006 007 011 014 016 019 020".split()]
    + [f"IC-{n}" for n in "004 006 009 021 024 027 039 040 045 046 047".split()]
)


def _items(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.req("SA-03")
@pytest.mark.req("SA-04")
@pytest.mark.parametrize("path", list(PINS), ids=lambda p: p.name)
def test_labels_and_guide_are_pinned(path: Path) -> None:
    found = hashlib.sha256(path.read_bytes()).hexdigest()
    assert found == PINS[path], "label or scoring rule change: needs the user (plan, stop 5)"


@pytest.mark.req("SA-04")
def test_twenty_ambiguous_prompts() -> None:
    assert [i["id"] for i in _items(PROMPTS)] == [f"CL-{n:03d}" for n in range(1, 21)]


@pytest.mark.req("SA-03")
def test_ten_conversations_per_intent() -> None:
    items = _items(CONVERSATIONS)
    assert [i["id"] for i in items] == [f"IC-{n:03d}" for n in range(1, 51)]
    assert Counter(i["intent"] for i in items) == dict.fromkeys(INTENTS, 10)


@pytest.mark.req("SA-03")
@pytest.mark.req("SA-04")
def test_the_users_marks() -> None:
    items = _items(PROMPTS) + _items(CONVERSATIONS)
    assert Counter(i["reviewed_by"] for i in items) == {"human": 21, "model": 49}
    assert {i["id"] for i in items if i["reviewed_by"] == "human"} == HUMAN_REVIEWED
