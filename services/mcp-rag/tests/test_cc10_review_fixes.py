"""The security review's fixes on row #10 (2026-11-10): L2, L3, L5 in part, L9, and its unconfirmed
items 1 and 3. Each test fails on the code before the fix."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from northwind_common.rag_tool import MAX_QUERY_CHARS
from northwind_mcp_rag.ledger import SearchLedger
from northwind_mcp_rag.server import token_gate
from northwind_mcp_rag.testing import KEY, call_as_dict, serve_retriever, token
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("CC-10")

REPO = Path(__file__).resolve().parents[3]
CARD = "4242" * 4  # synthetic
SOURCE = RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")


class Who:
    def __init__(self, region: str | None, audience: str) -> None:
        self.region, self.audience = region, audience


class Recording:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, caller: Who) -> Sequence[RetrievedChunk]:
        self.queries.append(query)
        return [SOURCE]


# ---- L2 --------------------------------------------------------------------------------------


@pytest.mark.req("RAG-10")
def test_an_exhausted_turn_gets_nothing_from_a_request_that_lands_at_expiry() -> None:
    now = [0.0]
    ledger = SearchLedger(clock=lambda: now[0])

    async def go() -> list[bool]:
        used = [await ledger.take("t", 10) for _ in range(3)]
        now[0] = 10.0  # the gate passed it at 9.999; the take runs at expiry
        return [*used, await ledger.take("t", 10), await ledger.take("t", 10)]

    assert asyncio.run(go()) == [True, True, True, False, False]


@pytest.mark.req("RAG-10")
def test_an_expired_token_never_opens_a_new_count() -> None:
    ledger = SearchLedger(clock=lambda: 50.0)

    async def go() -> bool:
        return await ledger.take("fresh-turn", 50)

    assert asyncio.run(go()) is False and len(ledger) == 0


# ---- L3 --------------------------------------------------------------------------------------


@pytest.mark.req("NFR-02")
def test_a_websocket_is_closed_by_the_gate_and_reaches_nothing() -> None:
    reached: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["type"])

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    gate: Any = token_gate(inner, KEY, lambda: 0.0)
    asyncio.run(gate({"type": "websocket", "headers": [], "path": "/mcp"}, receive, send))
    asyncio.run(gate({"type": "something-else"}, receive, send))
    asyncio.run(gate({"type": "lifespan"}, receive, send))
    assert reached == ["lifespan"]
    assert sent == [{"type": "websocket.close", "code": 1008}]


# ---- L5 (part) ---------------------------------------------------------------------------------


@pytest.mark.req("NFR-04")
@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "jacket", "note": CARD},
        {"query": "jacket", "nested": {"card": "4242 4242 4242 4242"}},
        {"query": "jacket", "list": ["a", CARD]},
        {"query": "jacket", "cvv": "cvv 737"},
    ],
)
def test_card_data_in_any_argument_is_refused_and_searches_nothing(
    arguments: dict[str, Any],
) -> None:
    retriever = Recording()
    with serve_retriever(retriever, Who) as (url, _):
        result = asyncio.run(call_as_dict(url, token()[0], arguments))
    body = json.loads(result["content"][0]["text"])
    assert result["is_error"] is True and body["code"] == "card_data"
    assert retriever.queries == [] and "4242" not in json.dumps(result)


def test_an_extra_argument_without_card_data_is_still_ignored() -> None:
    """The approved B2 behaviour, unchanged: refusing extra keys is the user's call."""
    retriever = Recording()
    with serve_retriever(retriever, Who) as (url, _):
        result = asyncio.run(call_as_dict(url, token()[0], {"query": "jacket", "note": "hi"}))
    assert "is_error" not in result and retriever.queries == ["jacket"]


def test_an_over_long_query_is_refused_and_searches_nothing() -> None:
    retriever = Recording()
    with serve_retriever(retriever, Who) as (url, _):
        ok = asyncio.run(call_as_dict(url, token()[0], {"query": "a" * MAX_QUERY_CHARS}))
        long = asyncio.run(call_as_dict(url, token()[0], {"query": "a" * (MAX_QUERY_CHARS + 1)}))
    assert "is_error" not in ok
    assert (
        long["is_error"] is True and json.loads(long["content"][0]["text"])["code"] == "bad_query"
    )
    assert retriever.queries == ["a" * MAX_QUERY_CHARS]


def test_the_query_cap_matches_the_gateways_message_limit() -> None:
    from northwind_gateway.chat import MAX_MESSAGE_CHARS

    assert MAX_QUERY_CHARS == MAX_MESSAGE_CHARS


# ---- L9 --------------------------------------------------------------------------------------


@pytest.mark.req("NFR-02")
def test_make_dev_gives_the_key_to_mcp_rag_and_the_gateway_but_not_the_portal() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\ndev-portal:\n", 1)[1].split("\n\n", 1)[0]
    assert recipe.strip().startswith("env -u NORTHWIND_MCP_TOKEN_KEY ")


# ---- re-review M1: the card check reads raw text, not a JSON encoding ---------------------------

SPLIT = ["\n", "\t", "\r", "\x01", "\x0c", " ", "-", "."]


@pytest.mark.req("NFR-04")
@pytest.mark.parametrize("sep", SPLIT, ids=[repr(s) for s in SPLIT])
@pytest.mark.parametrize("where", ["query", "extra"])
def test_a_card_split_by_any_separator_is_refused_in_any_argument(sep: str, where: str) -> None:
    card = sep.join(["4242"] * 4)
    arguments = {"query": card} if where == "query" else {"query": "jacket", "note": card}
    retriever = Recording()
    with serve_retriever(retriever, Who) as (url, _):
        result = asyncio.run(call_as_dict(url, token()[0], arguments))
    assert result.get("is_error") is True
    assert json.loads(result["content"][0]["text"])["code"] == "card_data"
    assert retriever.queries == []


@pytest.mark.req("NFR-04")
def test_a_card_split_across_two_arguments_is_refused() -> None:
    from northwind_mcp_rag.retrieve import holds_card_data

    assert holds_card_data({"query": "4242 4242", "note": "4242 4242"})
    assert not holds_card_data({"query": "jacket size 42", "note": "two pairs"})


CORPUS = [
    *[sep.join(["4242"] * 4) for sep in SPLIT],
    "4242424242424242",
    "cvv 737",
    "my CVC is 123",
    "４２４２４２４２４２４２４２４２",  # fullwidth digits
    "jacket size 42",
    "order 12345",
    "call 0800 123 4567",
    "4242 4242 4242 4241",  # Luhn fails
    "",
]


@pytest.mark.req("NFR-04")
@pytest.mark.parametrize("text", CORPUS, ids=range(len(CORPUS)))
def test_the_argument_check_agrees_with_the_raw_card_check(text: str) -> None:
    from northwind_common.cards import contains_card_data
    from northwind_mcp_rag.retrieve import holds_card_data

    assert holds_card_data({"query": text}) == contains_card_data(text)
    assert holds_card_data({"query": "jacket", "note": text}) == contains_card_data(text)
