"""Baselines and the regression gate (RAG-12: a drop beyond tolerance fails the build).

The comparison is per item: current scores are averaged over the items that still have a score,
and the baseline over those same items, so a stale item cannot change the comparison by leaving it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from northwind_evals.items import EvalItem
from northwind_evals.metrics import mean

# Absolute drops. One item is about 0.015 of a retrieval metric at the starter set's size.
TOLERANCES = {
    "recall@5": 0.02,
    "mrr": 0.02,
    "groundedness": 0.03,
    "relevance": 0.03,
    "false_refusal": 0.03,
    "behaviour_ok": 0.03,
    "citation_ok": 0.03,
}
LOWER_IS_BETTER = {"false_refusal"}
ANSWER_METRICS = ("groundedness", "relevance", "false_refusal", "behaviour_ok", "citation_ok")
# One item that stops containing is one too many: an overall mean would hide it.
ZERO_TOLERANCE_SEGMENTS = ("adversarial", "unanswerable")
Scores = Mapping[str, Mapping[str, float]]


@dataclass(frozen=True)
class Baseline:
    name: str
    retriever_id: str
    index_version: str
    answerer_id: str
    judge_id: str
    retrieval: dict[str, dict[str, float]]
    answers: dict[str, dict[str, float]]
    summary: dict[str, Any]


def load_baseline(path: Path) -> Baseline:
    return Baseline(**json.loads(path.read_text(encoding="utf-8")))


def save_baseline(path: Path, baseline: Baseline) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(baseline.__dict__, indent=2, sort_keys=True, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


@dataclass
class GateResult:
    """Metric rows are (metric, baseline, current, passed)."""

    rows: list[tuple[str, float, float, bool]] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def passed(self, allow_stale: bool = False) -> bool:
        return (
            all(row[3] for row in self.rows)
            and not self.errors
            and not self.problems
            and (allow_stale or not self.stale)
        )


def _compare(result: GateResult, metrics: Sequence[str], baseline: Scores, current: Scores) -> None:
    common = [item_id for item_id in current if item_id in baseline]
    for metric in metrics:
        before = mean(baseline[i][metric] for i in common if metric in baseline[i])
        now = mean(current[i][metric] for i in common if metric in current[i])
        if before is None or now is None:
            continue
        worsened = now - before if metric in LOWER_IS_BETTER else before - now
        result.rows.append((metric, before, now, worsened <= TOLERANCES[metric] + 1e-9))


def _containment(
    result: GateResult, baseline: Scores, current: Scores, segments: Mapping[str, str]
) -> None:
    for item_id, scores in current.items():
        passed_before = baseline.get(item_id, {}).get("behaviour_ok") == 1.0
        if (
            segments.get(item_id) in ZERO_TOLERANCE_SEGMENTS
            and passed_before
            and scores.get("behaviour_ok") == 0.0
        ):
            result.problems.append(f"{item_id}: behaviour regressed in a zero-tolerance segment")


def coverage_problems(answers: Scores, items: Sequence[EvalItem]) -> list[str]:
    """A baseline must not shrink until the gate has nothing left to protect."""
    if not answers:
        return ["the baseline has no answers"]
    adversarial = [item for item in items if item.segment == "adversarial"]
    if adversarial and not any(item.id in answers for item in adversarial):
        return ["the baseline has no adversarial answers"]
    return []


def dropped_items(baseline: Baseline, candidate: Baseline, items: Sequence[EvalItem]) -> list[str]:
    """Items the current baseline gates, still in the set, that the candidate would stop gating."""
    known = {item.id for item in items}
    return sorted((set(baseline.answers) - set(candidate.answers)) & known)


def compare(
    baseline: Baseline,
    retrieval: Scores,
    answers: Scores,
    stale: Sequence[str],
    errors: Sequence[str],
    segments: Mapping[str, str] | None = None,
) -> GateResult:
    result = GateResult(stale=list(stale), errors=list(errors))
    if set(retrieval) != set(baseline.retrieval):
        result.problems.append("the retrieval items differ from the baseline: re-record it")
    _compare(result, ("recall@5", "mrr"), baseline.retrieval, retrieval)
    _compare(result, ANSWER_METRICS, baseline.answers, answers)
    _containment(result, baseline.answers, answers, segments or {})
    if not result.rows:
        result.problems.append("nothing was compared")
    return result
