"""mcp-rag's token gate, scoping, errors and transport (CC-10 B1; NFR-01, NFR-02, hard rule 10).

A real server over HTTP. The one tool here is a spy registered as `retrieve_context`, so these
tests hold before the real handler lands (B2, after row #9).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx
import mcp.types as t
import pytest
from mcp.shared.exceptions import MCPError

from northwind_common import mcp_token
from northwind_common.errors import ENVELOPE_KEYS, ToolError
from northwind_common.mcp_token import Claims
from northwind_mcp_rag.server import (
    MANIFEST_URI,
    Backend,
    RagServer,
    ToolSpec,
    text_result,
)
from northwind_mcp_rag.testing import KEY, connect, running, token
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("CC-10")

SECRET = "postgresql://user:hunter2@db/prod MARK-RAW-ERROR"  # noqa: S105 - a planted fake
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}


class Spy:
    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[tuple[Claims, Mapping[str, Any]]] = []
        self.raises = raises

    async def __call__(
        self, server: RagServer, claims: Claims, args: Mapping[str, Any]
    ) -> t.CallToolResult:
        self.calls.append((claims, dict(args)))
        if self.raises is not None:
            raise self.raises
        return text_result("spy ok")


def no_db() -> Any:
    raise RuntimeError(SECRET)


def make(spy: Spy | None = None) -> RagServer:
    tools = {}
    if spy is not None:
        tool = t.Tool(
            name="retrieve_context",
            description="spy",
            input_schema={"type": "object", "additionalProperties": False},
        )
        tools["retrieve_context"] = ToolSpec(tool=tool, handler=spy)
    return RagServer(Backend(no_db, FakeEmbedder()), key=KEY, tools=tools)


def post(url: str, headers: dict[str, str]) -> httpx.Response:
    base = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    return httpx.post(url, json=INIT, headers={**base, **headers}, timeout=10)


def forged(body: dict[str, Any], key: bytes = KEY) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=").decode()
    return f"v1.{payload}.{mcp_token._sign(key, payload)}"


def bad_tokens() -> dict[str, dict[str, str]]:
    valid, claims = token()
    body = {
        "aud": "staff",  # wrong audience for the assistant
        "region": "UK",
        "agent": "shopper-assistant",
        "turn": claims.turn,
        "exp": claims.exp,
    }
    return {
        "missing": {},
        "empty": {"authorization": ""},
        "not-bearer": {"authorization": f"Basic {valid}"},
        "forged": {"authorization": f"Bearer {token(key=b'x' * 40)[0]}"},
        "expired": {"authorization": f"Bearer {token(now=0.0)[0]}"},
        "wrong-audience": {"authorization": f"Bearer {forged(body)}"},
        "malformed": {"authorization": "Bearer v1.!!.??"},
    }


@pytest.mark.req("NFR-02")
@pytest.mark.parametrize("case", sorted(bad_tokens()))
def test_a_bad_token_gets_a_bare_401_before_any_tool_runs(case: str) -> None:
    spy = Spy()
    server = make(spy)
    with running(server) as url:
        response = post(url, bad_tokens()[case])
    assert response.status_code == 401
    assert response.content == b""
    assert response.headers["www-authenticate"] == "Bearer"
    assert spy.calls == [] and len(server.ledger) == 0


def test_a_valid_token_opens_the_session() -> None:
    with running(make()) as url:
        response = post(url, {"authorization": f"Bearer {token()[0]}"})
    assert response.status_code == 200


def test_a_foreign_host_header_is_refused_even_with_a_valid_token() -> None:
    """DNS-rebinding protection (the SDK's, on 127.0.0.1)."""
    with running(make()) as url:
        response = post(url, {"authorization": f"Bearer {token()[0]}", "host": "evil.example"})
    assert response.status_code in (400, 403, 421)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def rpc_error(coro: Any) -> MCPError:
    """The MCPError a refused request raises; the client wraps it in its task group."""
    try:
        run(coro)
    except BaseException as error:  # noqa: BLE001
        pending: list[BaseException] = [error]
        while pending:
            item = pending.pop()
            if isinstance(item, MCPError):
                return item
            if isinstance(item, BaseExceptionGroup):
                pending.extend(item.exceptions)
        raise
    raise AssertionError("the request was not refused")


@pytest.mark.req("NFR-01")
def test_the_assistant_lists_only_its_tool_and_no_resource() -> None:
    async def go(url: str) -> tuple[list[str], list[str], str | None]:
        async with connect(url, token()[0]) as client:
            tools = [tool.name for tool in (await client.list_tools()).tools]
            resources = [str(r.uri) for r in (await client.list_resources()).resources]
            return tools, resources, client.instructions

    with running(make(Spy())) as url:
        tools, resources, instructions = run(go(url))
    assert tools == ["retrieve_context"]
    assert resources == []
    assert instructions is None  # S0 item 3: nothing for the CLI to put in `system`


@pytest.mark.req("NFR-01")
def test_a_staff_agent_is_offered_no_tool_but_the_manifest() -> None:
    async def go(url: str) -> tuple[list[str], list[str]]:
        async with connect(url, token("catalog-subagent", None)[0]) as client:
            tools = [tool.name for tool in (await client.list_tools()).tools]
            resources = [str(r.uri) for r in (await client.list_resources()).resources]
            return tools, resources

    with running(make(Spy())) as url:
        assert run(go(url)) == ([], [MANIFEST_URI])


def envelope(result: t.CallToolResult) -> dict[str, Any]:
    assert result.is_error is True
    [block] = result.content
    assert isinstance(block, t.TextContent)
    body: dict[str, Any] = json.loads(block.text)
    assert set(body) == ENVELOPE_KEYS
    assert result.structured_content == body
    return body


@pytest.mark.req("NFR-01")
@pytest.mark.parametrize(
    ("agent", "region", "name"),
    [
        ("catalog-subagent", None, "retrieve_context"),  # exists, not this agent's
        ("catalog-subagent", None, "no_such_tool"),
        ("shopper-assistant", "UK", "refund_order"),
        ("shopper-assistant", "UK", "capture_payment"),
        ("shopper-assistant", "UK", "assign_case"),
    ],
)
def test_a_tool_out_of_scope_is_refused_the_same_way_whether_or_not_it_exists(
    agent: str, region: str | None, name: str
) -> None:
    spy = Spy()

    async def go(url: str) -> t.CallToolResult:
        async with connect(url, token(agent, region)[0]) as client:
            return await client.call_tool(name, {"query": "x"})

    with running(make(spy)) as url:
        result = run(go(url))
    assert envelope(result) == {
        "isError": True,
        "errorCategory": "permission",
        "isRetryable": False,
        "message": "That tool is not available to this caller.",
        "code": "not_allowed",
    }
    assert spy.calls == []


def test_the_handler_gets_the_caller_from_the_token_never_from_arguments() -> None:
    spy = Spy()

    async def go(url: str) -> t.CallToolResult:
        async with connect(url, token("shopper-assistant", "EU")[0]) as client:
            args = {"query": "x", "audience": "staff", "region": "UK", "agent": "catalog-subagent"}
            return await client.call_tool("retrieve_context", args)

    with running(make(spy)) as url:
        result = run(go(url))
    assert result.is_error is False
    [(claims, _)] = spy.calls
    assert (claims.aud, claims.region, claims.agent) == ("shopper", "EU", "shopper-assistant")


def test_a_handler_crash_is_a_fixed_transient_error_with_nothing_of_the_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def go(url: str) -> t.CallToolResult:
        async with connect(url, token()[0]) as client:
            return await client.call_tool("retrieve_context", {"query": "x"})

    caplog.set_level(logging.DEBUG, logger="northwind_mcp_rag")
    with running(make(Spy(raises=RuntimeError(SECRET)))) as url:
        result = run(go(url))
    assert envelope(result) == {
        "isError": True,
        "errorCategory": "transient",
        "isRetryable": True,
        "message": "The tool failed. Try again later.",
        "code": "tool_failed",
    }
    assert "hunter2" not in result.model_dump_json() and "MARK-RAW" not in result.model_dump_json()
    assert "hunter2" not in caplog.text and "RuntimeError" in caplog.text


@pytest.mark.parametrize(
    "error",
    [
        ToolError("validation", False, "bad_query", "The query must be non-empty text."),
        ToolError("business", False, "search_limit", "The search limit is reached."),
        ToolError("permission", False, "not_allowed", "Not allowed."),
        ToolError("transient", True, "search_failed", "Search is unavailable right now."),
    ],
)
def test_every_category_arrives_as_the_envelope_with_is_error(error: ToolError) -> None:
    async def go(url: str) -> t.CallToolResult:
        async with connect(url, token()[0]) as client:
            return await client.call_tool("retrieve_context", {"query": "x"})

    with running(make(Spy(raises=error))) as url:
        assert envelope(run(go(url))) == error.to_envelope()


def test_a_success_is_text_only() -> None:
    """S0 item 5: with `structuredContent` the CLI would show the model that, not the text."""

    async def go(url: str) -> t.CallToolResult:
        async with connect(url, token()[0]) as client:
            return await client.call_tool("retrieve_context", {"query": "x"})

    with running(make(Spy())) as url:
        result = run(go(url))
    assert result.is_error is False and result.structured_content is None
    assert [b.text for b in result.content if isinstance(b, t.TextContent)] == ["spy ok"]


@pytest.mark.req("NFR-01")
def test_the_assistant_cannot_read_the_manifest() -> None:
    async def go(url: str) -> Any:
        async with connect(url, token()[0]) as client:
            return await client.read_resource(MANIFEST_URI)

    with running(make()) as url:
        error = rpc_error(go(url))
    assert error.data["errorCategory"] == "permission"
    assert error.data["code"] == "not_allowed"


def test_an_unknown_resource_is_refused_like_a_forbidden_one() -> None:
    async def go(url: str, uri: str) -> Any:
        async with connect(url, token("catalog-subagent", None)[0]) as client:
            return await client.read_resource(uri)

    with running(make()) as url:
        error = rpc_error(go(url, "northwind-rag://index/chunks"))
    assert error.data == {
        "isError": True,
        "errorCategory": "permission",
        "isRetryable": False,
        "message": "That resource is not available to this caller.",
        "code": "not_allowed",
    }


def test_a_manifest_read_with_the_database_down_is_transient_and_says_nothing_of_why(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def go(url: str) -> Any:
        async with connect(url, token("catalog-subagent", None)[0]) as client:
            return await client.read_resource(MANIFEST_URI)

    caplog.set_level(logging.DEBUG, logger="northwind_mcp_rag")
    with running(make()) as url:
        error = rpc_error(go(url))
    assert error.data["code"] == "index_unavailable"
    assert error.data["isRetryable"] is True
    assert "hunter2" not in str(error) and "hunter2" not in json.dumps(error.data)
    assert "hunter2" not in caplog.text


@pytest.mark.req("NFR-02")
def test_no_token_or_key_reaches_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    valid = token()[0]

    async def go(url: str) -> None:
        async with connect(url, valid) as client:
            await client.call_tool("retrieve_context", {"query": "SECRET-QUERY-TEXT"})

    caplog.set_level(logging.DEBUG)
    with running(make(Spy())) as url:
        run(go(url))
        post(url, {"authorization": "Bearer v1.forged.token"})
    # The server's side only: this test's own MCP client logs its requests at DEBUG (the gateway's
    # client is pinned above DEBUG in phase 2).
    client_side = ("mcp.client", "httpx", "httpcore")
    server_text = "\n".join(
        r.getMessage() for r in caplog.records if not r.name.startswith(client_side)
    )
    assert "request refused: token" in server_text  # the server did log, so this is not vacuous
    for needle in (valid, valid.split(".")[2], KEY.decode(), "SECRET-QUERY-TEXT", "v1.forged"):
        assert needle not in server_text
