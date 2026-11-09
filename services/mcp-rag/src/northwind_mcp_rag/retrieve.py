"""The northwind-rag tools. `retrieve_context` lands here with B2, after row #9 (RAG-11) merges,
because its rendering carries #9's NFKC step. Until then the server offers no tool."""

from __future__ import annotations

from collections.abc import Mapping

from northwind_mcp_rag.server import ToolSpec

TOOLS: Mapping[str, ToolSpec] = {}
