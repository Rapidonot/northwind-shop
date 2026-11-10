"""The `retrieve_context` contract between mcp-rag and the assistant (CC-10; SA-02, RAG-10).

The server (`northwind_mcp_rag`) and its one client (`northwind_assistant`) both import this, so
neither depends on the other. What the model is shown is fixed text from here: the description, the
schema, and the messages a search can end in. A success is the rendered `<source id="…">` blocks or
`NO_SOURCES`, as text only (S0 item 5); an error is the structured envelope with a `code`.
"""

from __future__ import annotations

import re
from typing import Any

SERVER_NAME = "northwind-rag"
TOOL_NAME = "retrieve_context"
ALLOWED_TOOL = f"mcp__{SERVER_NAME}__{TOOL_NAME}"

# PRD §7 tool design rules: what, when, when not, the format, one example.
DESCRIPTION = (
    "Search Northwind's product, review, guide and policy text. "
    "Use it when the sources you already have do not answer the question. Do not use it for "
    "prices, stock levels, promotions, delivery dates or order status: it holds none of them. "
    "Returns source blocks, each with an id to cite, or a line saying nothing matched. "
    'Example: {"query": "how to wash a merino jumper"}.'
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
LIMIT_MESSAGE = (
    "The search limit is reached. Answer from the sources you have, or say the evidence is not "
    "enough."
)

# Codes, one per way a search can end in an error.
BAD_QUERY = "bad_query"
CARD_DATA = "card_data"
SEARCH_LIMIT = "search_limit"
SEARCH_FAILED = "search_failed"  # the index could not be searched
SOURCE_REFUSED = "source_refused"  # a result could not be shown (unclassified type, bad id)
# An error with one of these codes, or any error that is not our envelope, means the search failed
# rather than found nothing (hard rule 10), so an answer with no sources behind it is an outage.
FAILURE_CODES = frozenset({SEARCH_FAILED, SOURCE_REFUSED, "tool_failed", "not_allowed"})

# Every source ID the generator writes fits this; the server refuses any other (RAG-11, F7).
SOURCE_ID = re.compile(r"[A-Za-z0-9._-]+")
# The opening line of each rendered source. Chunk text is escaped (`<` is `&lt;`), so only the
# server's own lines can match.
SOURCE_LINE = re.compile(r'^<source id="([A-Za-z0-9._-]+)" type="[a-z]+">$', re.MULTILINE)


def source_ids(rendered: str) -> list[str]:
    """The source IDs in a rendered result, in order."""
    return SOURCE_LINE.findall(rendered)
