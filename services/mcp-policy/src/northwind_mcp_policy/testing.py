"""Test helpers for mcp-policy (row #27 PR 2): a real server on a random port, and tokens minted for
northwind-policy, with or without a signed-in shopper."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from northwind_common.mcp_token import POLICY_SERVER, mint
from northwind_common.policy_tool import unlabel
from northwind_mcp_orders.book import OrderBook
from northwind_mcp_orders.policy_data import PolicyData
from northwind_mcp_orders.testing import connect, running
from northwind_mcp_policy.server import PolicyServer, ToolSpec
from northwind_mcp_policy.tools import TOOLS

__all__ = ["DEMO_SHOPPER", "KEY", "call", "connect", "running", "server", "token"]

KEY = b"p" * 40
DEMO_SHOPPER = "S0188"


def server(
    book: OrderBook | None = None,
    *,
    key: bytes = KEY,
    tools: Mapping[str, ToolSpec] = TOOLS,
) -> PolicyServer:
    return PolicyServer(
        book or OrderBook.from_data(), PolicyData.from_fixtures(), key=key, tools=tools
    )


def token(
    shopper: str | None = DEMO_SHOPPER,
    region: str | None = "US",
    *,
    agent: str = "shopper-assistant",
    key: bytes = KEY,
    server_name: str = POLICY_SERVER,
) -> str:
    audience: Any = "staff" if agent == "catalog-subagent" else "shopper"
    return mint(
        audience=audience, region=region, agent=agent, key=key, server=server_name, shopper=shopper
    )[0]


async def call(url: str, bearer_token: str, name: str, arguments: dict[str, Any]) -> Any:
    """One tool call: (is_error, raw text, parsed body)."""
    async with connect(url, bearer_token) as client:
        result = await client.call_tool(name, arguments)
    [block] = result.content
    text = getattr(block, "text", "null")
    if not result.is_error:
        labelled = unlabel(text)
        assert labelled is not None, "a success is exactly one policy_data label"  # noqa: S101
        return False, text, labelled[1]
    return True, text, json.loads(text)
