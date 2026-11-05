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


@dataclass(frozen=True)
class TrapRow:
    trap_id: str
    chunk_id: str
    plain: tuple[str, ...]
    enriched: tuple[str, ...]

    @property
    def increased(self) -> bool:
        return len(self.enriched) > len(self.plain)


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

    @property
    def delta(self) -> float:
        return self.enriched_recall - self.plain_recall

    @property
    def trap_increases(self) -> list[TrapRow]:
        return [row for row in self.traps if row.increased]


def _scores(items: Sequence[EvalItem], live: Live) -> dict[str, float]:
    return {
        item.id: score_retrieval(item, [c.source_id for c in live[item.id]])[METRIC]
        for item in items
        if item.retrieval_scored
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
) -> Report:
    """`trap_chunks` maps each planted chunk's ID to its trap, for every TRAP-INJ chunk."""
    before = _scores(items, plain)
    after = _scores(items, enriched)
    report = Report(
        plain_recall=_mean(list(before.values())),
        enriched_recall=_mean(list(after.values())),
        baseline_recall=_mean([scores[METRIC] for scores in baseline.values()]),
    )
    for segment in sorted({item.segment for item in items if item.id in before}):
        ids = [item.id for item in items if item.segment == segment and item.id in before]
        report.by_segment[segment] = (
            len(ids),
            _mean([before[i] for i in ids]),
            _mean([after[i] for i in ids]),
        )
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
    lines = [
        "# RAG-02: enriched index against the plain index",
        "",
        f"recall@5: plain {report.plain_recall:.3f} (baseline file "
        f"{report.baseline_recall:.3f}), enriched {report.enriched_recall:.3f}, "
        f"delta {report.delta:+.3f}",
        "",
        "| Segment | Items | Plain | Enriched | Delta |",
        "|---|---|---|---|---|",
    ]
    for segment, (count, before, after) in report.by_segment.items():
        lines.append(
            f"| {segment} | {count} | {before:.3f} | {after:.3f} | {after - before:+.3f} |"
        )
    lines += [
        "",
        f"Improved: {', '.join(report.improved) or 'none'}",
        f"Worsened: {', '.join(report.worsened) or 'none'}",
        "",
        f"Saved contexts that would change, so answers that go stale: {len(report.stale)}",
        f"Items: {', '.join(report.stale) or 'none'}",
        f"Adversarial or unanswerable: {', '.join(report.stale_zero_tolerance) or 'none'}",
        "",
        "## Planted injection chunks in a top 5",
        "",
        "| Trap | Chunk | Plain | Enriched | Increase | Items added |",
        "|---|---|---|---|---|---|",
    ]
    for row in report.traps:
        added = sorted(set(row.enriched) - set(row.plain))
        lines.append(
            f"| {row.trap_id} | {row.chunk_id} | {len(row.plain)} | {len(row.enriched)} | "
            f"{'yes' if row.increased else 'no'} | {', '.join(added) or '-'} |"
        )
    return "\n".join(lines) + "\n"
