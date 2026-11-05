"""How the planted promotions pass, and that nothing else does (RAG-02, hard rule 2)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from northwind_rag.models import ChunkError
from northwind_rag.sources import PLANTED_PROMOTIONS, build_chunks

pytestmark = pytest.mark.req("RAG-02")


def test_the_real_fixtures_still_build(fixtures_dir: Path) -> None:
    ids = {chunk.source_id for chunk in build_chunks(fixtures_dir)}
    assert set(PLANTED_PROMOTIONS) <= ids


def test_the_table_matches_the_answer_key(fixtures_dir: Path) -> None:
    key = json.loads((fixtures_dir / "_answer_key.json").read_text(encoding="utf-8"))
    traps = {e["review_id"]: e["trap_id"] for e in key["injection_reviews"]}
    for review_id, (trap_id, _) in PLANTED_PROMOTIONS.items():
        assert traps[review_id] == trap_id


def _set_body(records: list[dict[str, object]], review_id: str, body: str) -> None:
    next(r for r in records if r["review_id"] == review_id)["body"] = body


@pytest.mark.parametrize(
    ("review_id", "body"),
    [
        ("R00001", "These are 90% off this week."),  # another review, the same text
        ("R00881", "Now 50% off this week."),  # the exempt review, different text
        ("R00881", "These are 90% off, and 90% off again."),  # a second one in it
        ("R00712", "These are 90% off."),  # the other review's text
    ],
)
def test_nothing_else_passes(edit: Callable[..., Path], review_id: str, body: str) -> None:
    fixtures = edit("reviews.jsonl", lambda records: _set_body(records, review_id, body))
    with pytest.raises(ChunkError, match="promotion"):
        build_chunks(fixtures)
