"""`retrieve_context`, the northwind-rag tool (CC-10 B2; SA-02, RAG-10, RAG-11; hard rules 1, 6, 7).

One handler for every search a turn makes: the gateway's search before the first model call and
the model's refinements (decision 0007), so the checks, the failure handling and RAG-10's cap
cannot differ between them. It reads only `query`; audience and region come from the token, so
anything else in the arguments changes nothing. Card data is refused before any search (hard rule
1 names tool arguments too). The output is the rendered sources or `NO_SOURCES`, as text; every
failure is a fixed envelope, with nothing of the exception.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import mcp.types as t

from northwind_common.cards import contains_card_data
from northwind_common.errors import ToolError
from northwind_common.mcp_token import Claims
from northwind_common.rag_tool import (
    BAD_QUERY,
    BAD_QUERY_MESSAGE,
    CARD_DATA,
    CARD_QUERY_MESSAGE,
    DESCRIPTION,
    FAILED_MESSAGE,
    INPUT_SCHEMA,
    LIMIT_MESSAGE,
    NO_SOURCES,
    SEARCH_FAILED,
    SEARCH_LIMIT,
    SOURCE_REFUSED,
    TOOL_NAME,
)
from northwind_mcp_rag.render import render_sources
from northwind_mcp_rag.server import RagServer, ToolSpec, text_result

log = logging.getLogger("northwind_mcp_rag")


def _bad_query() -> ToolError:
    return ToolError("validation", False, BAD_QUERY, BAD_QUERY_MESSAGE)


async def retrieve_context(
    server: RagServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise _bad_query()
    if "\x00" in query:  # Postgres text cannot hold NUL, so it would fail every time (RAG-03)
        raise _bad_query()
    if contains_card_data(query):
        raise ToolError("validation", False, CARD_DATA, CARD_QUERY_MESSAGE)
    if not await server.ledger.take(claims.turn, claims.exp):
        raise ToolError("business", False, SEARCH_LIMIT, LIMIT_MESSAGE)
    try:
        chunks = await asyncio.to_thread(server.search, query, claims)
    except Exception as error:  # noqa: BLE001 - its text must not reach the model
        log.error("search failed: %s", type(error).__name__)
        raise ToolError("transient", True, SEARCH_FAILED, FAILED_MESSAGE) from None
    if not chunks:
        return text_result(NO_SOURCES)
    try:
        rendered = render_sources(chunks)
    except ValueError:
        # An unclassified source type or a malformed ID: nothing from this search is shown.
        log.error("search refused: unclassified source type or malformed source id")
        raise ToolError("business", False, SOURCE_REFUSED, FAILED_MESSAGE) from None
    return text_result(rendered)


TOOLS: Mapping[str, ToolSpec] = {
    TOOL_NAME: ToolSpec(
        tool=t.Tool(
            name=TOOL_NAME,
            description=DESCRIPTION,
            input_schema=INPUT_SCHEMA,
            annotations=t.ToolAnnotations(read_only_hint=True, open_world_hint=False),
        ),
        handler=retrieve_context,
    )
}
