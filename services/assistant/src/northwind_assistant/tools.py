"""Searching through mcp-rag, and reading what a search said (SA-02; CC-10 C1', C4').

`search_sources` is the assistant's one path to a search. The server calls it on the shopper's
message before the first model call (decision 0007); the model's refinement calls go from the CLI
to the same `retrieve_context` on mcp-rag, with the same per-turn token, so the checks, the failure
handling and RAG-10's cap are the server's for both. On the first search the card check runs here
first, so card text in a shopper's message never leaves this process; a query the model writes goes
from the CLI to mcp-rag, which refuses card data in any argument before searching (hard rule 1).

What a search found or how it failed is read the same way for both: `record` takes a result's text
and error flag. Source IDs come from the server-rendered `<source id="…">` lines, so citations stay
limited to what was returned this turn (hard rule 5), and an error whose code means the search
failed marks the turn, so an answer with nothing behind it is an outage, not an answer (hard rule
10). Audience and region are in the token, built from the server-side `Caller` (hard rule 7).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from northwind_assistant.rag_client import SearchResult
from northwind_common.cards import contains_card_data
from northwind_common.errors import ENVELOPE_KEYS
from northwind_common.rag_tool import (
    ALLOWED_TOOL,
    BAD_QUERY_MESSAGE,
    CARD_QUERY_MESSAGE,
    DESCRIPTION,
    FAILED_MESSAGE,
    FAILURE_CODES,
    INPUT_SCHEMA,
    LIMIT_MESSAGE,
    MAX_SEARCHES,
    NO_SOURCES,
    SERVER_NAME,
    TOOL_NAME,
    source_ids,
)

__all__ = [
    "ALLOWED_TOOL",
    "BAD_QUERY_MESSAGE",
    "CARD_QUERY_MESSAGE",
    "DESCRIPTION",
    "FAILED_MESSAGE",
    "INPUT_SCHEMA",
    "LIMIT_MESSAGE",
    "MAX_SEARCHES",
    "NO_SOURCES",
    "SERVER_NAME",
    "TOOL_NAME",
    "SearchFailure",
    "Searcher",
    "ToolState",
    "record",
    "search_sources",
]

log = logging.getLogger(__name__)


class Searcher(Protocol):
    async def search(self, query: str) -> SearchResult: ...


@dataclass
class ToolState:
    """What one turn's searches did. `seen` holds the only source IDs that may be cited."""

    seen: set[str] = field(default_factory=set)
    failed: bool = False
    searches: int = 0


@dataclass(frozen=True)
class SearchFailure:
    category: str
    retryable: bool
    message: str


def _envelope(text: str) -> dict[str, Any] | None:
    try:
        body = json.loads(text)
    except ValueError:
        return None
    if isinstance(body, dict) and set(body) == ENVELOPE_KEYS and body.get("isError") is True:
        return body
    return None


def record(state: ToolState, *, is_error: bool, text: str) -> str | SearchFailure:
    """What one `retrieve_context` result means for the turn. A success's source IDs join `seen`;
    an error is a `SearchFailure`, and marks the turn failed when its code says the search failed,
    or when it is not our envelope at all (the CLI's own error text, a transport failure)."""
    if not is_error:
        state.seen.update(source_ids(text))
        return text
    body = _envelope(text)
    if body is None:
        state.failed = True
        return SearchFailure("transient", True, FAILED_MESSAGE)
    if body["code"] in FAILURE_CODES:
        state.failed = True
    return SearchFailure(
        str(body["errorCategory"]), bool(body["isRetryable"]), str(body["message"])
    )


async def search_sources(query: Any, searcher: Searcher, state: ToolState) -> str | SearchFailure:
    """Rendered sources, or `NO_SOURCES` for a search that found nothing, or a failure: an access
    failure and a valid empty result stay distinct (hard rule 10)."""
    if not isinstance(query, str) or not query.strip():
        return SearchFailure("validation", False, BAD_QUERY_MESSAGE)
    if "\x00" in query:  # Postgres text cannot hold NUL, so it would fail every time (RAG-03)
        return SearchFailure("validation", False, BAD_QUERY_MESSAGE)
    if contains_card_data(query):  # hard rule 1: the shopper's card text never leaves here
        return SearchFailure("validation", False, CARD_QUERY_MESSAGE)
    if state.searches >= MAX_SEARCHES:  # the server holds the cap too; this one never sends
        return SearchFailure("business", False, LIMIT_MESSAGE)
    state.searches += 1
    try:
        result = await searcher.search(query)
    except Exception as error:  # noqa: BLE001 - its text must not reach the model or a log
        log.error("search failed: %s", type(error).__name__)
        state.failed = True
        return SearchFailure("transient", True, FAILED_MESSAGE)
    return record(state, is_error=result.is_error, text=result.text)
