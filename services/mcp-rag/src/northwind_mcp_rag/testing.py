"""Test helpers: run a real mcp-rag over HTTP in a thread, and talk to it as an MCP client (CC-10).

Imported by tests in any directory (no conftest change): `with running(server) as url:` then
`async with connect(url, token) as client:`.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from northwind_common.mcp_token import Claims, bearer, mint
from northwind_mcp_rag.server import RagServer

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
