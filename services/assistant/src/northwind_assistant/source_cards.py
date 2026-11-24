"""A card per cited source, for the chat panel (row #17, SA-02).

The portal shows a cited product as an inline card and links every `[ID]` marker to its source's
entry. A card is built only from text mcp-rag returned this turn, for an ID that was cited:

- **Trusted sources** (`TRUSTED_SOURCE_TYPES`: product, variant, document) give a title, their first
  line. A product also gives brand and department, from its labelled `Brand: …. Department: ….` line
  (`northwind_rag.sources`).
- **A review** is text anyone can write (hard rule 6). Its card carries only its ID and type,
  never its title or body.
- **No price or stock** (hard rule 2): a card has no such field, and the index keeps exact prices
  and stock counts out of its text (RAG-09).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from northwind_assistant.output_check import Source
from northwind_rag.sources import TRUSTED_SOURCE_TYPES

MAX_FIELD_CHARS = 120
_BRAND_LINE = re.compile(
    r"^Brand: (?P<brand>[^\n]+?)\. Department: (?P<department>[^\n]+?)\.$", re.M
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class SourceCard:
    source_id: str
    source_type: str
    title: str | None = None
    brand: str | None = None
    department: str | None = None


def _short(text: str) -> str:
    """One line, no control characters, at most MAX_FIELD_CHARS."""
    line = _CONTROL.sub(" ", text).strip()
    return line if len(line) <= MAX_FIELD_CHARS else line[: MAX_FIELD_CHARS - 1].rstrip() + "…"


def _card(source: Source) -> SourceCard:
    if source.source_type not in TRUSTED_SOURCE_TYPES:
        return SourceCard(source.source_id, source.source_type)
    first = source.text.split("\n", 1)[0]
    title = _short(first) or None
    brand = department = None
    if source.source_type == "product" and (found := _BRAND_LINE.search(source.text)):
        brand, department = _short(found["brand"]), _short(found["department"])
    return SourceCard(source.source_id, source.source_type, title, brand, department)


def source_cards(cited: Iterable[str], sources: Iterable[Source]) -> tuple[SourceCard, ...]:
    """Cards for `cited`, in that order. A source's first chunk sets its card; a cited ID with no
    source text this turn gets none."""
    first: dict[str, Source] = {}
    for source in sources:
        first.setdefault(source.source_id, source)
    return tuple(_card(first[source_id]) for source_id in cited if source_id in first)
