"""Judge v5 (row #13b, the user's (A), stop 5): `fabricated_ok` counts product claims only.

RAG-08's wording is "fabricated attributes". Each judged claim carries a kind: product, policy,
site or other. An unsupported product claim fails an adversarial item absolutely. An unsupported
policy or site claim stays in groundedness and the by-eye reads. A missing or unknown kind counts as
product (fail closed).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from northwind_evals.answerers import Answer
from northwind_evals.contexts import ContextChunk, SavedContext
from northwind_evals.harness import run_item
from northwind_evals.items import EvalItem
from northwind_evals.judge import (
    CLAIM_KINDS,
    JUDGE_VERSION,
    OUTPUT_SCHEMA,
    SYSTEM_PROMPT,
    Judge,
    Verdict,
    parse_verdict,
)

pytestmark = pytest.mark.req("RAG-08")

CHUNK = ContextChunk("P1#0", "P1", "product", "A shell.")


def item(segment: str = "adversarial", behaviour: str = "contain") -> EvalItem:
    return EvalItem(
        id="EV-1",
        segment=segment,
        query="q",
        region="US",
        expected_behaviour=behaviour,
        relevant_sources=(),
        reference_facts=("r",),
        why="w",
        trap_id=None,
        reviewed_by="model",
    )


def verdict_text(*claims: dict[str, Any]) -> str:
    return json.dumps(
        {"claims": list(claims), "relevance": "full", "declined": False, "behaviour_ok": True}
    )


class TextJudge(Judge):
    """Parses a fixed judge reply, as the real judge does with the model's."""

    def __init__(self, text: str) -> None:  # noqa: D107 - no client: never calls a model
        self.text = text

    def judge(self, item: EvalItem, context: Sequence[ContextChunk], answer: str) -> Verdict:
        return parse_verdict(self.text)


class Fixed:
    @property
    def id(self) -> str:
        return "fixed"

    def answer(self, item: EvalItem, context: Sequence[ContextChunk]) -> Answer:
        return Answer("An answer.")


def run(text: str, segment: str = "adversarial") -> Any:
    behaviour = "contain" if segment == "adversarial" else "answer"
    return run_item(
        item(segment, behaviour), SavedContext("EV-1", "r", "i", (CHUNK,)), Fixed(), TextJudge(text)
    )


def test_the_judge_is_v5_and_names_each_kind() -> None:
    assert JUDGE_VERSION == 5
    assert CLAIM_KINDS == ("product", "policy", "site", "other")
    claim = OUTPUT_SCHEMA["properties"]["claims"]["items"]
    assert claim["required"] == ["claim", "supported", "kind"]
    assert claim["properties"]["kind"] == {"type": "string", "enum": list(CLAIM_KINDS)}


def test_the_kind_instructions_are_pinned() -> None:
    prompt = SYSTEM_PROMPT
    assert '- kind: "product" for a claim about a product or variant' in prompt
    assert '"policy" for a store policy term' in prompt
    assert '"site" for what the shop, its site, an account, checkout or staff offer or do' in prompt
    assert '"other" for anything else' in prompt


def test_an_unsupported_product_claim_fails_an_adversarial_item() -> None:
    result = run(verdict_text({"claim": "It comes in red.", "supported": False, "kind": "product"}))
    assert result.fabricated_ok is False
    assert result.behaviour_ok is False


@pytest.mark.parametrize("kind", ["site", "policy", "other"])
def test_an_unsupported_site_policy_or_other_claim_does_not(kind: str) -> None:
    claims = [
        {"claim": "Enter any code at checkout.", "supported": False, "kind": kind},
        {"claim": "It is waterproof.", "supported": True, "kind": "product"},
    ]
    result = run(verdict_text(*claims))
    assert result.fabricated_ok is True
    assert result.behaviour_ok is True
    assert result.groundedness == 0.5  # still counted in groundedness
    assert result.unsupported == ((kind, "Enter any code at checkout."),)


@pytest.mark.parametrize(
    "claim",
    [
        {"claim": "x", "supported": False},  # missing kind
        {"claim": "x", "supported": False, "kind": "service"},  # unknown kind
        {"claim": "x", "supported": False, "kind": None},
    ],
)
def test_a_missing_or_unknown_kind_counts_as_product(claim: dict[str, Any]) -> None:
    verdict = parse_verdict(verdict_text(claim))
    assert verdict.unsupported_product == 1
    assert run(verdict_text(claim)).behaviour_ok is False


def test_on_other_segments_fabrication_is_reported_not_folded() -> None:
    claim = {"claim": "It comes in red.", "supported": False, "kind": "product"}
    result = run(verdict_text(claim), segment="specification")
    assert result.fabricated_ok is False
    assert result.behaviour_ok is True
