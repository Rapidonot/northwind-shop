"""Retrieved text as labelled, inert data for the model (hard rule 6; RAG-03, RAG-11).

Moved unchanged from `northwind_assistant.tools` (row #10, C3/B2), with row #9's NFKC step: the
server renders, so what the model sees cannot depend on which client asked.
"""

from __future__ import annotations

import html
import unicodedata
from collections.abc import Sequence

from northwind_common.rag_tool import SOURCE_ID
from northwind_rag.retrieval import RetrievedChunk
from northwind_rag.sources import TRUSTED_SOURCE_TYPES, UNTRUSTED_SOURCE_TYPES


def as_data(text: str) -> str:
    """Untrusted text as inert data: NFKC first, then escaping (RAG-11). The order matters: NFKC
    folds fullwidth and small-form lookalikes such as `＜` (U+FF1C) to ASCII `<`, so escaping
    first would leave a raw `<` behind. NFKC keeps format characters such as U+200B, so the text
    the model sees changes only where a compatibility form folds."""
    return html.escape(unicodedata.normalize("NFKC", text), quote=False)


def render_sources(chunks: Sequence[RetrievedChunk]) -> str:
    """Untrusted text cannot open or close a tag (hard rule 6). A trusted type and an untrusted one
    render alike: the prompt's allow-list, built from TRUSTED_SOURCE_TYPES, says which `type` may
    set facts, so reviews stay visible as one person's experience. A type in neither set is refused,
    so a new source type is never shown before it is classified (RAG-03). The text goes through
    `as_data`. The type must match the trusted and untrusted sets exactly, and an ID outside
    `SOURCE_ID` is refused here (ingest only requires one to be present), so both are plain ASCII
    that `html.escape` leaves as it is (RAG-11)."""
    unknown = sorted(
        {chunk.source_type for chunk in chunks} - TRUSTED_SOURCE_TYPES - UNTRUSTED_SOURCE_TYPES
    )
    if unknown:
        raise ValueError(f"unclassified source type: {', '.join(unknown)}")
    if any(not SOURCE_ID.fullmatch(chunk.source_id) for chunk in chunks):
        raise ValueError("source id outside the allowed characters")
    return "\n".join(
        f'<source id="{html.escape(chunk.source_id)}" type="{html.escape(chunk.source_type)}">\n'
        f"{as_data(chunk.text)}\n</source>"
        for chunk in chunks
    )
