"""RAG-07's 60 routing labels are frozen before any router exists (row #31).

The labels and the guide (which holds the scoring rule) were committed before the router, and the
user reviewed 20 of them. Any change to either file after this pin is a label or rule change, which
needs the user (plan `docs/reviews/2026-11-25-RAG-10-plan.md`, stops): update a pin only with their
answer.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("RAG-07")

REPO = Path(__file__).resolve().parents[2]
LABELS = REPO / "evals/fixtures/rag07_queries.jsonl"
GUIDE = REPO / "docs/reviews/2026-11-25-RAG-10-labelling-guide.md"

PINS = {
    LABELS: "ba0c4f5ffa74aaefb4fa6978424c4a9f51788d840f86b985f8b8a906627eecd6",
    GUIDE: "645fe72b545f1406b70a2469d986fa3a69d09ee9fbea8de07def5066bb1e7ce5",
}
INDEXES = {"policy", "reviews", "specifications"}
CATEGORIES = {"specifications": 16, "reviews": 14, "policy": 14, "multi": 12, "all": 4}

# The 20 items on the review sheet (`docs/reviews/2026-11-25-RAG-10-labels-review.md`), all accepted
# by the user as drafted, 2026-11-25.
HUMAN_REVIEWED = frozenset(
    f"RT7-{n}"
    for n in (
        "003 006 009 011 012 020 022 027 028 035 037 038 039 043 047 049 052 053 054 060"
    ).split()
)


@pytest.mark.parametrize("path", list(PINS), ids=lambda p: p.name)
def test_labels_and_guide_are_pinned(path: Path) -> None:
    found = hashlib.sha256(path.read_bytes()).hexdigest()
    assert found == PINS[path], "label or scoring rule change: needs the user (row #31 plan)"


def test_sixty_items_and_the_users_marks() -> None:
    items = [json.loads(line) for line in LABELS.read_text(encoding="utf-8").splitlines()]
    assert [i["id"] for i in items] == [f"RT7-{n:03d}" for n in range(1, 61)]
    assert Counter(i["category"] for i in items) == CATEGORIES
    assert all(i["indexes"] and set(i["indexes"]) <= INDEXES for i in items)
    assert Counter(i["reviewed_by"] for i in items) == {"human": 20, "model": 40}
    assert {i["id"] for i in items if i["reviewed_by"] == "human"} == HUMAN_REVIEWED
