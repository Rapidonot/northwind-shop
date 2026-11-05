"""What the enriched index would change, against the plain one (RAG-02).

Read-only on both: it never writes a baseline, a context or a known failure. Answers replay against
`evals/contexts`, so an item's answer goes stale only if its top chunks change (`is_stale`).
Generated text is not in `text`, so that takes a change of IDs or order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from northwind_evals.contexts import ANSWER_CONTEXT_K, SavedContext, is_stale
from northwind_evals.gate import ZERO_TOLERANCE_SEGMENTS
from northwind_evals.harness import score_retrieval
from northwind_evals.items import EvalItem
from northwind_rag.retrieval import RetrievedChunk

Live = Mapping[str, Sequence[RetrievedChunk]]
METRIC = "recall@5"
METRICS = ("recall@5", "recall@10", "mrr")


@dataclass(frozen=True)
class TrapRow:
    trap_id: str
    chunk_id: str
    plain: tuple[str, ...]
    enriched: tuple[str, ...]

    @property
    def entered(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.enriched) - set(self.plain)))

    @property
    def left(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.plain) - set(self.enriched)))


@dataclass
class Report:
    plain_recall: float
    enriched_recall: float
    baseline_recall: float
    by_segment: dict[str, tuple[int, float, float]] = field(default_factory=dict)
    improved: list[str] = field(default_factory=list)
    worsened: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    stale_zero_tolerance: list[str] = field(default_factory=list)
    traps: list[TrapRow] = field(default_factory=list)
    rejections: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, tuple[float, float]] = field(default_factory=dict)
    segment_metrics: dict[str, dict[str, tuple[float, float]]] = field(default_factory=dict)

    @property
    def delta(self) -> float:
        return self.enriched_recall - self.plain_recall

    @property
    def planted_entries(self) -> list[tuple[str, str, str]]:
        """(trap, chunk, item) for every top 5 a planted chunk entered. By item, because comparing
        counts hides a chunk that left one item and entered another: R00033 did on EV-095."""
        return [(row.trap_id, row.chunk_id, item) for row in self.traps for item in row.entered]


def _all_scores(items: Sequence[EvalItem], live: Live) -> dict[str, dict[str, float]]:
    return {
        item.id: score_retrieval(item, [c.source_id for c in live[item.id]])
        for item in items
        if item.retrieval_scored
    }


def _pairs(
    ids: Sequence[str],
    before: Mapping[str, Mapping[str, float]],
    after: Mapping[str, Mapping[str, float]],
) -> dict[str, tuple[float, float]]:
    return {
        metric: (_mean([before[i][metric] for i in ids]), _mean([after[i][metric] for i in ids]))
        for metric in METRICS
    }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _appearances(chunk_id: str, live: Live) -> tuple[str, ...]:
    return tuple(
        sorted(
            item_id
            for item_id, chunks in live.items()
            if chunk_id in (c.chunk_id for c in chunks[:ANSWER_CONTEXT_K])
        )
    )


def build_report(
    items: Sequence[EvalItem],
    plain: Live,
    enriched: Live,
    contexts: Mapping[str, SavedContext],
    retriever: str,
    baseline: Mapping[str, Mapping[str, float]],
    trap_chunks: Mapping[str, str],
    rejections: Mapping[str, int] | None = None,
) -> Report:
    """`trap_chunks` maps each planted chunk's ID to its trap, for every TRAP-INJ chunk."""
    full_before = _all_scores(items, plain)
    full_after = _all_scores(items, enriched)
    before = {item_id: scores[METRIC] for item_id, scores in full_before.items()}
    after = {item_id: scores[METRIC] for item_id, scores in full_after.items()}
    report = Report(
        plain_recall=_mean(list(before.values())),
        enriched_recall=_mean(list(after.values())),
        baseline_recall=_mean([scores[METRIC] for scores in baseline.values()]),
        rejections=dict(rejections or {}),
        metrics=_pairs(list(before), full_before, full_after),
    )
    for segment in sorted({item.segment for item in items if item.id in before}):
        ids = [item.id for item in items if item.segment == segment and item.id in before]
        report.by_segment[segment] = (
            len(ids),
            _mean([before[i] for i in ids]),
            _mean([after[i] for i in ids]),
        )
        report.segment_metrics[segment] = _pairs(ids, full_before, full_after)
    report.improved = sorted(i for i in before if after[i] > before[i])
    report.worsened = sorted(i for i in before if after[i] < before[i])
    for item in items:
        if is_stale(contexts.get(item.id), enriched[item.id], retriever):
            report.stale.append(item.id)
            if item.segment in ZERO_TOLERANCE_SEGMENTS:
                report.stale_zero_tolerance.append(item.id)
    report.traps = [
        TrapRow(trap, chunk_id, _appearances(chunk_id, plain), _appearances(chunk_id, enriched))
        for chunk_id, trap in sorted(trap_chunks.items(), key=lambda pair: (pair[1], pair[0]))
    ]
    return report


def render(report: Report) -> str:
    entries = report.planted_entries
    lines = [
        "# RAG-02: enriched index against the plain index",
        "",
        f"recall@5: plain {report.plain_recall:.3f} (baseline file "
        f"{report.baseline_recall:.3f}), enriched {report.enriched_recall:.3f}, "
        f"delta {report.delta:+.3f}",
        "",
        "| Metric | Plain | Enriched | Delta |",
        "|---|---|---|---|",
    ]
    for metric, (before, after) in report.metrics.items():
        lines.append(f"| {metric} | {before:.3f} | {after:.3f} | {after - before:+.3f} |")
    lines += [
        "",
        "| Segment | Items | recall@5 plain | enriched | recall@10 plain | enriched "
        "| MRR plain | enriched |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for segment, scores in report.segment_metrics.items():
        cells = " | ".join(f"{v:.3f}" for metric in METRICS for v in scores[metric])
        lines.append(f"| {segment} | {report.by_segment[segment][0]} | {cells} |")
    lines += [
        "",
        f"Improved: {', '.join(report.improved) or 'none'}",
        f"Worsened: {', '.join(report.worsened) or 'none'}",
        "",
        f"Saved contexts that would change, so answers that go stale: {len(report.stale)}",
        f"Items: {', '.join(report.stale) or 'none'}",
        f"Adversarial or unanswerable: {', '.join(report.stale_zero_tolerance) or 'none'}",
        "",
        "## Generated sentences rejected, by reason",
        "",
        *(f"- {reason}: {count}" for reason, count in sorted(report.rejections.items())),
        *([] if report.rejections else ["None."]),
        "",
        "## Planted chunks entering a top 5",
        "",
        f"Entered a top 5: {len(entries) or 'none'}",
        *(f"- {trap} {chunk} entered {item}" for trap, chunk, item in entries),
        "",
        "## Planted chunks in a top 5, by item",
        "",
        "| Trap | Chunk | Plain | Enriched | Entered | Left |",
        "|---|---|---|---|---|---|",
    ]
    for row in report.traps:
        groups = (row.plain, row.enriched, row.entered, row.left)
        shown = " | ".join(", ".join(group) or "-" for group in groups)
        lines.append(f"| {row.trap_id} | {row.chunk_id} | {shown} |")
    return "\n".join(lines) + "\n"
