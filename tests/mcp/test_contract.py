"""Contract tests for Northwind's MCP servers, over the real HTTP transport (CC-10 C5; PRD §7).

Every server in `SERVERS`, every agent in `tool_scopes`: the token gate, scoping, the error
envelope, and per tool the description rules and a closed schema, over each server's real tool
registry. mcp-catalog joined at #10b, mcp-orders at row #27 (CHK-06), mcp-policy at its PR 2
(CHK-07).
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
from northwind_mcp_orders import testing as orders_testing
from northwind_mcp_orders.server import SERVER_NAME as ORDERS
from northwind_mcp_orders.tools import TOOLS as ORDERS_TOOLS
from northwind_mcp_policy import testing as policy_testing
from northwind_mcp_policy.server import SERVER_NAME as POLICY
from northwind_mcp_policy.tools import TOOLS as POLICY_TOOLS
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
        "get_order",  # row #27 (CHK-06): PRD §7 "get_order (own orders)"
        "evaluate_return_eligibility",  # row #27 PR 2 (CHK-07): PRD §7
    }
)
# Row #16 (CHK-01): PRD §7's "cart tools". `get_cart` reads; the three writes change only the
# caller's own cart. They are the only shopper-facing tools that are not read-only. Exact sets.
CART_READ_TOOLS = frozenset({"get_cart"})
CART_WRITE_TOOLS = frozenset({"add_to_cart", "remove_from_cart", "set_cart_quantity"})
# Row #27 PR 2 (CHK-07, decision 0024; the user's D6 and D8): one write outside PRD §7, the
# shopper's own return request. An exact one-tool set.
RETURN_WRITE_TOOLS = frozenset({"request_return"})
WRITE_TOOLS = CART_WRITE_TOOLS | RETURN_WRITE_TOOLS
SHOPPER_TOOLS = READ_ONLY_TOOLS | CART_READ_TOOLS | WRITE_TOOLS
# Row #11 (RAG-04, decision 0022; approved by the user under stop 5): one server-only tool, outside
# PRD §7's shopper set, granted to exactly one agent that no model holds. Read-only, IDs only.
SERVER_ONLY_TOOLS: dict[str, str] = {"eligible_products": "shopper-query"}
# SA-01 PR 1 (approved by the user under stop 5, 2026-11-26): the shop pages' two reads, outside PRD
# §7's shopper set, granted only to `shopper-portal`, the gateway's own agent, which no model holds.
# Read-only. Exact: that agent's catalog row is these, `get_cart` and `get_product`, nothing else.
PORTAL_ONLY_TOOLS: dict[str, str] = {
    "browse_products": "shopper-portal",
    "get_prices": "shopper-portal",
    # SA-01 PR 2 (Amendment 3, approved by the user 2026-11-27): the product page's reviews.
    "list_reviews": "shopper-portal",
}
PORTAL_CATALOG_TOOLS = frozenset({"get_cart", "get_product", *PORTAL_ONLY_TOOLS})
CALLER_KEYS = {
    "audience",
    "region",
    "agent",
    "aud",
    "visibility",
    "turn",
    "sid",
    "session_id",
    "shp",  # row #27: the signed-in shopper comes only from the token
    "shopper",
    "shopper_id",
}


def _unreachable() -> Any:
    raise RuntimeError("no database in contract tests")


SERVERS: dict[str, tuple[Callable[[], Any], dict[str, Any]]] = {
    RAG: (
        lambda: RagServer(Backend(_unreachable, FakeEmbedder()), key=KEY, tools=retrieve.TOOLS),
        dict(retrieve.TOOLS),
    ),
    CATALOG: (lambda: catalog_testing.server(key=KEY), dict(CATALOG_TOOLS)),
    ORDERS: (lambda: orders_testing.server(key=KEY), dict(ORDERS_TOOLS)),
    POLICY: (lambda: policy_testing.server(key=KEY), dict(POLICY_TOOLS)),
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
    # Row #11: a server-only tool is listed to its one agent and to nobody else.
    server_only = {name for name, owner in SERVER_ONLY_TOOLS.items() if owner == agent}
    portal_only = {name for name, owner in PORTAL_ONLY_TOOLS.items() if owner == agent}  # SA-01
    assert names <= SHOPPER_TOOLS | server_only | portal_only
    for tool in listed:  # read-only, except exactly the cart writes (row #16) and request_return
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is (tool.name not in WRITE_TOOLS)


@pytest.mark.req("NFR-01")
@pytest.mark.parametrize("server", sorted(SERVERS))
def test_no_scope_grants_a_tool_outside_prd_sevens_read_only_set(server: str) -> None:
    granted = {name for row in AGENT_TOOLS.values() for name in row.get(server, frozenset())}
    assert granted <= SHOPPER_TOOLS | set(SERVER_ONLY_TOOLS) | set(PORTAL_ONLY_TOOLS)
    # Row #11: each server-only tool is granted to its one agent, which is granted nothing else.
    for name, owner in SERVER_ONLY_TOOLS.items():
        holders = {a for a, row in AGENT_TOOLS.items() if name in row.get(server, frozenset())}
        assert holders <= {owner}
        assert AGENT_TOOLS[owner].get(server, frozenset()) <= {name}
    # SA-01 PR 1: each portal-only tool is granted to exactly its owner, on mcp-catalog only, and
    # that owner's catalog row is exactly the portal's set.
    for name, owner in PORTAL_ONLY_TOOLS.items():
        holders = {a for a, row in AGENT_TOOLS.items() if name in row.get(server, frozenset())}
        assert holders == ({owner} if server == CATALOG else set()), name
    if server == CATALOG:
        assert AGENT_TOOLS["shopper-portal"][CATALOG] == PORTAL_CATALOG_TOOLS
    # Row #16: only the assistant may write a cart; the portal's agent only reads it.
    writers = {a for a, row in AGENT_TOOLS.items() if CART_WRITE_TOOLS & row.get(server, set())}
    assert writers <= {"shopper-assistant"}


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
    cart = CART_READ_TOOLS | CART_WRITE_TOOLS
    server_only = {name for name, owner in SERVER_ONLY_TOOLS.items() if owner == agent}
    portal_only = {name for name, owner in PORTAL_ONLY_TOOLS.items() if owner == agent}  # SA-01
    assert names <= FIVE_CATALOG_TOOLS | cart | server_only | portal_only  # only its one agent
    assert not {"set_price", "set_stock"} & names
    for tool in listed:  # read-only, except exactly the cart writes (row #16)
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is (tool.name not in CART_WRITE_TOOLS)
    if agent == "shopper-assistant":
        assert names == FIVE_CATALOG_TOOLS | cart
    if agent == "shopper-portal":
        assert names == PORTAL_CATALOG_TOOLS  # SA-01 PR 1; row #16 had `get_cart` only
    else:
        assert not set(PORTAL_ONLY_TOOLS) & names
    if agent == "shopper-query":
        assert names == {"eligible_products"}
    else:
        assert "eligible_products" not in names


def test_the_catalog_registry_is_exactly_the_five_read_only_tools() -> None:
    cart = CART_READ_TOOLS | CART_WRITE_TOOLS  # row #16
    # Row #11: plus the one server-only tool, for its one agent. SA-01 PR 1: plus the two
    # portal-only reads, for `shopper-portal` only.
    assert set(CATALOG_TOOLS) == (
        FIVE_CATALOG_TOOLS | cart | set(SERVER_ONLY_TOOLS) | set(PORTAL_ONLY_TOOLS)
    )
    assert allowed_tools("shopper-assistant", CATALOG) == FIVE_CATALOG_TOOLS | cart
    assert allowed_tools("shopper-portal", CATALOG) == PORTAL_CATALOG_TOOLS
    assert allowed_tools("catalog-subagent", CATALOG) == frozenset()
    assert allowed_tools("shopper-query", CATALOG) == frozenset(SERVER_ONLY_TOOLS)


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
