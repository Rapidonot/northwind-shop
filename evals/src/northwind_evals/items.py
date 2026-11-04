"""The labelled eval set (RAG-12): item schema, the human-review rule and slicing.

Labels point at `source_id`, not chunk IDs, so re-chunking (RAG-02, RAG-06) does not break them.
`relevant_sources` is a list of requirements, and each requirement is a list of alternatives: any
one source in it satisfies it. A comparison needs two requirements; a policy fact held by both the
policy and the FAQ is one requirement with two alternatives, both for the shopper's own region.
Claude writes `reviewed_by: model`; only the user sets `human` (CLAUDE.md, eval review rule).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SEGMENTS = (
    "specification",
    "comparison",
    "intent_filter",
    "variant",
    "policy",
    "ambiguous",
    "unanswerable",
    "adversarial",
)
BEHAVIOURS = ("answer", "abstain", "clarify", "contain")
REGIONS = ("US", "CA", "UK", "EU")
REVIEWERS = ("human", "model")
# Every item in these segments must be human-reviewed (PROGRESS.md row 4).
MUST_BE_HUMAN_REVIEWED = ("unanswerable", "adversarial")
MIN_HUMAN_REVIEWED = 25

_FIELDS = {
    "id",
    "segment",
    "query",
    "region",
    "expected_behaviour",
    "relevant_sources",
    "reference_facts",
    "why",
    "trap_id",
    "reviewed_by",
}


class ItemError(ValueError):
    """An eval item that does not match the schema."""


@dataclass(frozen=True)
class EvalItem:
    id: str
    segment: str
    query: str
    region: str
    expected_behaviour: str
    relevant_sources: tuple[tuple[str, ...], ...]
    reference_facts: tuple[str, ...]
    why: str
    trap_id: str | None
    reviewed_by: str

    @property
    def answerable(self) -> bool:
        return self.expected_behaviour == "answer"

    @property
    def retrieval_scored(self) -> bool:
        """Only items with an answer to find count toward recall and MRR."""
        return self.answerable

    @property
    def all_sources(self) -> tuple[str, ...]:
        return tuple(source for group in self.relevant_sources for source in group)


def _one_of(record: dict[str, Any], key: str, allowed: Sequence[str]) -> str:
    value = record[key]
    if value not in allowed:
        raise ItemError(f"{record['id']}: {key} must be one of {allowed}, not {value!r}")
    return str(value)


def _strings(record: dict[str, Any], key: str) -> tuple[str, ...]:
    value = record[key]
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ItemError(f"{record['id']}: {key} must be a list of non-empty strings")
    return tuple(value)


def _source_groups(record: dict[str, Any]) -> tuple[tuple[str, ...], ...]:
    value = record["relevant_sources"]
    if not isinstance(value, list) or not all(
        isinstance(group, list) and group and all(isinstance(s, str) and s for s in group)
        for group in value
    ):
        raise ItemError(f"{record['id']}: relevant_sources must be a list of non-empty lists")
    return tuple(tuple(group) for group in value)


def parse_item(record: dict[str, Any]) -> EvalItem:
    missing, extra = _FIELDS - record.keys(), record.keys() - _FIELDS
    if missing or extra:
        name = record.get("id", "<no id>")
        raise ItemError(f"{name}: missing {sorted(missing)}, unknown {sorted(extra)}")
    for key in ("id", "query", "why"):
        if not isinstance(record[key], str) or not record[key].strip():
            raise ItemError(f"{record['id']}: {key} must be a non-empty string")
    item = EvalItem(
        id=record["id"],
        segment=_one_of(record, "segment", SEGMENTS),
        query=record["query"],
        region=_one_of(record, "region", REGIONS),
        expected_behaviour=_one_of(record, "expected_behaviour", BEHAVIOURS),
        relevant_sources=_source_groups(record),
        reference_facts=_strings(record, "reference_facts"),
        why=record["why"],
        trap_id=record["trap_id"],
        reviewed_by=_one_of(record, "reviewed_by", REVIEWERS),
    )
    if item.retrieval_scored and not item.relevant_sources:
        raise ItemError(f"{item.id}: an `answer` item needs relevant_sources")
    return item


def load_items(path: Path) -> list[EvalItem]:
    lines = path.read_text(encoding="utf-8").splitlines()
    items = [parse_item(json.loads(line)) for line in lines if line.strip()]
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise ItemError("duplicate item IDs")
    return items


def awaiting_review(items: Iterable[EvalItem]) -> list[EvalItem]:
    """Items that must be human-reviewed and are not yet."""
    return [
        item
        for item in items
        if item.segment in MUST_BE_HUMAN_REVIEWED and item.reviewed_by != "human"
    ]


def human_reviewed(items: Iterable[EvalItem]) -> int:
    return sum(item.reviewed_by == "human" for item in items)


def slice_items(items: Sequence[EvalItem], size: int) -> list[EvalItem]:
    """`size` items taken in turn from each segment, so a small slice still covers them all."""
    queues = {segment: [i for i in items if i.segment == segment] for segment in SEGMENTS}
    chosen: list[EvalItem] = []
    while len(chosen) < min(size, len(items)):
        for segment in SEGMENTS:
            if queues[segment] and len(chosen) < size:
                chosen.append(queues[segment].pop(0))
    return chosen
