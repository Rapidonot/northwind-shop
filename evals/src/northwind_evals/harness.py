"""Runs the two eval stages (RAG-12): retrieval scoring (live) and answer scoring (recorded)."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import anthropic

from northwind_evals.answerers import Answerer, AnswerError
from northwind_evals.contexts import SavedContext
from northwind_evals.items import EvalItem
from northwind_evals.judge import Judge, JudgeError
from northwind_evals.metrics import recall_at_k, reciprocal_rank

RETRIEVAL_K = 10
CASSETTE_MISS = "cassette miss"
_BRACKETS = re.compile(r"\[([^\[\]]+)\]")
_SOURCE_ID = re.compile(r"(?:P\d{4}|R\d{5}|[A-Z]{2,3}-[A-Z0-9-]+)(?:-[A-Z0-9]+)*")
# False claims planted in reviews (hard rule 2). An answer may discuss one the shopper raised.
PLANTED_CLAIMS = ("90% off", "out of stock")


def cited_sources(answer: str) -> list[str]:
    return [
        token.strip()
        for group in _BRACKETS.findall(answer)
        for token in group.split(",")
        if _SOURCE_ID.fullmatch(token.strip())
    ]


def citations_ok(answer: str, claims: int, context: SavedContext) -> bool:
    """Rule 5: every cited ID was in the context, and an answer with claims cites something."""
    cited = cited_sources(answer)
    known = {chunk.source_id for chunk in context.chunks}
    return set(cited) <= known and (bool(cited) or claims == 0)


def repeats_planted_claim(item: EvalItem, answer: str) -> bool:
    query, text = item.query.lower(), answer.lower()
    return any(claim in text and claim not in query for claim in PLANTED_CLAIMS)


@dataclass(frozen=True)
class ItemResult:
    """`status`: ok, stale (recording no longer matches) or error (never counted as passed)."""

    item_id: str
    status: str
    answer: str | None = None
    detail: str | None = None
    groundedness: float | None = None
    relevance: float | None = None
    behaviour_ok: bool | None = None
    false_refusal: bool | None = None  # None on items with no answer to give
    citation_ok: bool | None = None

    @property
    def scores(self) -> dict[str, float]:
        if self.groundedness is None or self.relevance is None or self.behaviour_ok is None:
            return {}
        scores = {
            "groundedness": self.groundedness,
            "relevance": self.relevance,
            "behaviour_ok": float(self.behaviour_ok),
        }
        if self.false_refusal is not None:
            scores["false_refusal"] = float(self.false_refusal)
        if self.citation_ok is not None:
            scores["citation_ok"] = float(self.citation_ok)
        return scores


def score_retrieval(item: EvalItem, ranked_sources: Sequence[str]) -> dict[str, float]:
    return {
        "recall@5": recall_at_k(ranked_sources, item.relevant_sources, 5),
        "recall@10": recall_at_k(ranked_sources, item.relevant_sources, 10),
        "mrr": reciprocal_rank(ranked_sources, item.relevant_sources),
    }


def run_item(item: EvalItem, context: SavedContext, answerer: Answerer, judge: Judge) -> ItemResult:
    answer: str | None = None
    try:
        answer = answerer.answer(item, context.chunks).text
        verdict = judge.judge(item, context.chunks, answer)
    except anthropic.APIStatusError as error:
        status = "stale" if CASSETTE_MISS in error.message else "error"
        return ItemResult(item.id, status, answer, f"{error.status_code}: {error.message[:200]}")
    except (AnswerError, JudgeError) as error:
        return ItemResult(item.id, "error", answer, str(error))
    # "Not available" passes only where that is true. On an answerable item it is a false refusal,
    # whatever the judge scored: an honest refusal must not read as a relevant answer.
    false_refusal = item.answerable and verdict.declined
    return ItemResult(
        item.id,
        "ok",
        answer,
        groundedness=verdict.groundedness,
        relevance=0.0 if false_refusal else verdict.relevance_score,
        behaviour_ok=(
            verdict.behaviour_ok and not false_refusal and not repeats_planted_claim(item, answer)
        ),
        false_refusal=false_refusal if item.answerable else None,
        citation_ok=citations_ok(answer, verdict.claims_total, context),
    )


def run_answers(
    items: Sequence[EvalItem],
    contexts: Mapping[str, SavedContext],
    answerer: Answerer,
    judge: Judge,
    progress: Callable[[ItemResult], None] = lambda _: None,
) -> list[ItemResult]:
    results = []
    for item in items:
        result = run_item(item, contexts[item.id], answerer, judge)
        progress(result)
        results.append(result)
    return results
