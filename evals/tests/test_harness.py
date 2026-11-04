"""Metrics, the gate and the scripted answer path (RAG-12). No API, no database."""

from __future__ import annotations

import json

import pytest

from northwind_common.llm import anthropic_client
from northwind_evals.answerers import SingleShotAnswerer
from northwind_evals.contexts import ContextChunk, SavedContext, is_stale
from northwind_evals.gate import Baseline, compare
from northwind_evals.harness import run_item
from northwind_evals.items import EvalItem
from northwind_evals.judge import Judge, JudgeError, parse_verdict
from northwind_evals.metrics import recall_at_k, reciprocal_rank
from northwind_llm_replay.pricing import cost_usd
from northwind_llm_replay.scan import scan_text
from northwind_llm_replay.scripted import ScriptedLLM

pytestmark = pytest.mark.req("RAG-12")

ITEM = EvalItem("EV-1", "specification", "q?", "US", "answer", ("P1",), ("f",), "w", None, "model")
CHUNK = ContextChunk("product:P1:0", "P1", "product", "text")
CONTEXT = SavedContext("EV-1", "dense:v1:x", "idx", (CHUNK,))
VERDICT = {
    "claims": [{"claim": "a", "supported": True}, {"claim": "b", "supported": False}],
    "relevance": "partial",
    "behaviour_ok": True,
}


def baseline(recall: float = 0.8, groundedness: float = 0.9) -> Baseline:
    return Baseline(
        "dense-only",
        "r",
        "i",
        "a",
        "j",
        retrieval={"EV-1": {"recall@5": recall, "mrr": 0.5}},
        answers={"EV-1": {"groundedness": groundedness, "relevance": 1.0}},
        summary={},
    )


def test_recall_counts_sources_once_and_respects_k() -> None:
    assert recall_at_k(["A", "A", "B"], ["A", "C"], 5) == 0.5
    assert recall_at_k(["X", "A"], ["A"], 1) == 0.0


def test_reciprocal_rank() -> None:
    assert reciprocal_rank(["X", "A"], ["A"]) == 0.5
    assert reciprocal_rank(["X"], ["A"]) == 0.0


def test_verdict_scores() -> None:
    verdict = parse_verdict(json.dumps(VERDICT))
    assert verdict.groundedness == 0.5
    assert verdict.relevance_score == 0.5


@pytest.mark.parametrize("text", ["not json", "{}", '{"claims": 1, "relevance": "x"}'])
def test_a_malformed_verdict_is_an_error_not_a_score(text: str) -> None:
    with pytest.raises(JudgeError):
        parse_verdict(text)


def test_stale_means_live_retrieval_differs_from_the_saved_context() -> None:
    chunks = tuple(ContextChunk(f"c{n}", "P1", "product", "text") for n in range(5))
    saved = SavedContext("EV-1", "dense:v1:x", "idx", chunks)
    ids = [chunk.chunk_id for chunk in chunks]
    assert not is_stale(saved, [*ids, "beyond-the-answer-context"])
    assert is_stale(saved, list(reversed(ids)))
    assert is_stale(saved, ids[:4])
    assert is_stale(None, ["product:P1:0"])


def test_a_drop_beyond_tolerance_fails_and_one_within_passes() -> None:
    answers = {"EV-1": {"groundedness": 0.9, "relevance": 1.0}}
    within = {"EV-1": {"recall@5": 0.79, "mrr": 0.5}}
    beyond = {"EV-1": {"recall@5": 0.70, "mrr": 0.5}}
    assert compare(baseline(), within, answers, [], []).passed()
    assert not compare(baseline(), beyond, answers, [], []).passed()


def test_an_answer_metric_drop_fails() -> None:
    retrieval = {"EV-1": {"recall@5": 0.8, "mrr": 0.5}}
    answers = {"EV-1": {"groundedness": 0.5, "relevance": 1.0}}
    assert not compare(baseline(), retrieval, answers, [], []).passed()


def test_stale_fails_unless_allowed_and_errors_always_fail() -> None:
    retrieval = {"EV-1": {"recall@5": 0.8, "mrr": 0.5}}
    result = compare(baseline(), retrieval, {}, ["EV-1"], [])
    assert not result.passed()
    assert result.passed(allow_stale=True)
    assert not compare(baseline(), retrieval, {}, [], ["EV-1: boom"]).passed(allow_stale=True)


def test_changed_items_fail_the_gate() -> None:
    assert not compare(baseline(), {"EV-2": {"recall@5": 1, "mrr": 1}}, {}, [], []).passed()


def test_answerer_and_judge_run_on_separate_clients_with_no_tools() -> None:
    with ScriptedLLM() as llm:
        llm.reply("Answer [P1].")
        llm.reply(json.dumps(VERDICT))
        answerer = SingleShotAnswerer(anthropic_client(llm.url), "claude-sonnet-5-5")
        judge = Judge(anthropic_client(llm.url), "claude-sonnet-5-5")
        result = run_item(ITEM, CONTEXT, answerer, judge)
        bodies = [request["body"] for request in llm.requests]
    assert result.status == "ok"
    assert result.groundedness == 0.5
    assert [body["max_tokens"] for body in bodies] == [1024, 512]
    assert all("tools" not in body for body in bodies)


def test_costs_are_fixed_precision() -> None:
    usage = {"input_tokens": 3333, "output_tokens": 777}
    assert len(repr(cost_usd("claude-sonnet-5-5", usage)).split(".")[1]) <= 9


def test_scanner_refuses_a_canary_and_never_prints_it() -> None:
    [finding] = scan_text("x NWCANARY-abc y", canaries=frozenset({"NWCANARY-abc"}))
    assert finding.kind == "internal-canary"
    assert "NWCANARY" not in str(finding)
