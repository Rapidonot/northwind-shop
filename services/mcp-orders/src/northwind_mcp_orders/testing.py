"""Test helpers for mcp-orders (row #27): a real server over HTTP on a random port, and tokens
minted for northwind-orders, with or without a signed-in shopper."""

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

from northwind_common.mcp_token import ORDERS_SERVER, Claims, bearer, mint
from northwind_common.order_tool import unlabel
from northwind_mcp_orders.book import OrderBook
from northwind_mcp_orders.server import OrdersServer
from northwind_mcp_orders.tools import TOOLS

KEY = b"o" * 40
DEMO_SHOPPER = "S0188"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def server(book: OrderBook | None = None, *, key: bytes = KEY) -> OrdersServer:
    return OrdersServer(book or OrderBook.from_data(), key=key, tools=TOOLS)


@contextlib.contextmanager
def running(instance: Any) -> Iterator[str]:
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
            raise RuntimeError("mcp-orders failed to start")
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
    shopper: str | None = DEMO_SHOPPER,
    region: str | None = "US",
    *,
    agent: str = "shopper-assistant",
    now: float | None = None,
    key: bytes = KEY,
    server_name: str = ORDERS_SERVER,
) -> tuple[str, Claims]:
    audience: Any = "staff" if agent == "catalog-subagent" else "shopper"
    return mint(
        audience=audience,
        region=region,
        agent=agent,
        key=key,
        now=now,
        server=server_name,
        shopper=shopper,
    )


def body_of(text: str) -> Any:
    """A success's JSON from inside its `<order_data>` label (exactly one); an error's envelope."""
    if text.startswith("<"):
        labelled = unlabel(text)
        if labelled is None:
            raise AssertionError("a success result is not exactly one order_data label")
        return labelled[1]
    return json.loads(text)


async def call(url: str, bearer_token: str, name: str, arguments: dict[str, Any]) -> Any:
    """One tool call: (is_error, raw text, parsed body)."""
    async with connect(url, bearer_token) as client:
        result = await client.call_tool(name, arguments)
    [block] = result.content
    text = getattr(block, "text", "null")
    return bool(result.is_error), text, body_of(text)
