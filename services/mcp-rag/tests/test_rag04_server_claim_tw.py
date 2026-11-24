"""RAG-04 (row #11, decision 0022 section 7), hard rules 6, 7 and 8: mcp-rag reads a query plan only
from the token's `qf` claim, never from tool arguments, and a plan cannot widen a search.

A real mcp-rag over HTTP with a spy behind it (`serve_retriever`, no database). The spy records each
search, whether plain or planned, and the caller (audience and region) the server gave it. From the
decision: the model's `retrieve_context` schema is unchanged, audience and region stay token-bound,
a token for another server or agent can neither carry nor use a plan, and a planned search counts
against RAG-10's cap like any other.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from northwind_common.mcp_token import (
    CATALOG_SERVER,
    RAG_SERVER,
    Claims,
    TokenError,
    _b64,
    _sign,
    mint,
    product_bitmap,
    verify_for,
)
from northwind_common.rag_tool import MAX_SEARCHES, SEARCH_FAILED, SEARCH_LIMIT, TOOL_NAME
from northwind_mcp_rag.server import Backend, RagServer, plan_parts
from northwind_mcp_rag.testing import KEY, call_as_dict, connect, serve_retriever
from northwind_rag.retrieval import RetrievedChunk, SearchFilter
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("RAG-04")

SOURCE = RetrievedChunk("c1", "product", "P0011", None, "A fleece.")
PLAN = {
    "parts": [
        {"q": "black fleece", "c": ["Black"], "s": ["M"]},
        {"p": product_bitmap(["P0009", "P0011", "P0012"])},
    ]
}
# Everything a hostile model could put in the arguments of the one tool it has.
HOSTILE_ARGS: dict[str, Any] = {
    "query": "pants",
    "colours": ["White"],
    "sizes": ["XXL"],
    "products": ["P0009"],
    "filters": {"visibility": "internal", "colour": ["Red"]},
    "audience": "staff",
    "region": "EU",
    "visibility": "internal",
    "qf": {"parts": [{"c": ["Red"]}]},
    "plan": {"parts": [{"c": ["Red"]}]},
}


@dataclass(frozen=True)
class Who:
    region: str | None
    audience: str


class Spy:
    """Records plain and planned searches with the caller the server built."""

    def __init__(self) -> None:
        self.plain: list[tuple[str, Who]] = []
        self.planned: list[tuple[list[tuple[str, SearchFilter]], Who]] = []

    def search(self, query: str, caller: Who) -> Sequence[RetrievedChunk]:
        self.plain.append((query, caller))
        return [SOURCE]

    def search_planned(self, parts: Any, caller: Who) -> Sequence[RetrievedChunk]:
        self.planned.append((list(parts), caller))
        return [SOURCE]


def planned_token(
    plan: dict[str, Any] | None = PLAN,
    *,
    agent: str = "shopper-assistant",
    region: str | None = "UK",
    turn: str | None = None,
) -> str:
    audience: Any = "staff" if agent == "catalog-subagent" else "shopper"
    token, _ = mint(
        audience=audience,
        region=region,
        agent=agent,
        key=KEY,
        server=RAG_SERVER,
        plan=plan,
        turn=turn,
    )
    return token


def search(retriever: Any, token: str, args: dict[str, Any]) -> dict[str, Any]:
    with serve_retriever(retriever, Who) as (url, _):
        return asyncio.run(call_as_dict(url, token, args))


def text(result: dict[str, Any]) -> str:
    return "".join(block["text"] for block in result["content"])


# ---- reading the claim --------------------------------------------------------------------------


def claims_with(plan: dict[str, Any] | None) -> Claims:
    return verify_for(planned_token(plan), key=KEY, server=RAG_SERVER)


def test_a_token_without_a_plan_has_no_parts() -> None:
    assert plan_parts("pants", claims_with(None)) is None


def test_the_parts_are_the_claims_and_a_part_without_text_searches_the_query() -> None:
    parts = plan_parts("the raw message", claims_with(PLAN))
    assert parts == [
        ("black fleece", SearchFilter(colours=("Black",), sizes=("M",), products=None)),
        (
            "the raw message",
            SearchFilter(products=frozenset({"P0009", "P0011", "P0012"})),
        ),
    ]


def test_a_plan_can_only_produce_the_three_narrowing_fields() -> None:
    for _, narrow in plan_parts("q", claims_with(PLAN)) or []:
        assert set(narrow.__dataclass_fields__) == {"colours", "sizes", "products"}


# ---- arguments never carry a plan or a filter ---------------------------------------------------


def test_hostile_arguments_on_a_plain_token_change_nothing() -> None:
    spy = Spy()
    from northwind_mcp_rag.testing import token

    result = search(spy, token("shopper-assistant", "UK")[0], HOSTILE_ARGS)
    assert "is_error" not in result
    assert spy.planned == []
    assert spy.plain == [("pants", Who("UK", "shopper"))]  # the query, as the token's caller


def test_hostile_arguments_on_a_planned_token_do_not_replace_the_claims_filters() -> None:
    spy = Spy()
    result = search(spy, planned_token(), HOSTILE_ARGS)
    assert "is_error" not in result
    assert spy.plain == []
    [(parts, caller)] = spy.planned
    assert caller == Who("UK", "shopper")  # not staff, not EU, whatever the arguments say
    assert parts[0] == ("black fleece", SearchFilter(colours=("Black",), sizes=("M",)))
    assert parts[1][0] == "pants"  # only the query argument is read, for a part with no text
    assert parts[1][1] == SearchFilter(products=frozenset({"P0009", "P0011", "P0012"}))
    flat = repr(parts)
    for word in ("White", "XXL", "Red", "internal"):
        assert word not in flat


def test_the_models_tool_schema_takes_a_query_and_nothing_else() -> None:
    async def schema(token: str) -> dict[str, Any]:
        with serve_retriever(Spy(), Who) as (url, _):
            async with connect(url, token) as client:
                [tool] = [t for t in (await client.list_tools()).tools if t.name == TOOL_NAME]
        return dict(tool.input_schema)

    for token in (planned_token(), planned_token(None)):
        found = asyncio.run(schema(token))
        assert set(found["properties"]) == {"query"}
        assert found.get("required") == ["query"]


# ---- who may use a plan -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent", "region"),
    [("shopper-portal", "US"), ("shopper-query", "US"), ("catalog-subagent", None)],
)
def test_an_agent_without_the_tool_cannot_use_a_plan(agent: str, region: str | None) -> None:
    spy = Spy()
    result = search(spy, planned_token(agent=agent, region=region), {"query": "pants"})
    assert result.get("is_error") is True and "permission" in text(result)
    assert spy.plain == [] and spy.planned == []


def forge(**over: Any) -> str:
    body: dict[str, Any] = {
        "aud": "shopper",
        "region": "UK",
        "agent": "shopper-assistant",
        "srv": RAG_SERVER,
        "turn": str(uuid.uuid4()),
        "exp": int(time.time()) + 60,
    }
    body.update(over)
    payload = _b64(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
    return f"v1.{payload}.{_sign(KEY, payload)}"


@pytest.mark.parametrize(
    "token",
    [
        forge(srv=CATALOG_SERVER, qf=PLAN),  # signed, for the catalog, carrying a plan
        forge(qf={"parts": [{"c": ["Red"], "aud": "staff"}]}),  # a smuggled field
        forge(qf=PLAN, visibility="internal"),  # a smuggled claim
        forge(qf=PLAN, aud="staff"),  # staff audience with a shopper agent
    ],
    ids=["catalog-server", "smuggled-field", "smuggled-claim", "audience-mismatch"],
)
def test_a_forged_token_with_a_plan_never_reaches_a_search(token: str) -> None:
    spy = Spy()
    with pytest.raises(Exception):  # noqa: B017, PT011 - a bare 401 surfaces as a client error
        search(spy, token, {"query": "pants"})
    assert spy.plain == [] and spy.planned == []


def test_a_planned_token_for_this_server_is_refused_by_the_catalog_verifier() -> None:
    with pytest.raises(TokenError):
        verify_for(planned_token(), key=KEY, server=CATALOG_SERVER)


def test_a_plan_cannot_be_minted_for_the_catalog() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        mint(
            audience="shopper",
            region="UK",
            agent="shopper-assistant",
            key=KEY,
            server=CATALOG_SERVER,
            plan=PLAN,
        )


# ---- a plan cannot widen a search, or skip the cap ----------------------------------------------


def test_a_shopper_plan_naming_an_internal_product_still_runs_as_a_shopper() -> None:
    spy = Spy()
    search(spy, planned_token(region="US"), {"query": "pants"})
    [(parts, caller)] = spy.planned
    assert caller == Who("US", "shopper")
    assert "P0009" in (parts[1][1].products or frozenset())  # named, and only narrows


class PlainSearcher:
    """The server's `Searcher` protocol with no `search_planned`."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, query: str, *, audience: str, region: str | None) -> list[RetrievedChunk]:
        self.calls.append(query)
        return [SOURCE]


def test_a_searcher_that_cannot_run_a_plan_fails_the_search_it_does_not_fall_back() -> None:
    """The plain search would ignore the plan's filters. On the production server a plan that
    cannot run is a failed search (a fixed error), never the unfiltered search instead."""
    from northwind_mcp_rag.retrieve import TOOLS
    from northwind_mcp_rag.testing import running

    plain = PlainSearcher()
    server = RagServer(Backend(no_db, FakeEmbedder()), key=KEY, tools=TOOLS, searcher=plain)
    with running(server) as url:
        result = asyncio.run(call_as_dict(url, planned_token(), {"query": "pants"}))
    assert result.get("is_error") is True and SEARCH_FAILED in text(result)
    assert plain.calls == []
    with pytest.raises(RuntimeError):
        server.search("pants", verify_for(planned_token(), key=KEY, server=RAG_SERVER))
    assert plain.calls == []


def test_a_planned_search_counts_against_the_turns_cap() -> None:
    spy = Spy()
    turn = str(uuid.uuid4())
    token = planned_token(turn=turn)

    async def many() -> list[dict[str, Any]]:
        with serve_retriever(spy, Who) as (url, _):
            return [
                await call_as_dict(url, token, {"query": "pants"}) for _ in range(MAX_SEARCHES + 1)
            ]

    results = asyncio.run(many())
    assert len(spy.planned) == MAX_SEARCHES
    assert results[-1].get("is_error") is True and SEARCH_LIMIT in text(results[-1])


def test_a_planned_and_a_plain_token_of_one_turn_share_one_cap() -> None:
    spy = Spy()
    turn = str(uuid.uuid4())
    plain, _ = mint(
        audience="shopper",
        region="UK",
        agent="shopper-assistant",
        key=KEY,
        server=RAG_SERVER,
        turn=turn,
    )

    async def run() -> list[dict[str, Any]]:
        with serve_retriever(spy, Who) as (url, _):
            first = await call_as_dict(url, planned_token(turn=turn), {"query": "a"})
            rest = [await call_as_dict(url, plain, {"query": "b"}) for _ in range(MAX_SEARCHES)]
            return [first, *rest]

    results = asyncio.run(run())
    assert len(spy.planned) + len(spy.plain) == MAX_SEARCHES
    assert results[-1].get("is_error") is True


# ---- RagServer.search directly ------------------------------------------------------------------


class Recorder:
    """The server's own `Searcher` protocol: audience and region as keywords."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, str, str | None]] = []

    def search(self, query: str, *, audience: str, region: str | None) -> list[RetrievedChunk]:
        self.calls.append(("plain", query, audience, region))
        return [SOURCE]

    def search_planned(
        self, parts: Any, *, audience: str, region: str | None
    ) -> list[RetrievedChunk]:
        self.calls.append(("planned", list(parts), audience, region))
        return [SOURCE]


def no_db() -> Any:
    raise RuntimeError("no database behind this test")


def server_with(recorder: Recorder) -> RagServer:
    return RagServer(Backend(no_db, FakeEmbedder()), key=KEY, searcher=recorder)


def test_server_search_takes_audience_and_region_from_the_claims_for_a_planned_search() -> None:
    recorder = Recorder()
    claims = verify_for(planned_token(region="CA"), key=KEY, server=RAG_SERVER)
    server_with(recorder).search("pants", claims)
    [(kind, parts, audience, region)] = recorder.calls
    assert (kind, audience, region) == ("planned", "shopper", "CA")
    assert parts[0][1] == SearchFilter(colours=("Black",), sizes=("M",))


def test_server_search_without_a_plan_is_the_plain_search() -> None:
    recorder = Recorder()
    claims = verify_for(planned_token(None), key=KEY, server=RAG_SERVER)
    server_with(recorder).search("pants", claims)
    assert recorder.calls == [("plain", "pants", "shopper", "UK")]


def test_server_search_never_runs_both_for_one_call() -> None:
    recorder = Recorder()
    server = server_with(recorder)
    server.search("a", verify_for(planned_token(), key=KEY, server=RAG_SERVER))
    server.search("b", verify_for(planned_token(None), key=KEY, server=RAG_SERVER))
    assert [c[0] for c in recorder.calls] == ["planned", "plain"]
