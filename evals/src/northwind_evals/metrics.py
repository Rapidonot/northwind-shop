"""Retrieval metrics and per-segment aggregation (RAG-12).

An item's relevance is per source, and `ranked_sources` holds one entry per retrieved chunk, so
several chunks of one source do not count more than once toward recall.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence


def recall_at_k(ranked_sources: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Share of the relevant sources that appear in the top `k` chunks."""
    wanted = set(relevant)
    if not wanted:
        raise ValueError("recall needs at least one relevant source")
    return len(wanted & set(ranked_sources[:k])) / len(wanted)


def reciprocal_rank(ranked_sources: Sequence[str], relevant: Iterable[str]) -> float:
    """1 / rank of the first chunk from a relevant source; 0 if none was retrieved."""
    wanted = set(relevant)
    for rank, source in enumerate(ranked_sources, start=1):
        if source in wanted:
            return 1 / rank
    return 0.0


def mean(values: Iterable[float]) -> float | None:
    """None, not 0, when nothing was scored: "no data" must not read as a failing score."""
    collected = list(values)
    return sum(collected) / len(collected) if collected else None


def aggregate(
    scores: Mapping[str, Mapping[str, float]], segments: Mapping[str, str]
) -> dict[str, dict[str, float | None]]:
    """Means per metric, overall and per segment. `scores` is item ID -> metric -> value."""
    groups: dict[str, list[str]] = {"overall": list(scores)}
    for item_id in scores:
        groups.setdefault(segments[item_id], []).append(item_id)
    result: dict[str, dict[str, float | None]] = {}
    for group, ids in groups.items():
        names = sorted({name for item_id in ids for name in scores[item_id]})
        result[group] = {
            name: mean(scores[i][name] for i in ids if name in scores[i]) for name in names
        }
        result[group]["n"] = len(ids)
    return result
