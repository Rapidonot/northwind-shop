"""Citations scale with product and policy claims (row #13b, the user's (a), stop 5, 2026-11-13).

A site or other claim has no source to cite, so it no longer demands a citation; it still counts
against groundedness, and the #12 residual tracks it. A claim with a missing or unknown kind still
needs a citation (fail closed). `metrics.py` is unchanged, and main's aggregation of the new scores
equals this branch's.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from northwind_evals import metrics
from northwind_evals.answerers import Answer
from northwind_evals.contexts import ContextChunk, SavedContext
from northwind_evals.harness import run_item
from northwind_evals.items import EvalItem
from northwind_evals.judge import Judge, Verdict, parse_verdict

pytestmark = pytest.mark.req("RAG-08")

REPO = Path(__file__).resolve().parents[2]
CHUNK = ContextChunk("P1#0", "P1", "product", "A shell.")


def item(segment: str = "specification") -> EvalItem:
    behaviour = "contain" if segment == "adversarial" else "answer"
    return EvalItem(
        id="EV-1",
        segment=segment,
        query="q",
        region="US",
        expected_behaviour=behaviour,
        relevant_sources=(("P1",),),
        reference_facts=("r",),
        why="w",
        trap_id=None,
        reviewed_by="model",
    )


class TextJudge(Judge):
    def __init__(self, text: str) -> None:  # noqa: D107 - no client: never calls a model
        self.text = text

    def judge(self, item: EvalItem, context: Sequence[ContextChunk], answer: str) -> Verdict:
        return parse_verdict(self.text)


class Fixed:
    def __init__(self, text: str) -> None:
        self.text = text

    @property
    def id(self) -> str:
        return "fixed"

    def answer(self, item: EvalItem, context: Sequence[ContextChunk]) -> Answer:
        return Answer(self.text)


def claims(*pairs: tuple[str | None, bool]) -> str:
    listed: list[dict[str, Any]] = []
    for kind, supported in pairs:
        claim: dict[str, Any] = {"claim": "c", "supported": supported}
        if kind is not None:
            claim["kind"] = kind
        listed.append(claim)
    return json.dumps(
        {"claims": listed, "relevance": "full", "declined": False, "behaviour_ok": True}
    )


def run(text: str, answer: str = "No citation here.", segment: str = "specification") -> Any:
    context = SavedContext("EV-1", "r", "i", (CHUNK,))
    return run_item(item(segment), context, Fixed(answer), TextJudge(text))


def test_site_and_other_claims_need_no_citation() -> None:
    result = run(claims(("site", False), ("other", False), ("site", True)))
    assert result.citation_ok is True


@pytest.mark.parametrize("kind", ["product", "policy", None, "service"])
def test_product_policy_and_unknown_or_missing_kinds_need_citations(kind: str | None) -> None:
    result = run(claims((kind, True), (kind, True), (kind, True)))
    assert result.citation_ok is False  # three claims, no citation
    assert run(claims((kind, True)), answer="It is a shell [P1].").citation_ok is True


def test_the_requirement_counts_only_product_and_policy_claims() -> None:
    four = claims(("product", True), ("site", False), ("site", False), ("site", False))
    assert run(four, answer="It is a shell [P1].").citation_ok is True
    many = claims(*[("product", True)] * 4)  # ceil(4 / 3) = 2 citations needed
    assert run(many, answer="It is a shell [P1].").citation_ok is False


def test_groundedness_still_counts_site_and_other_claims() -> None:
    result = run(claims(("product", True), ("site", False), ("other", False), ("site", True)))
    assert result.groundedness == 0.5


def test_an_invented_source_still_fails_whatever_the_kinds() -> None:
    result = run(claims(("site", False)), answer="See [P9].")
    assert result.citation_ok is False


GIT_SHOW = ["git", "show", "origin/main:evals/src/northwind_evals/metrics.py"]


def _main_source() -> str:
    shown = subprocess.run(GIT_SHOW, capture_output=True, text=True, cwd=REPO)  # noqa: S603, S607
    if shown.returncode != 0:
        pytest.skip("origin/main is not available in this checkout")
    return shown.stdout


def _main_metrics() -> Any:
    spec = importlib.util.spec_from_loader("main_metrics", loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    exec(compile(_main_source(), "main:metrics.py", "exec"), module.__dict__)  # noqa: S102
    return module


def test_metrics_py_is_unchanged_from_main() -> None:
    assert (REPO / "evals/src/northwind_evals/metrics.py").read_text("utf-8") == _main_source()


def test_mains_aggregation_of_the_new_scores_equals_this_branchs() -> None:
    """Promotion recomputes the summary with main's copy (`summary_problems`)."""
    main = _main_metrics()
    answers = {
        "EV-1": {
            "citation_ok": 1.0,
            "groundedness": 0.5,
            "fabricated_ok": 1.0,
            "behaviour_ok": 1.0,
        },
        "EV-2": {
            "citation_ok": 0.0,
            "groundedness": 1.0,
            "fabricated_ok": 1.0,
            "behaviour_ok": 0.0,
        },
        "EV-3": {"citation_ok": 1.0, "fabricated_ok": 0.0, "behaviour_ok": 1.0, "relevance": 0.5},
    }
    segments = {"EV-1": "adversarial", "EV-2": "policy", "EV-3": "unanswerable"}
    assert main.aggregate(answers, segments) == metrics.aggregate(answers, segments)
    candidate = REPO / ".llm-proxy-log/baseline-candidate.json"
    if candidate.exists():
        record = json.loads(candidate.read_text("utf-8"))
        ours = metrics.aggregate(record["answers"], record["segments"])
        assert main.aggregate(record["answers"], record["segments"]) == ours
