"""Contract tests for Northwind's MCP servers, over the real HTTP transport (CC-10 C5; PRD §7).

Every server in `SERVERS`, every agent in `tool_scopes`: the token gate, scoping, the error
envelope, and per tool the description rules and a closed schema, over each server's real tool
registry. mcp-catalog joined at #10b.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

import httpx
import mcp.types as t
import pytest

from northwind_common.errors import ENVELOPE_KEYS
from northwind_common.mcp_token import mint
from northwind_common.tool_scopes import AGENT_AUDIENCE, AGENT_TOOLS, agents, allowed_tools
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.server import SERVER_NAME as CATALOG
from northwind_mcp_catalog.tools import TOOLS as CATALOG_TOOLS
from northwind_mcp_rag import retrieve
from northwind_mcp_rag.server import SERVER_NAME as RAG
from northwind_mcp_rag.server import Backend, RagServer
from northwind_mcp_rag.testing import KEY, connect, running
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("CC-10")

# PRD §7: the tools a shopper-facing server may offer at all. All read-only.
READ_ONLY_TOOLS = frozenset(
    {
        "retrieve_context",
        "search_products",
        "get_product",
        "get_variant",
        "check_stock",
        "get_price",
    }
)
CALLER_KEYS = {"audience", "region", "agent", "aud", "visibility", "turn"}


def _unreachable() -> Any:
    raise RuntimeError("no database in contract tests")


SERVERS: dict[str, tuple[Callable[[], Any], dict[str, Any]]] = {
    RAG: (
        lambda: RagServer(Backend(_unreachable, FakeEmbedder()), key=KEY, tools=retrieve.TOOLS),
        dict(retrieve.TOOLS),
    ),
    CATALOG: (lambda: catalog_testing.server(key=KEY), dict(CATALOG_TOOLS)),
}
TOOL_CASES = [(server, name) for server, (_, tools) in SERVERS.items() for name in tools]


def server_token(
    server: str,
    agent: str = "shopper-assistant",
    *,
    now: float | None = None,
    key: bytes = KEY,
) -> str:
    """A token minted for `server` (L4, #10b), so each gate case fails for its own reason."""
    audience = AGENT_AUDIENCE[agent]
    region = "UK" if audience == "shopper" else None
    return mint(audience=audience, region=region, agent=agent, key=key, now=now, server=server)[0]


def agent_token(agent: str, server: str = RAG) -> str:
    return server_token(server, agent)


def reached(instance: Any) -> int:
    """Requests that reached a tool handler: mcp-rag's search ledger, mcp-catalog's call count."""
    return len(instance.ledger) if hasattr(instance, "ledger") else int(instance.calls)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_every_scoped_server_is_under_contract() -> None:
    scoped = {server for row in AGENT_TOOLS.values() for server in row}
    assert scoped <= set(SERVERS)


@pytest.mark.req("NFR-01")
@pytest.mark.parametrize("server", sorted(SERVERS))
@pytest.mark.parametrize("agent", sorted(agents()))
def test_tools_list_is_within_the_agents_scope_and_read_only(server: str, agent: str) -> None:
    async def go(url: str) -> list[t.Tool]:
        async with connect(url, agent_token(agent, server)) as client:
            return list((await client.list_tools()).tools)

    with running(SERVERS[server][0]()) as url:
        listed = run(go(url))
    names = {tool.name for tool in listed}
    # Exactly the agent's scope among what the server has: nothing missing, nothing extra.
    assert names == allowed_tools(agent, server) & set(SERVERS[server][1])
    assert names <= READ_ONLY_TOOLS
    assert all(tool.annotations and tool.annotations.read_only_hint for tool in listed)


@pytest.mark.req("NFR-01")
@pytest.mark.parametrize("server", sorted(SERVERS))
def test_no_scope_grants_a_tool_outside_prd_sevens_read_only_set(server: str) -> None:
    granted = {name for row in AGENT_TOOLS.values() for name in row.get(server, frozenset())}
    assert granted <= READ_ONLY_TOOLS


@pytest.mark.parametrize(("server", "name"), TOOL_CASES)
def test_a_description_says_when_not_to_use_it_and_gives_an_example(server: str, name: str) -> None:
    description = SERVERS[server][1][name].tool.description or ""
    assert re.search(r"\b(do not|don't|never|not for)\b", description, re.IGNORECASE)
    assert re.search(r"\bexample\b", description, re.IGNORECASE)


@pytest.mark.parametrize(("server", "name"), TOOL_CASES)
def test_an_input_schema_is_closed_and_takes_no_caller_identity(server: str, name: str) -> None:
    schema = SERVERS[server][1][name].tool.input_schema
    assert schema.get("type") == "object"
    assert schema.get("additionalProperties") is False
    assert not CALLER_KEYS & set(schema.get("properties", {}))


@pytest.mark.req("NFR-02")
@pytest.mark.parametrize("server", sorted(SERVERS))
@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer", "Basic abc", "Bearer v1.a.b", "expired", "forged"],
)
def test_a_request_without_a_valid_token_is_a_bare_401(
    server: str, authorization: str | None
) -> None:
    if authorization == "expired":
        authorization = f"Bearer {server_token(server, now=0.0)}"
    elif authorization == "forged":
        authorization = f"Bearer {server_token(server, key=b'f' * 40)}"
    headers = {"accept": "application/json, text/event-stream"}
    if authorization is not None:
        headers["authorization"] = authorization
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    instance = SERVERS[server][0]()
    with running(instance) as url:
        response = httpx.post(url, json=body, headers=headers, timeout=10)
    assert (response.status_code, response.content) == (401, b"")
    assert reached(instance) == 0


@pytest.mark.req("NFR-01")
@pytest.mark.parametrize("server", sorted(SERVERS))
@pytest.mark.parametrize("agent", sorted(agents()))
def test_a_tool_outside_the_scope_is_a_permission_envelope(server: str, agent: str) -> None:
    async def go(url: str) -> t.CallToolResult:
        async with connect(url, agent_token(agent, server)) as client:
            return await client.call_tool("refund_order", {"order_id": "O-1"})

    with running(SERVERS[server][0]()) as url:
        result = run(go(url))
    assert result.is_error is True
    [block] = result.content
    assert isinstance(block, t.TextContent)
    body = json.loads(block.text)
    assert set(body) == ENVELOPE_KEYS
    assert (body["errorCategory"], body["isRetryable"], body["code"]) == (
        "permission",
        False,
        "not_allowed",
    )
    assert result.structured_content == body


# ---- #10b: mcp-catalog ---------------------------------------------------------------------------

FIVE_CATALOG_TOOLS = {"search_products", "get_product", "get_variant", "check_stock", "get_price"}


def envelope_of(result: t.CallToolResult) -> dict[str, Any]:
    [block] = result.content
    assert isinstance(block, t.TextContent)
    body: dict[str, Any] = json.loads(block.text)
    return body


@pytest.mark.parametrize("agent", sorted(agents()))
def test_the_catalog_listing_is_within_the_five_read_only_tools(agent: str) -> None:
    async def go(url: str) -> list[t.Tool]:
        async with connect(url, agent_token(agent, CATALOG)) as client:
            return list((await client.list_tools()).tools)

    with running(SERVERS[CATALOG][0]()) as url:
        listed = run(go(url))
    names = {tool.name for tool in listed}
    assert names <= FIVE_CATALOG_TOOLS
    assert not {"set_price", "set_stock"} & names
    assert all(tool.annotations and tool.annotations.read_only_hint for tool in listed)
    if agent == "shopper-assistant":
        assert names == FIVE_CATALOG_TOOLS


def test_the_catalog_registry_is_exactly_the_five_read_only_tools() -> None:
    assert set(CATALOG_TOOLS) == FIVE_CATALOG_TOOLS
    assert allowed_tools("shopper-assistant", CATALOG) == FIVE_CATALOG_TOOLS
    assert allowed_tools("catalog-subagent", CATALOG) == frozenset()


@pytest.mark.req("NFR-01")
@pytest.mark.parametrize("name", ["set_price", "set_stock", "no_such_tool"])
def test_the_live_store_setters_are_not_tools_and_look_like_an_unknown_tool(name: str) -> None:
    async def go(url: str, tool: str) -> t.CallToolResult:
        async with connect(url, agent_token("shopper-assistant", CATALOG)) as client:
            return await client.call_tool(
                tool, {"variant_id": "P0001-BLK-REG-L", "amount": "0.01", "on_hand": 999}
            )

    instance = SERVERS[CATALOG][0]()
    before = instance.catalog.price("P0001-BLK-REG-L", "UK")
    with running(instance) as url:
        result = run(go(url, name))
        unknown = run(go(url, "no_such_tool"))
    body = envelope_of(result)
    assert result.is_error is True
    assert set(body) == ENVELOPE_KEYS
    assert (body["errorCategory"], body["isRetryable"], body["code"]) == (
        "permission",
        False,
        "not_allowed",
    )
    assert result.structured_content == body
    assert envelope_of(unknown) == body
    assert reached(instance) == 0
    assert instance.catalog.price("P0001-BLK-REG-L", "UK") == before


@pytest.mark.req("NFR-02")
@pytest.mark.parametrize(
    ("minted_for", "served"),
    [(RAG, CATALOG), (CATALOG, RAG)],
    ids=["rag-on-catalog", "catalog-on-rag"],
)
def test_a_token_minted_for_the_other_server_is_a_bare_401(minted_for: str, served: str) -> None:
    headers = {
        "accept": "application/json, text/event-stream",
        "authorization": f"Bearer {server_token(minted_for)}",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    instance = SERVERS[served][0]()
    with running(instance) as url:
        refused = httpx.post(url, json=body, headers=headers, timeout=10)
        good = httpx.post(
            url,
            json=body,
            headers={**headers, "authorization": f"Bearer {server_token(served)}"},
            timeout=10,
        )
    assert (refused.status_code, refused.content) == (401, b"")
    assert refused.headers["www-authenticate"] == "Bearer"
    assert good.status_code != 401
    assert reached(instance) == 0
