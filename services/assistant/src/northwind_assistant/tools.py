"""The assistant's only tool (SA-02).

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
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from claude_agent_sdk import SdkMcpTool, tool

from northwind_assistant.caller import Caller
from northwind_common.cards import contains_card_data
from northwind_rag.retrieval import RetrievedChunk

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
NO_SOURCES = "No sources matched this search."
FAILED_MESSAGE = "Search is unavailable right now. Tell the shopper you cannot look this up."
BAD_QUERY_MESSAGE = "The query must be non-empty text."
CARD_QUERY_MESSAGE = "Search queries must not hold card details."


class Retriever(Protocol):
    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]: ...


@dataclass
class ToolState:
    """What one turn's tool calls did. `seen` holds the only source IDs that may be cited."""

    seen: set[str] = field(default_factory=set)
    failed: bool = False


def render_sources(chunks: Sequence[RetrievedChunk]) -> str:
    """Untrusted text cannot open or close a tag (hard rule 6)."""
    return "\n".join(
        f'<source id="{html.escape(chunk.source_id)}" type="{html.escape(chunk.source_type)}">\n'
        f"{html.escape(chunk.text, quote=False)}\n</source>"
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


def retrieve_tool(caller: Caller, retriever: Retriever, state: ToolState) -> SdkMcpTool[Any]:
    @tool(TOOL_NAME, DESCRIPTION, INPUT_SCHEMA)
    async def retrieve_context(args: dict[str, Any]) -> dict[str, Any]:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return _error("validation", False, BAD_QUERY_MESSAGE)
        if contains_card_data(query):  # hard rule 1 names tool arguments too
            return _error("validation", False, CARD_QUERY_MESSAGE)
        try:
            chunks = await asyncio.to_thread(retriever.search, query, caller)
        except Exception as error:  # noqa: BLE001 - its text must not reach the model
            log.error("retrieve_context failed: %s", type(error).__name__)
            state.failed = True
            return _error("transient", True, FAILED_MESSAGE)
        state.seen.update(chunk.source_id for chunk in chunks)
        text = render_sources(chunks) if chunks else NO_SOURCES
        return {"content": [{"type": "text", "text": text}]}

    return retrieve_context
