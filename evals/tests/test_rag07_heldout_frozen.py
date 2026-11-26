"""RAG-07's held-out set is frozen before it is scored (row #31, the user's decision 2026-11-26).

Drafted by a fresh subagent from the frozen guide alone, with the router frozen first; all
`reviewed_by: model`, no user marks. Scored once, no fix round, no label edits. Any change to the
file is a label change, which needs the user: update the pin only with their answer.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("RAG-07")

REPO = Path(__file__).resolve().parents[2]
HELDOUT = REPO / "evals/fixtures/rag07_heldout.jsonl"
HELDOUT_SHA256 = "9173691e47a31250a0d27544b335808f9ffb0ea060171ea1e80823e1a2962a2f"
CATEGORIES = {"specifications": 16, "reviews": 14, "policy": 14, "multi": 12, "all": 4}


def test_the_held_out_set_is_pinned() -> None:
    found = hashlib.sha256(HELDOUT.read_bytes()).hexdigest()
    assert found == HELDOUT_SHA256, "label change: needs the user (row #31 plan)"


def test_sixty_items_all_model() -> None:
    items = [json.loads(line) for line in HELDOUT.read_text(encoding="utf-8").splitlines()]
    assert [i["id"] for i in items] == [f"RH7-{n:03d}" for n in range(1, 61)]
    assert Counter(i["category"] for i in items) == CATEGORIES
    assert {i["reviewed_by"] for i in items} == {"model"}
