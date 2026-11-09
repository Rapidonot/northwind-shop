"""RAG-03: compare dense, keyword and hybrid retrieval, and fingerprint live retrieval for drift.

The criterion is "beats dense-only and keyword-only on recall@5 in the evaluation harness". All
three arms run on the path a shopper gets (audience shopper, the item's region), over the
retrieval-scored items, with the parameters fixed in the plan before anything was measured. No LLM.

The fingerprint is the drift check from the 5b review: `score_drift` reads answers only, so the gate
prints every retrieval metric to 6 places and a SHA-256 over each item's live top-k chunk IDs. On
macOS and on Linux CI they must match exactly; a difference is a stop, not a tolerance.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from northwind_evals.contexts import ANSWER_CONTEXT_K
from northwind_evals.harness import score_retrieval
from northwind_evals.items import EvalItem
from northwind_evals.metrics import mean

ARMS = ("dense", "keyword", "hybrid")
CRITERION_METRIC = "recall@5"
TRAP_TOP_K = ANSWER_CONTEXT_K  # the answer context, as in `enrichment_report`


@dataclass(frozen=True)
class Comparison:
    """Mean retrieval metrics per arm, over `n` retrieval-scored items."""

    n: int
    means: dict[str, dict[str, float]]
    # Per arm, every (item, trap) where a planted injection review is in the item's top 5. Reported
    # for the user's call (security review, row #8); not part of the criterion.
    traps: dict[str, list[tuple[str, str]]] | None = None

    @property
    def hybrid_wins(self) -> bool:
        """Strictly above both other arms on recall@5. A tie is not a win."""
        hybrid = self.means["hybrid"][CRITERION_METRIC]
        return all(hybrid > self.means[arm][CRITERION_METRIC] for arm in ARMS if arm != "hybrid")


def mean_scores(
    items: Sequence[EvalItem], ranked_sources: Mapping[str, Sequence[str]]
) -> dict[str, float]:
    """Mean of each retrieval metric over the retrieval-scored items; empty if there are none."""
    scored = [
        score_retrieval(item, ranked_sources[item.id]) for item in items if item.retrieval_scored
    ]
    if not scored:
        return {}
    means: dict[str, float] = {}
    for metric in scored[0]:
        value = mean(row[metric] for row in scored)
        if value is None:
            raise ValueError(f"no values for {metric}")
        means[metric] = value
    return means


def trap_appearances(
    ranked: Mapping[str, Sequence[str]], trap_reviews: Mapping[str, str]
) -> list[tuple[str, str]]:
    """(item, trap) for each planted review in an item's top 5, over every item, sorted.
    `trap_reviews` maps a review ID to its trap ID. Counted per review; `enrichment_report` counts
    per chunk. The two agree while each planted review is one chunk, which a test pins."""
    return sorted(
        (item_id, trap_reviews[source])
        for item_id, sources in ranked.items()
        for source in dict.fromkeys(sources[:TRAP_TOP_K])
        if source in trap_reviews
    )


def compare(
    items: Sequence[EvalItem],
    ranked: Mapping[str, Mapping[str, Sequence[str]]],
    trap_reviews: Mapping[str, str] | None = None,
) -> Comparison:
    """`ranked[arm][item_id]` is that arm's ranked source IDs for the item. Retrieval metrics are
    over the retrieval-scored items; trap appearances are over every item, since a planted review
    can reach any context."""
    missing = [arm for arm in ARMS if arm not in ranked]
    if missing:
        raise ValueError(f"missing arms: {', '.join(missing)}")
    n = sum(1 for item in items if item.retrieval_scored)
    if n == 0:
        raise ValueError("no retrieval-scored items")
    traps = (
        None
        if trap_reviews is None
        else {arm: trap_appearances(ranked[arm], trap_reviews) for arm in ARMS}
    )
    return Comparison(n, {arm: mean_scores(items, ranked[arm]) for arm in ARMS}, traps)


def render(comparison: Comparison, retriever: str) -> str:
    metrics = sorted(comparison.means["hybrid"])
    lines = [
        "# RAG-03 retrieval comparison",
        "",
        "Written by `make eval-compare-retrievers` (no LLM). Each arm runs on the path a shopper",
        "gets: audience shopper, the item's own region. Means over the retrieval-scored items.",
        f"Hybrid is `{retriever}`.",
        "",
        f"Items: {comparison.n}",
        "",
        "| Arm | " + " | ".join(metrics) + " |",
        "|---|" + "---|" * len(metrics),
    ]
    for arm in ARMS:
        values = " | ".join(f"{comparison.means[arm][metric]:.6f}" for metric in metrics)
        lines.append(f"| {arm} | {values} |")
    verdict = "meets" if comparison.hybrid_wins else "does NOT meet"
    lines += [
        "",
        f"Hybrid {verdict} the criterion: {CRITERION_METRIC} strictly above dense-only and "
        "keyword-only.",
        "",
    ]
    if comparison.traps is not None:
        lines += [
            "## Planted injection reviews in a top 5",
            "",
            "Every item, each arm's top 5 source IDs, counting the TRAP-INJ reviews in the answer",
            "key, once per review. Each is one chunk, so this equals `enrichment_report`'s",
            "per-chunk count. Reported for the user's decision, not part of the criterion.",
            "",
            "| Arm | Appearances | (item, trap) |",
            "|---|---|---|",
        ]
        for arm in ARMS:
            pairs = comparison.traps[arm]
            shown = ", ".join(f"{item} {trap}" for item, trap in pairs) or "none"
            lines.append(f"| {arm} | {len(pairs)} | {shown} |")
        dense, hybrid = set(comparison.traps["dense"]), set(comparison.traps["hybrid"])
        new = ", ".join(f"{item} {trap}" for item, trap in sorted(hybrid - dense)) or "none"
        gone = ", ".join(f"{item} {trap}" for item, trap in sorted(dense - hybrid)) or "none"
        lines += [
            "",
            f"Dense to hybrid: {len(hybrid) - len(dense):+d}. New in hybrid: {new}. "
            f"Gone in hybrid: {gone}.",
            "",
        ]
    return "\n".join(lines)


def fingerprint(live_chunk_ids: Mapping[str, Sequence[str]]) -> str:
    """SHA-256 over every item's ranked chunk IDs, keyed and sorted by item ID."""
    canonical = json.dumps(
        {item_id: list(ids) for item_id, ids in sorted(live_chunk_ids.items())},
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def drift_lines(
    means: Mapping[str, float], live_chunk_ids: Mapping[str, Sequence[str]]
) -> list[str]:
    """What the gate prints for the macOS and Linux comparison: exact, never rounded to pass."""
    lines = [f"retrieval {metric} {means[metric]:.6f}" for metric in sorted(means)]
    lines.append(f"retrieval top-k sha256 {fingerprint(live_chunk_ids)}")
    return lines
