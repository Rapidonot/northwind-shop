"""Helpers for the RAG-02 tests: a complete cache built without a model."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from northwind_rag.enrichment import CacheEntry, judge, parents_of
from northwind_rag.models import Chunk

MODEL = "claude-haiku-5-5"
# A sentence that no check should refuse, for any kind of chunk.
NEUTRAL = "This passage sits inside the Northwind catalog record it belongs to."


def write_entries(directory: Path, entries: Iterable[CacheEntry]) -> None:
    """Writes `<visibility>.jsonl` the way the cache reads it, one entry per key."""
    by_file: dict[str, dict[str, CacheEntry]] = {}
    for entry in entries:
        by_file.setdefault(entry.visibility, {})[entry.key] = entry
    directory.mkdir(parents=True, exist_ok=True)
    for visibility, found in by_file.items():
        lines = (json.dumps(asdict(found[k]), ensure_ascii=False, sort_keys=True) for k in found)
        (directory / f"{visibility}.jsonl").write_text(
            "".join(line + "\n" for line in lines), encoding="utf-8"
        )


def neutral_entries(
    chunks: Sequence[Chunk], contexts: Mapping[str, str] | None = None
) -> list[CacheEntry]:
    """One judged entry per chunk. `contexts` overrides the sentence for given chunk IDs."""
    parents = parents_of(chunks)
    return [
        judge(c, parents[c.chunk_id], MODEL, (contexts or {}).get(c.chunk_id, NEUTRAL), "end_turn")
        for c in chunks
    ]


def write_complete_cache(
    directory: Path, chunks: Sequence[Chunk], contexts: Mapping[str, str] | None = None
) -> None:
    write_entries(directory, neutral_entries(chunks, contexts))
