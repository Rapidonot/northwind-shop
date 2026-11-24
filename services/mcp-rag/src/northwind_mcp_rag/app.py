"""Run the northwind-rag MCP server: `python -m northwind_mcp_rag.app` (`make dev-mcp-rag`).

The signing key comes only from `NORTHWIND_MCP_TOKEN_KEY` (hard rule 4); the server refuses to
start without it. It listens on 127.0.0.1 only, one worker, on this worktree's port
(`northwind_common.localenv.mcp_rag_port`). `MCP_RAG_EMBEDDER=cached` serves the frozen vectors
(decision 0012), as the smoke E2E does; the default is the live model, as the gateway used.
"""

from __future__ import annotations

import os

import psycopg
import uvicorn

from northwind_common.localenv import announce_database, mcp_rag_port
from northwind_common.mcp_token import signing_key
from northwind_mcp_rag.retrieve import TOOLS
from northwind_mcp_rag.server import Backend, RagServer
from northwind_rag.cli import DEFAULT_DATABASE_URL
from northwind_rag.embedding import CachedEmbedder, Embedder, FastEmbedEmbedder


def _connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    return psycopg.connect(url, connect_timeout=2, autocommit=True)


def embedder() -> Embedder:
    model = FastEmbedEmbedder()
    return CachedEmbedder(model) if os.environ.get("MCP_RAG_EMBEDDER") == "cached" else model


def main() -> None:
    # Row #16: which database the index is read from, by name only (the scratch-database proof).
    announce_database("mcp-rag", os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    server = RagServer(Backend(_connect, embedder()), key=signing_key(), tools=TOOLS)
    uvicorn.run(server.app(), host="127.0.0.1", port=mcp_rag_port(), workers=1, log_level="warning")


if __name__ == "__main__":
    main()
