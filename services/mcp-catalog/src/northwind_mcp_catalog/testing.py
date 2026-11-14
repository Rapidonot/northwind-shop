"""Test helpers for mcp-catalog (#10b): a real server over HTTP on a random port, and tokens minted
for northwind-catalog."""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from northwind_common.catalog_tool import unlabel
from northwind_common.mcp_token import CATALOG_SERVER, Claims, bearer, mint
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.tools import TOOLS

KEY = b"t" * 40


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def server(catalog: FixtureCatalog | None = None, *, key: bytes = KEY) -> CatalogServer:
    """A catalog server over the real, verified fixtures (or the given store)."""
    return CatalogServer(catalog or FixtureCatalog(), key=key, tools=TOOLS)


@contextlib.contextmanager
def running(instance: CatalogServer) -> Iterator[str]:
    port = free_port()
    config = uvicorn.Config(
        instance.app(), host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not uv.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("mcp-catalog failed to start")
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
    server_name: str = CATALOG_SERVER,
) -> tuple[str, Claims]:
    audience: Any = "staff" if agent == "catalog-subagent" else "shopper"
    return mint(audience=audience, region=region, agent=agent, key=key, now=now, server=server_name)


def body_of(text: str) -> Any:
    """A result's JSON: a success's from inside its `<catalog_data>` label (row #12, L6), which must
    be exactly one well-formed label; an error's, the bare envelope, as it is."""
    if text.startswith("<"):
        labelled = unlabel(text)
        if labelled is None:
            raise AssertionError("a success result is not exactly one catalog_data label")
        return labelled[1]
    return json.loads(text)


async def call(url: str, bearer_token: str, name: str, arguments: dict[str, Any]) -> Any:
    """One tool call; returns (is_error, parsed JSON body)."""
    async with connect(url, bearer_token) as client:
        result = await client.call_tool(name, arguments)
    [block] = result.content
    return bool(result.is_error), body_of(getattr(block, "text", "null"))
