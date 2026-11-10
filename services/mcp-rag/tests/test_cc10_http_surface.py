"""What mcp-rag's HTTP surface lets through, beyond the one `/mcp` POST (CC-10 B1, B3, C8; NFR-01,
NFR-02, RAG-10; hard rules 4, 7, 8).

The token gate on every method and path the app serves, a token that expires with its turn's cap,
one turn's token that cannot touch another turn's count, and no query text in a server log.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from northwind_common.rag_tool import LIMIT_MESSAGE, NO_SOURCES, source_ids
from northwind_mcp_rag.retrieve import TOOLS
from northwind_mcp_rag.server import Backend, RagServer
from northwind_mcp_rag.testing import (
    KEY,
    CallerSearcher,
    call_as_dict,
    connect,
    running,
    token,
)
from northwind_rag.retrieval import RetrievedChunk
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("CC-10")

SOURCE = RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")
SECRET = "postgresql://user:hunter2@db/prod"  # noqa: S105 - a planted fake


@dataclass(frozen=True)
class Who:
    region: str | None
    audience: str


class Searches:
    """Finds a source, nothing (`EMPTY` in the query) or fails (`FAIL` in the query)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, query: str, caller: Who) -> Sequence[RetrievedChunk]:
        self.calls.append(query)
        if "FAIL" in query:
            raise RuntimeError(SECRET)
        return [] if "EMPTY" in query else [SOURCE]


def _no_db() -> Any:
    raise RuntimeError(SECRET)


def make(searches: Searches | None = None, clock: Any = None) -> RagServer:
    kwargs: dict[str, Any] = {} if clock is None else {"clock": clock}
    return RagServer(
        Backend(_no_db, FakeEmbedder()),
        key=KEY,
        tools=TOOLS,
        searcher=CallerSearcher(searches or Searches(), Who),
        **kwargs,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---- the gate covers every method and path ----------------------------------------------------

METHODS = ["GET", "POST", "DELETE", "PUT", "PATCH", "OPTIONS", "HEAD"]
PATHS = [
    "/mcp",
    "/mcp/",
    "/",
    "/sse",
    "/messages/",
    "/health",
    "/docs",
    "/.well-known/oauth-protected-resource",
    "/anything/else",
]


@pytest.fixture(scope="module")
def served() -> Iterator[tuple[str, RagServer]]:
    server = make()
    with running(server) as url:
        yield url.removesuffix("/mcp"), server


@pytest.mark.req("NFR-02")
@pytest.mark.parametrize("auth", ["none", "forged", "expired", "not-bearer"])
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("method", METHODS)
def test_every_method_and_path_gets_the_bare_401_without_a_valid_token(
    served: tuple[str, RagServer], method: str, path: str, auth: str
) -> None:
    base, server = served
    headers = {"accept": "application/json, text/event-stream"}
    if auth == "forged":
        headers["authorization"] = f"Bearer {token(key=b'f' * 40)[0]}"
    elif auth == "expired":
        headers["authorization"] = f"Bearer {token(now=0.0)[0]}"
    elif auth == "not-bearer":
        headers["authorization"] = f"Basic {token()[0]}"
    response = httpx.request(method, base + path, headers=headers, timeout=10)
    assert response.status_code == 401
    assert response.content == b""
    assert len(server.ledger) == 0


@pytest.mark.req("NFR-02")
@pytest.mark.parametrize("path", ["/", "/health", "/docs", "/anything/else"])
def test_a_valid_token_gets_nothing_from_a_path_other_than_mcp(
    served: tuple[str, RagServer], path: str
) -> None:
    base, _ = served
    response = httpx.get(base + path, headers={"authorization": f"Bearer {token()[0]}"}, timeout=10)
    assert response.status_code == 404
    assert SOURCE.text not in response.text


@pytest.mark.req("NFR-02")
@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_a_valid_token_without_a_session_cannot_use_get_or_delete(
    served: tuple[str, RagServer], method: str
) -> None:
    base, server = served
    headers = {
        "authorization": f"Bearer {token()[0]}",
        "accept": "application/json, text/event-stream",
    }
    response = httpx.request(method, base + "/mcp", headers=headers, timeout=10)
    assert 400 <= response.status_code < 500
    assert len(server.ledger) == 0


# ---- the cap and the token's life -------------------------------------------------------------


def test_a_token_dies_at_its_expiry_and_the_cap_does_not_reset() -> None:
    now = [1000.0]
    searches = Searches()
    server = make(searches, clock=lambda: now[0])
    tok, claims = token(now=1000.0)
    ask = {"query": "q"}
    with running(server) as url:
        results = [run(call_as_dict(url, tok, ask)) for _ in range(3)]
        assert all("is_error" not in r for r in results)
        now[0] = claims.exp - 1  # the last second of the token: still the same three searches
        refused = run(call_as_dict(url, tok, ask))
        assert refused["is_error"] is True and LIMIT_MESSAGE in refused["content"][0]["text"]
        now[0] = claims.exp  # expired: the gate refuses before any tool
        with pytest.raises(BaseException):  # noqa: B017, PT011 - the client's 401 surfaces as a group
            run(call_as_dict(url, tok, ask))
        # A new token is a new turn with its own cap, and the old entry is evicted.
        fresh, fresh_claims = token(now=now[0])
        assert "is_error" not in run(call_as_dict(url, fresh, ask))
    assert len(searches.calls) == 4
    assert server.ledger.used(claims.turn) == 0
    assert server.ledger.used(fresh_claims.turn) == 1


def test_one_turns_token_cannot_spend_or_read_another_turns_searches() -> None:
    server = make()
    a, claims_a = token()
    b, claims_b = token()
    assert claims_a.turn != claims_b.turn
    with running(server) as url:
        for _ in range(3):
            run(call_as_dict(url, a, {"query": "q"}))
        assert (server.ledger.used(claims_a.turn), server.ledger.used(claims_b.turn)) == (3, 0)
        refused = run(call_as_dict(url, a, {"query": "q"}))
        assert refused["is_error"] is True
        # Another turn is unaffected by A's exhaustion, and counted on its own.
        assert "is_error" not in run(call_as_dict(url, b, {"query": "q"}))
    assert (server.ledger.used(claims_a.turn), server.ledger.used(claims_b.turn)) == (3, 1)
    # What the refusal tells the caller holds no turn id and no count.
    shown = json.dumps(refused)
    assert claims_a.turn not in shown and claims_b.turn not in shown
    assert not any(ch.isdigit() for ch in refused["content"][0]["text"])


def _swap_turn(victim: str, donor: str) -> str:
    """The victim's token with the donor's turn id in its claims and the victim's signature."""
    head, payload, signature = victim.split(".")
    padded = payload + "=" * (-len(payload) % 4)
    body = json.loads(base64.urlsafe_b64decode(padded))
    body["turn"] = donor
    new = base64.urlsafe_b64encode(json.dumps(body, sort_keys=True).encode()).rstrip(b"=")
    return f"{head}.{new.decode()}.{signature}"


def test_a_token_with_another_turns_id_swapped_in_is_refused() -> None:
    server = make()
    mine, _ = token()
    _, other = token()
    swapped = _swap_turn(mine, other.turn)
    init = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {
        "authorization": f"Bearer {swapped}",
        "accept": "application/json, text/event-stream",
    }
    with running(server) as url:
        response = httpx.post(url, json=init, headers=headers, timeout=10)
    assert (response.status_code, response.content) == (401, b"")
    assert server.ledger.used(other.turn) == 0


# ---- the assistant's token and the resource ---------------------------------------------------


@pytest.mark.req("NFR-01")
def test_the_assistant_gets_no_resource_template_and_no_manifest_by_any_listing() -> None:
    async def go(url: str) -> tuple[list[str], list[Any]]:
        async with connect(url, token()[0]) as client:
            resources = [str(r.uri) for r in (await client.list_resources()).resources]
            try:
                templates = list((await client.list_resource_templates()).resource_templates)
            except Exception:  # noqa: BLE001 - "not supported" is as good as an empty list
                templates = []
            return resources, templates

    with running(make()) as url:
        resources, templates = run(go(url))
    assert resources == [] and templates == []


# ---- logs -------------------------------------------------------------------------------------


@pytest.mark.req("NFR-04")
def test_no_query_text_reaches_a_server_log_on_any_path(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    card = "4242" * 4  # synthetic
    queries = [
        "QMARK-ok-7d2e",
        "QMARK-EMPTY-7d2e",
        "QMARK-FAIL-7d2e",
        f"QMARK-card {card}",
        "QMARK-nul\x00-7d2e",
    ]
    tok, _ = token()
    with running(make()) as url:
        for query in queries:
            run(call_as_dict(url, tok, {"query": query}))
        run(call_as_dict(url, tok, {"query": "QMARK-over-the-cap-7d2e"}))  # the fourth call
    client_side = ("mcp.client", "httpx", "httpcore")
    server_text = "\n".join(
        r.getMessage() for r in caplog.records if not r.name.startswith(client_side)
    )
    assert "tool failed" in server_text or "search failed" in server_text  # not vacuous
    for needle in ("QMARK", card, "4242", tok, SECRET, "hunter2"):
        assert needle not in server_text


def test_the_gateway_style_first_search_and_a_refinement_share_one_empty_answer() -> None:
    """Sanity for the helpers: an empty search is the fixed line and names no source."""
    tok, _ = token()
    with running(make()) as url:
        result = run(call_as_dict(url, tok, {"query": "EMPTY"}))
    text = result["content"][0]["text"]
    assert text == NO_SOURCES and source_ids(text) == [] and "is_error" not in result
