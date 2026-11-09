"""Searching the index, and the assistant's only tool (SA-02).

`search_sources` is the one path to the retriever. The server calls it on the shopper's message
before the first model call (decision 0007), and `retrieve_context` calls it for refinement, so the
card check, failure handling and RAG-10's search cap cannot differ between the two. Its card check
guards the retriever; `stream_answer` ends the turn on a refusal, which is what guards the model.

`retrieve_context` is built per request as a closure over the server-side `Caller`: the schema
offers only `query`, and the handler reads only `query`, so nothing the model sends can change the
audience or region (hard rule 7). The output is labelled, escaped data (hard rule 6) and holds
nothing that varies between runs, so a recorded next request still hashes the same.

The SDK sends an uncaught exception's text to the model, and a database message can quote the
connection string, so the handler catches everything and returns fixed text (hard rule 10).
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from claude_agent_sdk import SdkMcpTool, tool

from northwind_assistant.caller import Caller
from northwind_common.cards import contains_card_data
from northwind_rag.retrieval import RetrievedChunk
from northwind_rag.sources import TRUSTED_SOURCE_TYPES, UNTRUSTED_SOURCE_TYPES

log = logging.getLogger(__name__)

SERVER_NAME = "northwind"
TOOL_NAME = "retrieve_context"
ALLOWED_TOOL = f"mcp__{SERVER_NAME}__{TOOL_NAME}"

DESCRIPTION = (
    "Search Northwind's product, review, guide and policy text. Returns sources to cite by id. "
    "It holds no prices or stock levels."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "What to search for"}},
    "required": ["query"],
    "additionalProperties": False,
}
# RAG-10: no answer needs more than 3 retrieval rounds, and the first is the server's own search.
MAX_SEARCHES = 3
NO_SOURCES = "No sources matched this search."
FAILED_MESSAGE = "Search is unavailable right now. Tell the shopper you cannot look this up."
BAD_QUERY_MESSAGE = "The query must be non-empty text."
CARD_QUERY_MESSAGE = "Search queries must not hold card details."
# Every source ID the generator writes fits this. Anything else is refused, not escaped, so a
# fullwidth quote or equals sign in an ID can never imitate an attribute (RAG-11 review, F7).
SOURCE_ID = re.compile(r"[A-Za-z0-9._-]+")
LIMIT_MESSAGE = (
    "The search limit is reached. Answer from the sources you have, or say the evidence is not "
    "enough."
)


class Retriever(Protocol):
    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]: ...


@dataclass
class ToolState:
    """What one turn's tool calls did. `seen` holds the only source IDs that may be cited."""

    seen: set[str] = field(default_factory=set)
    failed: bool = False
    searches: int = 0


@dataclass(frozen=True)
class SearchFailure:
    category: str
    retryable: bool
    message: str


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


def _error(category: str, retryable: bool, message: str) -> dict[str, Any]:
    body = {
        "isError": True,
        "errorCategory": category,
        "isRetryable": retryable,
        "message": message,
    }
    return {"content": [{"type": "text", "text": json.dumps(body)}], "is_error": True}


async def search_sources(
    query: Any, caller: Caller, retriever: Retriever, state: ToolState
) -> str | SearchFailure:
    """Rendered sources, or `NO_SOURCES` for a search that found nothing, or a failure: an access
    failure and a valid empty result stay distinct (hard rule 10). Audience and region come only
    from `caller`, which the server built."""
    if not isinstance(query, str) or not query.strip():
        return SearchFailure("validation", False, BAD_QUERY_MESSAGE)
    if "\x00" in query:  # Postgres text cannot hold NUL, so it would fail every time (RAG-03)
        return SearchFailure("validation", False, BAD_QUERY_MESSAGE)
    if contains_card_data(query):  # hard rule 1 names tool arguments too
        return SearchFailure("validation", False, CARD_QUERY_MESSAGE)
    if state.searches >= MAX_SEARCHES:
        return SearchFailure("business", False, LIMIT_MESSAGE)
    state.searches += 1
    try:
        chunks = await asyncio.to_thread(retriever.search, query, caller)
    except Exception as error:  # noqa: BLE001 - its text must not reach the model
        log.error("search failed: %s", type(error).__name__)
        state.failed = True
        return SearchFailure("transient", True, FAILED_MESSAGE)
    if not chunks:
        return NO_SOURCES
    try:
        rendered = render_sources(chunks)
    except ValueError:
        # An unclassified source type or a malformed ID: nothing from this search is shown or
        # counted as seen.
        log.error("search refused: unclassified source type or malformed source id")
        state.failed = True
        return SearchFailure("business", False, FAILED_MESSAGE)
    state.seen.update(chunk.source_id for chunk in chunks)
    return rendered


def retrieve_tool(caller: Caller, retriever: Retriever, state: ToolState) -> SdkMcpTool[Any]:
    @tool(TOOL_NAME, DESCRIPTION, INPUT_SCHEMA)
    async def retrieve_context(args: dict[str, Any]) -> dict[str, Any]:
        found = await search_sources(args.get("query"), caller, retriever, state)
        if isinstance(found, SearchFailure):
            return _error(found.category, found.retryable, found.message)
        return {"content": [{"type": "text", "text": found}]}

    return retrieve_context
