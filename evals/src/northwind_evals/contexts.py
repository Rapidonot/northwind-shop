"""Saved retrieved contexts (RAG-12).

Each item's answer is generated from the context saved here, never from live retrieval, so a
replayed request is byte-identical on any platform. Live retrieval is compared with the saved
chunk IDs instead: if they differ, the item is stale, which is a reported state, not a cassette
miss.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

ANSWER_CONTEXT_K = 5


@dataclass(frozen=True)
class ContextChunk:
    chunk_id: str
    source_id: str
    source_type: str
    text: str


@dataclass(frozen=True)
class SavedContext:
    item_id: str
    retriever_id: str
    index_version: str
    chunks: tuple[ContextChunk, ...]

    @property
    def chunk_ids(self) -> list[str]:
        return [chunk.chunk_id for chunk in self.chunks]


def load_contexts(path: Path) -> dict[str, SavedContext]:
    saved: dict[str, SavedContext] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        chunks = tuple(ContextChunk(**chunk) for chunk in record.pop("chunks"))
        saved[record["item_id"]] = SavedContext(chunks=chunks, **record)
    return saved


def save_contexts(path: Path, contexts: Iterable[SavedContext]) -> None:
    lines = [
        json.dumps(asdict(context), sort_keys=True, ensure_ascii=False) for context in contexts
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def is_stale(saved: SavedContext | None, live_chunk_ids: Sequence[str]) -> bool:
    """True if live retrieval no longer returns exactly the saved chunks, in the saved order."""
    return saved is None or saved.chunk_ids != list(live_chunk_ids[:ANSWER_CONTEXT_K])
