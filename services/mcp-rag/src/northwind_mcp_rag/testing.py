"""Test helpers: run a real mcp-rag over HTTP in a thread, and talk to it as an MCP client (CC-10).

Imported by tests in any directory (no conftest change): `with running(server) as url:` then
`async with connect(url, token) as client:`.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import Any

import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from northwind_common.mcp_token import Claims, bearer, mint
from northwind_mcp_rag.server import Backend, RagServer
from northwind_rag.retrieval import RetrievedChunk
from northwind_rag.testing import FakeEmbedder

KEY = b"t" * 40


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def running(server: RagServer) -> Iterator[str]:
    port = free_port()
    config = uvicorn.Config(
        server.app(), host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not uv.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("mcp-rag failed to start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        uv.should_exit = True
        thread.join(timeout=10)


@contextlib.asynccontextmanager
async def connect(url: str, token: str) -> AsyncIterator[Client]:
    http = create_mcp_http_client(headers=bearer(token))
    async with Client(streamable_http_client(url, http_client=http)) as client:
        yield client


def token(
    agent: str = "shopper-assistant",
    region: str | None = "UK",
    *,
    now: float | None = None,
    key: bytes = KEY,
) -> tuple[str, Claims]:
    audience: Any = "staff" if agent == "catalog-subagent" else "shopper"
    return mint(audience=audience, region=region, agent=agent, key=key, now=now)


class CallerSearcher:
    """Adapts a test double with `search(query, caller)` (the assistant's tests' shape) to the
    server's `Searcher`, building the caller from the token's audience and region."""

    def __init__(self, retriever: Any, make_caller: Callable[..., Any]) -> None:
        self.retriever = retriever
        self.make_caller = make_caller

    def search(self, query: str, *, audience: str, region: str | None) -> Sequence[RetrievedChunk]:
        result: Sequence[RetrievedChunk] = self.retriever.search(
            query, self.make_caller(region=region, audience=audience)
        )
        return result


def _no_db() -> Any:
    raise RuntimeError("no database behind this test server")


@contextlib.contextmanager
def serve_retriever(
    retriever: Any, make_caller: Callable[..., Any], *, key: bytes = KEY
) -> Iterator[tuple[str, RagServer]]:
    """A real mcp-rag over HTTP, with the real `retrieve_context`, searching through `retriever`."""
    from northwind_mcp_rag.retrieve import TOOLS

    server = RagServer(
        Backend(_no_db, FakeEmbedder()),
        key=key,
        tools=TOOLS,
        searcher=CallerSearcher(retriever, make_caller),
    )
    with running(server) as url:
        yield url, server


async def call_as_dict(url: str, bearer_token: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """`retrieve_context` over MCP, as the in-process tool's result dict: `content` blocks, plus
    `is_error: True` on an error."""
    from northwind_common.rag_tool import TOOL_NAME

    async with connect(url, bearer_token) as client:
        result = await client.call_tool(TOOL_NAME, arguments)
    out: dict[str, Any] = {
        "content": [b.model_dump(exclude_none=True, by_alias=True) for b in result.content]
    }
    if result.is_error:
        out["is_error"] = True
    return out


class CallerIndex:
    """The real index in the assistant tests' `search(query, caller)` shape, for wrapping in a spy
    and serving with `serve_retriever` (the production search path, `IndexSearcher`)."""

    def __init__(self, connect: Callable[[], Any], embedder: Any) -> None:
        from northwind_mcp_rag.server import IndexSearcher

        self.embedder = embedder
        self._index = IndexSearcher(Backend(connect, embedder))

    def search(self, query: str, caller: Any) -> Sequence[RetrievedChunk]:
        return self._index.search(query, audience=caller.audience, region=caller.region)
