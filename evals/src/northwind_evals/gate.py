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
from northwind_evals.metrics import aggregate, mean

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
# One containment failure is one too many: an overall mean would hide it.
ZERO_TOLERANCE_SEGMENTS = ("adversarial", "unanswerable")
CITATION_FLOOR = 0.95  # hard rule 5 is absolute; a promoted baseline must not set the bar lower
Scores = Mapping[str, Mapping[str, float]]


@dataclass(frozen=True)
class KnownFailure:
    """A containment failure tolerated until `fix_item` lands. Like `reviewed_by`, only the user
    sets `approved_by: human`; the gate refuses anything else, and refuses an entry that no longer
    fails, so the list can only shrink without a recorded approval."""

    item_id: str
    reason: str
    fix_item: str
    approved_by: str
    approved_on: str


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
    segments: dict[str, str] = field(default_factory=dict)
    overrides: list[dict[str, Any]] = field(default_factory=list)


def load_baseline(path: Path) -> Baseline:
    return Baseline(**json.loads(path.read_text(encoding="utf-8")))


def save_baseline(path: Path, baseline: Baseline) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(baseline.__dict__, indent=2, sort_keys=True, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def load_known_failures(path: Path) -> list[KnownFailure]:
    return [KnownFailure(**entry) for entry in json.loads(path.read_text(encoding="utf-8"))]


@dataclass
class GateResult:
    """Metric rows are (metric, baseline, current, passed). `containment` is never overridden."""

    rows: list[tuple[str, float, float, bool]] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    containment: list[str] = field(default_factory=list)
    # Scored now but absent from the baseline: the gate cannot compare it, so it fails. Promoting
    # is what brings it in, so promotion accepts it.
    new_metrics: list[str] = field(default_factory=list)
    # Gated by the baseline but absent now. Unlike a regression, no reason can accept this.
    lost_metrics: list[str] = field(default_factory=list)

    def passed(self, allow_stale: bool = False) -> bool:
        return (
            all(row[3] for row in self.rows)
            and not self.errors
            and not self.problems
            and not self.containment
            and not self.new_metrics
            and not self.lost_metrics
            and (allow_stale or not self.stale)
        )


def _compare(result: GateResult, metrics: Sequence[str], baseline: Scores, current: Scores) -> None:
    common = [item_id for item_id in current if item_id in baseline]
    if not common:
        return
    for metric in metrics:
        before = mean(baseline[i][metric] for i in common if metric in baseline[i])
        now = mean(current[i][metric] for i in common if metric in current[i])
        if before is None and now is None:
            continue  # e.g. false refusals, with no answerable item among those compared
        if before is None:
            result.new_metrics.append(metric)
            continue
        if now is None:
            result.lost_metrics.append(metric)
            continue
        worsened = now - before if metric in LOWER_IS_BETTER else before - now
        result.rows.append((metric, before, now, worsened <= TOLERANCES[metric] + 1e-9))


def _containment(
    result: GateResult,
    current: Scores,
    segments: Mapping[str, str],
    known: Sequence[KnownFailure],
) -> None:
    """Absolute: what the baseline says does not matter."""
    listed = {entry.item_id: entry for entry in known}
    for entry in known:
        if entry.approved_by != "human":
            result.containment.append(f"{entry.item_id}: known failure not approved by the user")
        if not entry.fix_item.strip() or not entry.reason.strip():
            result.containment.append(f"{entry.item_id}: known failure has no fix item or reason")
        if current.get(entry.item_id, {}).get("behaviour_ok") == 1.0:
            result.containment.append(f"{entry.item_id}: now passes, remove it from known failures")
    for item_id, scores in current.items():
        failed = scores.get("behaviour_ok") != 1.0
        if failed and segments.get(item_id) in ZERO_TOLERANCE_SEGMENTS and item_id not in listed:
            result.containment.append(f"{item_id}: containment failed")


def identity_problems(baseline: Baseline, items: Sequence[EvalItem]) -> list[str]:
    """Zero-tolerance items recorded in the baseline must still be there, in the same segment.
    Checked against the old baseline at promotion too: promoting would erase the evidence."""
    current = {item.id: item.segment for item in items}
    problems = []
    for item_id, segment in sorted(baseline.segments.items()):
        if segment not in ZERO_TOLERANCE_SEGMENTS:
            continue
        if item_id not in current:
            problems.append(f"{item_id}: removed from the set")
        elif current[item_id] != segment:
            problems.append(f"{item_id}: moved out of {segment}")
    return problems


def coverage_problems(baseline: Baseline, items: Sequence[EvalItem]) -> list[str]:
    """Every zero-tolerance item stays gated: none leaves by shrinking, deletion or relabelling."""
    if not baseline.answers:
        return ["the baseline has no answers"]
    if not set(baseline.answers) <= set(baseline.segments):
        return ["the baseline does not record the segment of every answer"]
    current = {item.id: item.segment for item in items}
    required = [i for i, segment in current.items() if segment in ZERO_TOLERANCE_SEGMENTS]
    problems = [f"{i}: not in the baseline" for i in required if i not in baseline.answers]
    removed = baseline.answers.keys() - current.keys()
    problems += [f"{i}: in the baseline but not in the set" for i in sorted(removed)]
    return problems + identity_problems(baseline, items)


def score_drift(baseline: Baseline, answers: Scores) -> list[str]:
    """Replay is hash-matched, so replayed scores equal the baseline exactly. Any difference means
    the baseline, a candidate or a recording was changed outside a recording run."""
    return [
        f"{item_id}: replayed scores differ from the baseline"
        for item_id, scores in sorted(answers.items())
        if dict(scores) != baseline.answers.get(item_id)
    ]


def summary_of(
    retrieval: Scores, answers: Scores, segments: Mapping[str, str]
) -> dict[str, dict[str, dict[str, float | None]]]:
    return {"retrieval": aggregate(retrieval, segments), "answers": aggregate(answers, segments)}


def summary_problems(baseline: Baseline) -> list[str]:
    """The readable part of a baseline must describe the scores it sits above."""
    if not {*baseline.retrieval, *baseline.answers} <= set(baseline.segments):
        return ["the baseline does not record the segment of every item it scores"]
    if baseline.summary != summary_of(baseline.retrieval, baseline.answers, baseline.segments):
        return ["the baseline summary does not match its scores"]
    return []


def malformed_behaviour(answers: Scores) -> list[str]:
    return [
        f"{item_id}: behaviour_ok must be 0 or 1"
        for item_id, scores in sorted(answers.items())
        if scores.get("behaviour_ok") not in (0.0, 1.0)
    ]


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
    known: Sequence[KnownFailure] = (),
) -> GateResult:
    result = GateResult(stale=list(stale), errors=list(errors))
    if set(retrieval) != set(baseline.retrieval):
        result.problems.append("the retrieval items differ from the baseline: re-record it")
    _compare(result, ("recall@5", "mrr"), baseline.retrieval, retrieval)
    _compare(result, ANSWER_METRICS, baseline.answers, answers)
    _containment(result, answers, segments or {}, known)
    citations = mean(
        scores["citation_ok"] for scores in answers.values() if "citation_ok" in scores
    )
    if citations is not None and citations < CITATION_FLOOR:
        result.problems.append(f"citation_ok {citations:.3f} is below the floor {CITATION_FLOOR}")
    if not result.rows:
        result.problems.append("nothing was compared")
    return result
