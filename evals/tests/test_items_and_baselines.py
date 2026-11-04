"""Item schema rejections, slicing, aggregation, and baseline and context round trips (RAG-12)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from northwind_evals.contexts import ContextChunk, SavedContext, load_contexts, save_contexts
from northwind_evals.gate import TOLERANCES, Baseline, compare, load_baseline, save_baseline
from northwind_evals.items import (
    SEGMENTS,
    EvalItem,
    ItemError,
    awaiting_review,
    human_reviewed,
    load_items,
    parse_item,
    slice_items,
)
from northwind_evals.metrics import aggregate, mean, recall_at_k

pytestmark = pytest.mark.req("RAG-12")

RECORD: dict[str, Any] = {
    "id": "EV-1",
    "segment": "specification",
    "query": "Is it waterproof?",
    "region": "US",
    "expected_behaviour": "answer",
    "relevant_sources": [["P1"]],
    "reference_facts": ["It is waterproof."],
    "why": "tests a spec",
    "trap_id": None,
    "reviewed_by": "model",
}


def make(**changes: Any) -> EvalItem:
    return parse_item({**RECORD, **changes})


# --- schema ----------------------------------------------------------------------------------


def test_a_valid_record_parses_with_any_of_groups_as_tuples() -> None:
    parsed = make(relevant_sources=[["POL-US", "FAQ-US"], ["P1"]])
    assert parsed.relevant_sources == (("POL-US", "FAQ-US"), ("P1",))
    assert parsed.all_sources == ("POL-US", "FAQ-US", "P1")


@pytest.mark.parametrize(
    "changes",
    [
        {"reviewed_by": "robot"},
        {"reviewed_by": "Human"},
        {"reviewed_by": None},
        {"segment": "chitchat"},
        {"region": "AU"},
        {"region": "us"},
        {"expected_behaviour": "refuse"},
        {"query": ""},
        {"query": "   "},
        {"query": 7},
        {"why": ""},
        {"id": ""},
        {"reference_facts": "one string"},
        {"reference_facts": [""]},
        {"reference_facts": [1]},
        {"relevant_sources": ["P1"]},  # flat list: the old, pre-any-of shape
        {"relevant_sources": [[]]},
        {"relevant_sources": [[""]]},
        {"relevant_sources": [["P1", 2]]},
        {"relevant_sources": "P1"},
        {"relevant_sources": []},  # an `answer` item needs something to retrieve
    ],
    ids=lambda c: json.dumps(c)[:40],
)
def test_malformed_items_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ItemError):
        make(**changes)


@pytest.mark.parametrize("missing", sorted(RECORD))
def test_every_field_is_required(missing: str) -> None:
    record = {k: v for k, v in RECORD.items() if k != missing}
    with pytest.raises(ItemError, match="missing"):
        parse_item(record)


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ItemError, match="unknown"):
        parse_item({**RECORD, "difficulty": "hard"})


def test_non_answer_items_may_have_no_sources() -> None:
    assert make(segment="unanswerable", expected_behaviour="abstain", relevant_sources=[])
    assert not make(
        segment="unanswerable", expected_behaviour="abstain", relevant_sources=[]
    ).retrieval_scored


@pytest.mark.parametrize("behaviour", ["abstain", "clarify", "contain"])
def test_only_answer_items_count_toward_retrieval(behaviour: str) -> None:
    assert not make(expected_behaviour=behaviour).retrieval_scored
    assert make().retrieval_scored


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "items.jsonl"
    path.write_text(json.dumps(RECORD) + "\n" + json.dumps(RECORD) + "\n", encoding="utf-8")
    with pytest.raises(ItemError, match="duplicate"):
        load_items(path)


def test_blank_lines_are_ignored_and_an_empty_file_is_an_empty_set(tmp_path: Path) -> None:
    path = tmp_path / "items.jsonl"
    path.write_text("\n" + json.dumps(RECORD) + "\n\n", encoding="utf-8")
    assert [i.id for i in load_items(path)] == ["EV-1"]
    path.write_text("", encoding="utf-8")
    assert load_items(path) == []


def test_a_bad_json_line_is_an_error_not_skipped(tmp_path: Path) -> None:
    path = tmp_path / "items.jsonl"
    path.write_text(json.dumps(RECORD) + "\n{not json\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_items(path)


# --- review rule -----------------------------------------------------------------------------


def test_awaiting_review_is_unanswerable_and_adversarial_not_yet_human() -> None:
    items = [
        make(id="a"),
        make(id="b", segment="unanswerable", expected_behaviour="abstain", relevant_sources=[]),
        make(
            id="c",
            segment="adversarial",
            expected_behaviour="contain",
            relevant_sources=[],
            reviewed_by="human",
        ),
        make(id="d", segment="adversarial", expected_behaviour="contain", relevant_sources=[]),
    ]
    assert [i.id for i in awaiting_review(items)] == ["b", "d"]
    assert human_reviewed(items) == 1
    assert human_reviewed([]) == 0
    assert awaiting_review([]) == []


# --- slicing ---------------------------------------------------------------------------------


def _items_across_segments(per_segment: int) -> list[EvalItem]:
    return [make(id=f"{seg}-{n}", segment=seg) for seg in SEGMENTS for n in range(per_segment)]


def test_slice_of_zero_is_empty_and_negative_does_not_hang() -> None:
    items = _items_across_segments(2)
    assert slice_items(items, 0) == []
    assert slice_items(items, -3) == []


def test_slice_larger_than_the_set_returns_everything_once() -> None:
    items = _items_across_segments(2)
    chosen = slice_items(items, 1000)
    assert sorted(i.id for i in chosen) == sorted(i.id for i in items)


def test_slice_of_an_empty_set_is_empty() -> None:
    assert slice_items([], 5) == []


def test_slice_of_one_takes_the_first_segment_and_never_repeats() -> None:
    items = _items_across_segments(3)
    [only] = slice_items(items, 1)
    assert only.segment == SEGMENTS[0]
    chosen = slice_items(items, 13)
    assert len({i.id for i in chosen}) == 13


def test_slice_is_round_robin_and_deterministic() -> None:
    items = _items_across_segments(3)
    chosen = slice_items(items, len(SEGMENTS) + 2)
    assert [i.segment for i in chosen[: len(SEGMENTS)]] == list(SEGMENTS)
    assert [i.id for i in chosen] == [i.id for i in slice_items(items, len(SEGMENTS) + 2)]
    assert chosen[len(SEGMENTS)].id == f"{SEGMENTS[0]}-1"


def test_slice_survives_segments_with_no_items() -> None:
    items = [make(id="x", segment="policy"), make(id="y", segment="policy")]
    assert [i.id for i in slice_items(items, 2)] == ["x", "y"]


# --- aggregation -----------------------------------------------------------------------------


def test_aggregate_gives_overall_and_per_segment_means_with_counts() -> None:
    scores = {
        "a": {"recall@5": 1.0, "mrr": 1.0},
        "b": {"recall@5": 0.0, "mrr": 0.5},
        "c": {"recall@5": 0.5, "mrr": 0.25},
    }
    result = aggregate(scores, {"a": "policy", "b": "policy", "c": "variant"})
    assert result["overall"]["recall@5"] == pytest.approx(0.5)
    assert result["overall"]["n"] == 3
    assert result["policy"]["recall@5"] == pytest.approx(0.5)
    assert result["policy"]["mrr"] == pytest.approx(0.75)
    assert result["policy"]["n"] == 2
    assert result["variant"] == {"mrr": 0.25, "recall@5": 0.5, "n": 1}


def test_aggregate_averages_a_metric_only_over_items_that_have_it() -> None:
    """false_refusal exists only on answerable items; the others must not count as zeros."""
    scores = {
        "a": {"relevance": 1.0, "false_refusal": 1.0},
        "b": {"relevance": 1.0},
        "c": {"relevance": 0.0, "false_refusal": 0.0},
    }
    result = aggregate(scores, {"a": "s", "b": "s", "c": "s"})
    assert result["overall"]["false_refusal"] == pytest.approx(0.5)
    assert result["overall"]["relevance"] == pytest.approx(2 / 3)
    assert result["overall"]["n"] == 3


def test_aggregate_of_nothing_is_overall_with_zero_and_no_data_is_none_not_zero() -> None:
    assert aggregate({}, {}) == {"overall": {"n": 0}}
    assert mean([]) is None
    result = aggregate({"u": {"relevance": 1.0}}, {"u": "unanswerable"})
    assert "false_refusal" not in result["unanswerable"]


def test_aggregate_with_an_item_missing_its_segment_is_an_error() -> None:
    with pytest.raises(KeyError):
        aggregate({"a": {"mrr": 1.0}}, {})


def test_recall_with_no_requirements_is_an_error_not_a_perfect_score() -> None:
    with pytest.raises(ValueError, match="at least one"):
        recall_at_k(["A"], [], 5)


# --- baselines and contexts round trips ------------------------------------------------------


def a_baseline() -> Baseline:
    return Baseline(
        name="dense-only",
        retriever_id="dense:v1:x",
        index_version="idx",
        answerer_id="single-shot:v1:m",
        judge_id="judge:v2:m",
        retrieval={"EV-1": {"recall@5": 1.0, "recall@10": 1.0, "mrr": 0.5}},
        answers={"EV-1": {"groundedness": 0.75, "relevance": 0.5, "false_refusal": 0.0}},
        summary={"overall": {"mrr": 0.5, "n": 1}},
    )


def test_a_baseline_round_trips_exactly(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "baseline.json"
    save_baseline(path, a_baseline())
    assert load_baseline(path) == a_baseline()


def test_saving_a_baseline_is_deterministic_and_newline_terminated(tmp_path: Path) -> None:
    one, two = tmp_path / "one.json", tmp_path / "two.json"
    save_baseline(one, a_baseline())
    save_baseline(two, a_baseline())
    assert one.read_bytes() == two.read_bytes()
    assert one.read_text().endswith("}\n")
    keys = list(json.loads(one.read_text()))
    assert keys == sorted(keys)


def test_a_baseline_with_an_unknown_field_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    save_baseline(path, a_baseline())
    data = json.loads(path.read_text())
    data["tolerance"] = 1.0  # a baseline must not be able to loosen the gate
    path.write_text(json.dumps(data))
    with pytest.raises(TypeError):
        load_baseline(path)


def test_the_committed_baseline_loads_and_gates_against_itself() -> None:
    repo = Path(__file__).resolve().parents[2]
    baseline = load_baseline(repo / "evals/baselines/dense-only.json")
    assert baseline.retrieval
    assert baseline.answers
    result = compare(baseline, baseline.retrieval, baseline.answers, [], [])
    assert result.passed()
    assert {row[0] for row in result.rows} >= {"recall@5", "mrr", "groundedness", "relevance"}


def test_a_context_round_trips_and_keeps_chunk_order(tmp_path: Path) -> None:
    chunks = tuple(ContextChunk(f"c{n}", f"P{n}", "product", f"text {n} é") for n in (3, 1, 2))
    saved = SavedContext("EV-1", "dense:v1:x", "idx", chunks)
    path = tmp_path / "deep" / "contexts.jsonl"
    save_contexts(path, [saved, SavedContext("EV-2", "dense:v1:x", "idx", ())])
    loaded = load_contexts(path)
    assert loaded["EV-1"] == saved
    assert [chunk.chunk_id for chunk in loaded["EV-1"].chunks] == ["c3", "c1", "c2"]
    assert loaded["EV-2"].chunks == ()


def test_every_tolerance_is_small_and_covers_each_gated_metric() -> None:
    assert set(TOLERANCES) == {
        "recall@5",
        "mrr",
        "groundedness",
        "relevance",
        "false_refusal",
        "behaviour_ok",
        "citation_ok",
    }
    assert all(0 < t <= 0.05 for t in TOLERANCES.values())


@pytest.mark.parametrize("metric", sorted(TOLERANCES))
def test_each_gated_metric_alone_can_fail_the_gate(metric: str) -> None:
    """RAG-12: a change that lowers any metric beyond tolerance fails the build."""
    base = Baseline(
        "b",
        "r",
        "i",
        "a",
        "j",
        retrieval={"E": {"recall@5": 0.96, "mrr": 0.96}},
        answers={
            "E": {
                "groundedness": 0.96,
                "relevance": 0.96,
                "false_refusal": 0.96,
                "behaviour_ok": 0.96,
                "citation_ok": 0.96,
            }
        },
        summary={},
    )
    retrieval = {"E": dict(base.retrieval["E"])}
    answers = {"E": dict(base.answers["E"])}
    worse = 0.96 + 0.1 if metric == "false_refusal" else 0.96 - 0.1
    (retrieval if metric in retrieval["E"] else answers)["E"][metric] = worse
    assert not compare(base, retrieval, answers, [], []).passed()
    # and an improvement is never a failure
    better = 0.96 - 0.1 if metric == "false_refusal" else 0.96 + 0.1
    (retrieval if metric in retrieval["E"] else answers)["E"][metric] = better
    assert compare(base, retrieval, answers, [], []).passed()


def test_a_gate_with_nothing_to_compare_fails_rather_than_passes() -> None:
    base = Baseline("b", "r", "i", "a", "j", {}, {}, {})
    result = compare(base, {}, {}, [], [])
    assert not result.passed()
    assert result.problems
