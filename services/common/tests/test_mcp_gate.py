"""The shared token gate (CC-10, row #10b; NFR-02, L4). It replaces the Starlette gate that lived in
mcp-rag, so its header lookup is measured against Starlette's `Headers(scope=scope).get` here."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from starlette.datastructures import Headers

from northwind_common import mcp_gate
from northwind_common.mcp_gate import CLAIMS_KEY, token_gate
from northwind_common.mcp_token import CATALOG_SERVER, RAG_SERVER, Claims, mint
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.testing import running
from northwind_mcp_catalog.tools import TOOLS

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("NFR-02")]

KEY = b"g" * 40
NOW = 1_800_000_000.0


def minted(server: str, *, key: bytes = KEY) -> str:
    return mint(
        audience="shopper",
        region="UK",
        agent="shopper-assistant",
        key=key,
        now=NOW,
        server=server,
    )[0]


class Run:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.seen: list[dict[str, Any]] = []


def drive(server: str, scope: dict[str, Any]) -> Run:
    run = Run()

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        run.seen.append(dict(scope))

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(message: Any) -> None:
        run.sent.append(dict(message))

    gate = token_gate(inner, key=KEY, clock=lambda: NOW, server=server)

    async def go() -> None:
        await gate(scope, receive, send)

    asyncio.run(go())
    return run


def http_scope(headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return {"type": "http", "method": "POST", "path": "/mcp", "headers": headers}


def assert_bare_401(run: Run) -> None:
    assert run.seen == []
    assert run.sent == [
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"www-authenticate", b"Bearer"), (b"content-length", b"0")],
        },
        {"type": "http.response.body", "body": b""},
    ]


def expected_pass(value: str, server: str) -> bool:
    scheme, _, token = value.partition(" ")
    if scheme != "Bearer":
        return False
    from northwind_common.mcp_token import TokenError, verify_for

    try:
        verify_for(token, key=KEY, server=server, now=NOW)
    except TokenError:
        return False
    return True


GOOD = f"Bearer {minted(CATALOG_SERVER)}"
BAD = "Bearer not-a-token"


@pytest.mark.parametrize(
    ("label", "headers"),
    [
        ("missing", []),
        ("only other headers", [(b"accept", b"*/*")]),
        ("single", [(b"authorization", GOOD.encode())]),
        (
            "duplicate good then bad",
            [(b"authorization", GOOD.encode()), (b"authorization", BAD.encode())],
        ),
        (
            "duplicate bad then good",
            [(b"authorization", BAD.encode()), (b"authorization", GOOD.encode())],
        ),
        ("mixed case", [(b"Authorization", GOOD.encode())]),
        ("upper case", [(b"AUTHORIZATION", GOOD.encode())]),
        (
            "mixed case then lower",
            [(b"Authorization", BAD.encode()), (b"authorization", GOOD.encode())],
        ),
        ("latin-1 value", [(b"authorization", "Bearer caf\xe9".encode("latin-1"))]),
        ("empty value", [(b"authorization", b"")]),
        ("wrong scheme", [(b"authorization", GOOD.replace("Bearer", "Basic").encode())]),
        ("lower-case scheme", [(b"authorization", GOOD.replace("Bearer", "bearer").encode())]),
    ],
)
def test_the_header_lookup_matches_starlette_and_so_does_the_outcome(
    label: str, headers: list[tuple[bytes, bytes]]
) -> None:
    scope = http_scope(headers)
    assert mcp_gate._authorization(scope) == Headers(scope=scope).get("authorization", ""), label
    value = Headers(scope=scope).get("authorization", "")
    run = drive(CATALOG_SERVER, scope)
    if expected_pass(value, CATALOG_SERVER):
        assert len(run.seen) == 1 and run.sent == []
        assert isinstance(run.seen[0]["state"][CLAIMS_KEY], Claims)
    else:
        assert_bare_401(run)


def test_the_first_authorization_header_decides() -> None:
    both = http_scope([(b"authorization", GOOD.encode()), (b"authorization", BAD.encode())])
    assert len(drive(CATALOG_SERVER, both).seen) == 1
    reverse = http_scope([(b"authorization", BAD.encode()), (b"authorization", GOOD.encode())])
    assert_bare_401(drive(CATALOG_SERVER, reverse))


def test_a_scope_without_headers_is_refused() -> None:
    assert_bare_401(drive(CATALOG_SERVER, {"type": "http", "method": "POST", "path": "/mcp"}))


def test_the_claims_ride_in_scope_state_and_the_rest_of_the_scope_is_kept() -> None:
    scope = {**http_scope([(b"authorization", GOOD.encode())]), "state": {"keep": 1}}
    run = drive(CATALOG_SERVER, scope)
    [seen] = run.seen
    claims = seen["state"][CLAIMS_KEY]
    assert (claims.srv, claims.aud, claims.region) == (CATALOG_SERVER, "shopper", "UK")
    assert seen["state"]["keep"] == 1 and seen["path"] == "/mcp"


@pytest.mark.parametrize(
    ("minted_for", "gate_for"), [(RAG_SERVER, CATALOG_SERVER), (CATALOG_SERVER, RAG_SERVER)]
)
def test_a_token_for_the_other_server_is_a_bare_401(minted_for: str, gate_for: str) -> None:
    scope = http_scope([(b"authorization", f"Bearer {minted(minted_for)}".encode())])
    assert_bare_401(drive(gate_for, scope))
    assert len(drive(minted_for, scope).seen) == 1


def test_a_forged_or_expired_token_is_a_bare_401() -> None:
    forged = f"Bearer {minted(CATALOG_SERVER, key=b'f' * 40)}"
    assert_bare_401(drive(CATALOG_SERVER, http_scope([(b"authorization", forged.encode())])))
    old = mint(
        audience="shopper",
        region="UK",
        agent="shopper-assistant",
        key=KEY,
        now=0.0,
        server=CATALOG_SERVER,
    )[0]
    assert_bare_401(
        drive(CATALOG_SERVER, http_scope([(b"authorization", f"Bearer {old}".encode())]))
    )


def test_lifespan_passes_through_ungated() -> None:
    run = drive(CATALOG_SERVER, {"type": "lifespan"})
    assert [s["type"] for s in run.seen] == ["lifespan"] and run.sent == []


def test_a_websocket_is_closed_with_1008_and_never_reaches_the_app() -> None:
    run = drive(
        CATALOG_SERVER,
        {"type": "websocket", "path": "/ws", "headers": [(b"authorization", GOOD.encode())]},
    )
    assert run.seen == []
    assert run.sent == [{"type": "websocket.close", "code": 1008}]


def test_any_other_scope_type_is_dropped() -> None:
    run = drive(CATALOG_SERVER, {"type": "something-else", "headers": []})
    assert (run.seen, run.sent) == ([], [])


# ---- over real HTTP: uvicorn lower-cases header names --------------------------------------------


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    server = CatalogServer(FixtureCatalog(), key=KEY, tools=TOOLS)
    with running(server) as live_url:
        yield live_url


def initialize(url: str, headers: dict[str, str]) -> httpx.Response:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    return httpx.post(
        url,
        json=body,
        headers={"accept": "application/json, text/event-stream", **headers},
        timeout=10,
    )


def live_token(server: str) -> str:
    return mint(audience="shopper", region="UK", agent="shopper-assistant", key=KEY, server=server)[
        0
    ]


@pytest.mark.parametrize("name", ["authorization", "Authorization", "AUTHORIZATION"])
def test_a_mixed_case_header_passes_over_real_http(url: str, name: str) -> None:
    response = initialize(url, {name: f"Bearer {live_token(CATALOG_SERVER)}"})
    assert response.status_code == 200


def test_a_wrong_server_token_is_a_bare_401_over_real_http(url: str) -> None:
    response = initialize(url, {"Authorization": f"Bearer {live_token(RAG_SERVER)}"})
    assert (response.status_code, response.content) == (401, b"")
    assert response.headers["www-authenticate"] == "Bearer"


def test_no_token_is_a_bare_401_over_real_http(url: str) -> None:
    response = initialize(url, {})
    assert (response.status_code, response.content) == (401, b"")
