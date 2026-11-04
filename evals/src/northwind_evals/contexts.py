"""Saved retrieved contexts (RAG-12).

Each item's answer is generated from the context saved here, never from live retrieval, so a
replayed request is byte-identical on any platform. Live retrieval is compared with the saved
chunks instead, by ID and text: if they differ, the item is stale, which is a reported state, not a
cassette miss.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from northwind_llm_replay.scan import scan_text

ANSWER_CONTEXT_K = 5
_VISIBILITY_FILES = (
    ("documents.jsonl", "doc_id"),
    ("products.jsonl", "product_id"),
    ("variants.jsonl", "variant_id"),
    ("reviews.jsonl", "review_id"),
)


class LiveChunk(Protocol):
    @property
    def chunk_id(self) -> str: ...

    @property
    def text(self) -> str: ...


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
    def fingerprint(self) -> list[tuple[str, str]]:
        return [(chunk.chunk_id, chunk.text) for chunk in self.chunks]


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


def is_stale(saved: SavedContext | None, live: Sequence[LiveChunk], retriever: str) -> bool:
    """True unless the same retriever still returns the saved chunks, in order, with their text."""
    if saved is None or saved.retriever_id != retriever:
        return True
    return saved.fingerprint != [(c.chunk_id, c.text) for c in live[:ANSWER_CONTEXT_K]]


def internal_sources(fixtures: Path) -> frozenset[str]:
    """Every internal source ID. Variants carry no canary, so canaries alone cannot catch them."""
    found: set[str] = set()
    for name, key in _VISIBILITY_FILES:
        for line in (fixtures / name).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["visibility"] == "internal":
                found.add(record[key])
    return frozenset(found)


def leaks(
    contexts: Mapping[str, SavedContext], internal: frozenset[str], canaries: frozenset[str]
) -> list[str]:
    """Items whose saved context holds an internal source, a canary, a card number or a key shape
    (hard rules 1, 4, 7). Decoded text is scanned, so this sees what the model would see."""
    return [
        item_id
        for item_id, context in contexts.items()
        if any(
            chunk.source_id in internal or scan_text(chunk.text, canaries=canaries)
            for chunk in context.chunks
        )
    ]
