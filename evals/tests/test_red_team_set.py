"""RAG-11 (row #9): the red-team corpus in `evals/adversarial/` has the shape the row's plan names.

These items are not gate items: they never go on `known_failures.json`, and a failure is a stop.
The checks on query text matter because the queries are model input in a spending window: none
may carry a canary, internal wording or card data to the provider.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from northwind_common.cards import contains_card_data
from northwind_rag.retrieval import SHOPPER_REGIONS
from northwind_rag.sources import build_chunks

pytestmark = pytest.mark.req("RAG-11")

REPO = Path(__file__).resolve().parents[2]
RED_TEAM = REPO / "evals" / "adversarial" / "rag11_red_team.jsonl"
STARTER_SET = REPO / "evals" / "fixtures" / "starter_set.jsonl"
ANSWER_KEY = REPO / "data" / "fixtures" / "_answer_key.json"
INTERNAL_FACTS = REPO / "evals" / "adversarial" / "rag11_internal_facts.json"
FIELDS = {"id", "query", "region", "attack", "targets", "expect", "reviewed_by", "why"}
OPTIONAL = {"starter_item"}
ATTACKS = {"direct_probe", "social_engineering", "escalation", "extraction", "cross_region"}
INTERNAL_TRAPS = [f"TRAP-INT-{n:03d}" for n in range(1, 8)]
REGION_TRAPS = {"TRAP-REG-001", "TRAP-REG-003", "TRAP-REG-004", "TRAP-REG-005"}
MAX_CHARS = 2000  # the gateway's message cap
RUN = 5
LONG_SENTENCE = 40


def items() -> list[dict[str, Any]]:
    return [json.loads(line) for line in RED_TEAM.read_text(encoding="utf-8").splitlines()]


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def runs(text: str, size: int = RUN) -> set[tuple[str, ...]]:
    w = words(text)
    return {tuple(w[i : i + size]) for i in range(len(w) - size + 1)}


def internal_texts() -> list[str]:
    return [
        c.text
        for c in build_chunks(REPO / "data" / "fixtures")
        if c.metadata.visibility != "public"
    ]


def public_runs() -> set[tuple[str, ...]]:
    found: set[tuple[str, ...]] = set()
    for c in build_chunks(REPO / "data" / "fixtures"):
        if c.metadata.visibility == "public":
            found |= runs(c.text)
    return found


def canaries() -> list[str]:
    key = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    return [str(entry["canary"]) for entry in key["internal_only"]]


def test_the_ids_are_exactly_rt_001_to_rt_028_each_once() -> None:
    ids = [item["id"] for item in items()]
    assert ids == [f"RT-{n:03d}" for n in range(1, 29)]


def test_every_item_has_the_planned_fields_and_values() -> None:
    for item in items():
        assert FIELDS <= set(item) <= FIELDS | OPTIONAL, item["id"]
        assert item["expect"] == "contain"
        assert item["reviewed_by"] in {"model", "human"}
        assert item["attack"] in ATTACKS, item["id"]
        assert item["targets"] and all(isinstance(t, str) for t in item["targets"])
        assert isinstance(item["why"], str) and item["why"].strip()


def test_every_region_is_one_the_server_accepts_and_each_has_an_item() -> None:
    regions = Counter(item["region"] for item in items())
    assert set(regions) == set(SHOPPER_REGIONS)


def test_every_internal_trap_has_at_least_two_items() -> None:
    counts = Counter(t for item in items() for t in item["targets"])
    for trap in INTERNAL_TRAPS:
        assert counts[trap] >= 2, trap


def test_the_cross_region_and_extraction_items_are_there() -> None:
    by_attack = Counter(item["attack"] for item in items())
    assert by_attack["cross_region"] == 7
    assert by_attack["extraction"] == 2
    assert by_attack["escalation"] == 6
    region_targets = {t for item in items() for t in item["targets"] if t.startswith("TRAP-REG")}
    assert region_targets == REGION_TRAPS
    for item in items():
        if item["attack"] == "cross_region":
            assert any(t.startswith("TRAP-REG") for t in item["targets"]), item["id"]


def test_a_starter_item_reference_repeats_its_query_exactly() -> None:
    starter = {
        row["id"]: row
        for row in (json.loads(x) for x in STARTER_SET.read_text("utf-8").splitlines())
    }
    referenced = [item for item in items() if "starter_item" in item]
    assert len(referenced) == 4
    for item in referenced:
        row = starter[item["starter_item"]]
        assert row["query"] == item["query"]
        assert row["region"] == item["region"]


def test_no_query_holds_a_canary_or_card_data_or_is_too_long() -> None:
    marks = canaries()
    assert len(marks) == 7
    for item in items():
        query = item["query"]
        assert not any(mark in query for mark in marks), item["id"]
        assert "NWCANARY" not in query.upper()
        assert not contains_card_data(query), item["id"]
        assert 0 < len(query) <= MAX_CHARS


def test_no_query_quotes_internal_wording() -> None:
    """No 5-word run of internal text that is not also public, and no internal sentence over 40
    characters: the queries go to the provider in a window (decision 0005)."""
    internal = internal_texts()
    assert internal
    internal_runs = set().union(*(runs(text) for text in internal)) - public_runs()
    sentences = {
        s.strip().lower()
        for text in internal
        for s in re.split(r"[.!?\n]", text)
        if len(s.strip()) > LONG_SENTENCE
    }
    assert sentences
    for item in items():
        query = item["query"]
        assert not runs(query) & internal_runs, (item["id"], sorted(runs(query) & internal_runs))
        assert not any(s in query.lower() for s in sentences), item["id"]


def test_no_query_uses_a_word_only_internal_text_holds() -> None:
    """Security review pass 1, F1: reordered internal content passes the run and sentence checks, so
    no query may hold a word that only internal text holds, other than the words of an internal
    document's title ("names and topics", data-flow.md)."""
    chunks = build_chunks(REPO / "data" / "fixtures")
    internal = {w for c in chunks if c.metadata.visibility != "public" for w in words(c.text)}
    public = {w for c in chunks if c.metadata.visibility == "public" for w in words(c.text)}
    key = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    titles = {w for entry in key["internal_only"] for w in words(entry["title"])}
    content_only = internal - public - titles
    assert {"january", "february", "misses", "console"} <= content_only  # the check is not empty
    for item in items():
        used = set(words(item["query"])) & content_only
        assert not used, (item["id"], sorted(used))


def test_no_query_states_an_internal_fact() -> None:
    """Security review pass 2, P2-4 (d): numbers such as "30%" are public words, so the word check
    cannot see them. No query may match a pattern from the internal-facts list, which would also
    switch that pattern off in the recorded check (it skips what the query holds)."""
    facts = json.loads(INTERNAL_FACTS.read_text(encoding="utf-8"))["patterns"]
    assert facts
    for item in items():
        query = re.sub(r"\s+", " ", item["query"].lower())
        assert not [p for p in facts if re.search(p, query)], item["id"]


def test_the_file_is_canonical_json_lines() -> None:
    for line in RED_TEAM.read_text(encoding="utf-8").splitlines():
        assert line == json.dumps(json.loads(line), ensure_ascii=False, sort_keys=True)
