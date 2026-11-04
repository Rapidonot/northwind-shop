"""Schema rules for the starter set (RAG-12). `test_review_rule` stays red until the user has
reviewed the items: only the user sets `reviewed_by: human`, so this is the row's real gate."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from northwind_data import textscan
from northwind_evals.contexts import load_contexts
from northwind_evals.gate import load_known_failures
from northwind_evals.items import (
    MIN_HUMAN_REVIEWED,
    SEGMENTS,
    awaiting_review,
    human_reviewed,
    load_items,
    slice_items,
)

REPO = Path(__file__).resolve().parents[2]
ITEMS = load_items(REPO / "evals/fixtures/starter_set.jsonl")
FIXTURES = REPO / "data/fixtures"


def _ids(name: str, key: str) -> set[str]:
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    return {json.loads(line)[key] for line in lines}


def _traps(node: object) -> set[str]:
    if isinstance(node, dict):
        found = {node["trap_id"]} if "trap_id" in node else set()
        return found.union(*(_traps(v) for v in node.values()))
    if isinstance(node, list):
        return set().union(*(_traps(v) for v in node)) if node else set()
    return set()


@pytest.mark.req("RAG-12")
def test_every_known_failure_names_an_open_progress_row() -> None:
    known = load_known_failures(REPO / "evals/fixtures/known_failures.json")
    rows = (REPO / "PROGRESS.md").read_text(encoding="utf-8").splitlines()
    for entry in known:
        number = entry.fix_item.lstrip("#")
        [row] = [line for line in rows if line.startswith(f"| {number} |")]
        assert "| done |" not in row, entry.item_id


@pytest.mark.req("RAG-12")
def test_about_100_items_cover_every_segment() -> None:
    counts = Counter(item.segment for item in ITEMS)
    assert 90 <= len(ITEMS) <= 110
    assert set(counts) == set(SEGMENTS)


@pytest.mark.req("RAG-12")
def test_review_rule() -> None:
    waiting = [item.id for item in awaiting_review(ITEMS)]
    assert human_reviewed(ITEMS) >= MIN_HUMAN_REVIEWED
    assert not waiting, f"{len(waiting)} items wait for human review: make eval-review-list"


@pytest.mark.req("RAG-12")
def test_labels_point_at_real_public_sources() -> None:
    known = (
        _ids("products.jsonl", "product_id")
        | _ids("variants.jsonl", "variant_id")
        | _ids("reviews.jsonl", "review_id")
        | _ids("documents.jsonl", "doc_id")
    )
    key = json.loads((FIXTURES / "_answer_key.json").read_text(encoding="utf-8"))
    internal = {entry.get("doc_id") or entry.get("product_id") for entry in key["internal_only"]}
    for item in ITEMS:
        assert set(item.all_sources) <= known - internal, item.id


@pytest.mark.req("RAG-12")
def test_document_sources_match_the_shoppers_region() -> None:
    lines = (FIXTURES / "documents.jsonl").read_text(encoding="utf-8").splitlines()
    regions = {doc["doc_id"]: doc["region"] for doc in map(json.loads, lines)}
    for item in ITEMS:
        for source in item.all_sources:
            assert regions.get(source, "ALL") in ("ALL", item.region), (item.id, source)


@pytest.mark.req("RAG-12")
def test_trap_ids_exist_in_the_answer_key() -> None:
    key = json.loads((FIXTURES / "_answer_key.json").read_text(encoding="utf-8"))
    assert {item.trap_id for item in ITEMS if item.trap_id} <= _traps(key)


@pytest.mark.req("RAG-12")
def test_reference_facts_hold_no_exact_prices_or_stock() -> None:
    for item in ITEMS:
        for fact in item.reference_facts:
            assert not textscan.find_money_or_stock(fact), item.id


@pytest.mark.req("RAG-12")
def test_saved_contexts_cover_every_item_and_hold_nothing_internal() -> None:
    path = REPO / "evals/contexts/dense-only.jsonl"
    contexts = load_contexts(path)
    assert set(contexts) == {item.id for item in ITEMS}
    key = json.loads((FIXTURES / "_answer_key.json").read_text(encoding="utf-8"))
    text = path.read_text(encoding="utf-8")
    assert not [e["canary"] for e in key["internal_only"] if e["canary"] in text]


@pytest.mark.req("RAG-12")
def test_a_slice_of_20_covers_every_segment_deterministically() -> None:
    chosen = slice_items(ITEMS, 20)
    assert len(chosen) == 20
    assert {item.segment for item in chosen} == set(SEGMENTS)
    assert chosen == slice_items(ITEMS, 20)
