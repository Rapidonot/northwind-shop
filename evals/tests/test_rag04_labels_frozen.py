"""RAG-04's 100 labelled queries are frozen before any extractor runs on them (row #11).

The labels and the labelling guide (which holds the scoring rule) were committed before the
extractor existed, and the user reviewed 25 of them. Any change to either file after this pin is a
label change, which needs the user (plan `docs/reviews/2026-11-24-11-plan.md`, stop 5): update the
pin only with their answer.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LABELS = REPO / "evals/fixtures/rag04_queries.jsonl"
GUIDE = REPO / "docs/reviews/2026-11-24-11-labelling-guide.md"

LABELS_SHA256 = "e3f278a8e228bab33a231027b2dd61f06364bb93b11a8bec3bab87f82df5b8a0"
GUIDE_SHA256 = "c26e0e11122dd057aad222d588a58c99933123ee98f3d14cc1842e155bc5114a"

# The 25 items on the review sheet (`docs/reviews/2026-11-24-11-labels-review.md`), all accepted
# by the user as drafted, 2026-11-24.
HUMAN_REVIEWED = frozenset(
    f"QU-{n}"
    for n in (
        "001 006 014 022 026 030 035 036 037 038 040 041 045 047 049 050 058 059 070 077 078 "
        "081 085 097 100"
    ).split()
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.req("RAG-04")
def test_labels_are_pinned() -> None:
    assert _sha256(LABELS) == LABELS_SHA256, "label change: needs the user (row #11 plan, stop 5)"


@pytest.mark.req("RAG-04")
def test_guide_is_pinned() -> None:
    assert _sha256(GUIDE) == GUIDE_SHA256, "scoring or label rule change: needs the user"


@pytest.mark.req("RAG-04")
def test_one_hundred_items_and_the_users_marks() -> None:
    items = [json.loads(line) for line in LABELS.read_text(encoding="utf-8").splitlines()]
    assert [i["id"] for i in items] == [f"QU-{n:03d}" for n in range(1, 101)]
    marks = Counter(i["reviewed_by"] for i in items)
    assert marks == {"human": 25, "model": 75}
    assert {i["id"] for i in items if i["reviewed_by"] == "human"} == HUMAN_REVIEWED
