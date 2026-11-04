"""Splits text into chunks: on Markdown headings first, then down to a size limit (RAG-01)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from northwind_rag.models import MAX_CHUNK_CHARS, ChunkError

# Bump when a change here alters chunk output; it is recorded in the index manifest.
CHUNKER_VERSION = 1

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
# Coarsest boundary first, so a split keeps as much structure as the limit allows.
_BOUNDARIES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\n\s*\n"), "\n\n"),
    (re.compile(r"\n"), "\n"),
    (re.compile(r"(?<=[.!?])\s+"), " "),
    (re.compile(r"\s+"), " "),
)
_MIN_BODY_CHARS = 100


@dataclass(frozen=True)
class Section:
    """`path` holds the headings above the body. H1 is left out: the title already says it."""

    path: tuple[str, ...]
    body: str


def split_headings(markdown: str) -> list[Section]:
    """One section per heading, skipping headings with no body. `#` in a code fence is not one."""
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    lines: list[str] = []
    in_fence = False

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            sections.append(Section(tuple(title for _, title in stack), body))
        lines.clear()

    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading is None:
            lines.append(line)
            continue
        flush()
        level = len(heading.group(1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        if level == 1:
            stack.clear()
        else:
            stack.append((level, heading.group(2)))
    flush()
    return sections


def split_to_fit(text: str, limit: int) -> list[str]:
    """Pieces of at most `limit` characters, cut at the coarsest boundary that works."""
    return _split(text.strip(), limit, 0)


def _split(text: str, limit: int, level: int) -> list[str]:
    if len(text) <= limit:
        return [text] if text else []
    if level == len(_BOUNDARIES):
        return [text[i : i + limit] for i in range(0, len(text), limit)]
    pattern, joiner = _BOUNDARIES[level]
    pieces: list[str] = []
    current = ""
    for part in pattern.split(text):
        candidate = f"{current}{joiner}{part}" if current else part
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            pieces.append(current)
            current = ""
        if len(part) <= limit:
            current = part
        else:
            pieces.extend(_split(part, limit, level + 1))
    if current:
        pieces.append(current)
    return pieces


def with_prefix(path: tuple[str, ...], body: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Pieces of `body`, each led by its heading path. The limit includes the prefix."""
    prefix = " > ".join(path) + "\n\n"
    budget = max_chars - len(prefix)
    if budget < _MIN_BODY_CHARS:
        raise ChunkError(f"heading path too long for a {max_chars}-character chunk: {path}")
    return [prefix + piece for piece in split_to_fit(body, budget)]


def chunk_markdown(title: str, body: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Chunk texts for a Markdown document, split on headings and capped at `max_chars`."""
    return [
        text
        for section in split_headings(body)
        for text in with_prefix((title, *section.path), section.body, max_chars)
    ]
