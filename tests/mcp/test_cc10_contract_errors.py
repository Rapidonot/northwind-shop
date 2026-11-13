"""Contract tests for mcp-rag's real `retrieve_context`, over HTTP (CC-10 C4, C5, C9; plan
section 10; NFR-01, NFR-04, RAG-11; hard rules 8, 9, 10).

Complements `test_contract.py`, which runs the spy-free scope, schema and gate checks: here every
error path of the real handler is read as the wire shows it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import mcp.types as t
import pytest

from northwind_common.errors import ENVELOPE_KEYS
from northwind_common.rag_tool import NO_SOURCES, TOOL_NAME
from northwind_common.tool_scopes import AGENT_TOOLS, FORBIDDEN_FRAGMENTS
from northwind_mcp_catalog.tools import TOOLS as CATALOG_TOOLS
from northwind_mcp_rag.retrieve import TOOLS
from northwind_mcp_rag.server import Backend, RagServer
from northwind_mcp_rag.testing import KEY, CallerSearcher, connect, running, token
from northwind_rag.retrieval import RetrievedChunk
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("CC-10")

SOURCE = RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")
SECRET = "postgresql://user:hunter2@db/prod"  # noqa: S105 - a planted fake
CARD = "4242" * 4  # synthetic
MARK = "QUERYECHO-31bd"


@dataclass(frozen=True)
class Who:
    region: str | None
    audience: str


class Searcher:
    def __init__(self, chunks: Sequence[RetrievedChunk] = (SOURCE,), fail: bool = False) -> None:
        self.chunks, self.fail, self.calls = chunks, fail, 0

    def search(self, query: str, caller: Who) -> Sequence[RetrievedChunk]:
        self.calls += 1
        if self.fail:
            raise RuntimeError(SECRET)
        return self.chunks


def _db_down() -> Any:
    raise RuntimeError(SECRET)


def server_with(searcher: Searcher | None) -> RagServer:
    """With a searcher, the double; without, the production `IndexSearcher` on a dead database."""
    return RagServer(
        Backend(_db_down, FakeEmbedder()),
        key=KEY,
        tools=TOOLS,
        searcher=None if searcher is None else CallerSearcher(searcher, Who),
    )


def call(
    server: RagServer,
    arguments: dict[str, Any],
    *,
    agent: str = "shopper-assistant",
    name: str = TOOL_NAME,
    times: int = 1,
) -> t.CallToolResult:
    region = None if agent == "catalog-subagent" else "UK"

    async def go(url: str) -> t.CallToolResult:
        async with connect(url, token(agent, region)[0]) as client:
            result = None
            for _ in range(times):
                result = await client.call_tool(name, arguments)
            assert result is not None
            return result

    with running(server) as url:
        return asyncio.run(go(url))


def body_of(result: t.CallToolResult) -> dict[str, Any]:
    [block] = result.content
    assert isinstance(block, t.TextContent)
    body: dict[str, Any] = json.loads(block.text)
    return body


ERROR_CASES: list[tuple[str, Any, dict[str, Any], dict[str, Any], tuple[str, bool, str]]] = [
    ("wrong type", Searcher(), {"query": 5}, {}, ("validation", False, "bad_query")),
    ("list", Searcher(), {"query": [MARK]}, {}, ("validation", False, "bad_query")),
    ("empty", Searcher(), {"query": "  "}, {}, ("validation", False, "bad_query")),
    ("missing", Searcher(), {}, {}, ("validation", False, "bad_query")),
    ("only unknown", Searcher(), {"audience": "staff"}, {}, ("validation", False, "bad_query")),
    ("nul", Searcher(), {"query": f"{MARK}\x00"}, {}, ("validation", False, "bad_query")),
    ("card", Searcher(), {"query": f"{MARK} {CARD}"}, {}, ("validation", False, "card_data")),
    ("cap", Searcher(), {"query": MARK}, {"times": 4}, ("business", False, "search_limit")),
    (
        "store raises",
        Searcher(fail=True),
        {"query": MARK},
        {},
        ("transient", True, "search_failed"),
    ),
    ("db down", None, {"query": MARK}, {}, ("transient", True, "search_failed")),
    (
        "unclassified",
        Searcher([RetrievedChunk("c9", "policy-note", "D-1", None, "x")]),
        {"query": MARK},
        {},
        ("business", False, "source_refused"),
    ),
    (
        "out of scope: staff",
        Searcher(),
        {"query": MARK},
        {"agent": "catalog-subagent"},
        ("permission", False, "not_allowed"),
    ),
    (
        "out of scope: unknown tool",
        Searcher(),
        {"order_id": "O-1"},
        {"name": "capture_payment"},
        ("permission", False, "not_allowed"),
    ),
]


@pytest.mark.parametrize(
    ("label", "searcher", "arguments", "extra", "expected"),
    ERROR_CASES,
    ids=[c[0] for c in ERROR_CASES],
)
def test_every_error_path_is_the_four_keys_plus_code_with_protocol_is_error(
    label: str,
    searcher: Searcher | None,
    arguments: dict[str, Any],
    extra: dict[str, Any],
    expected: tuple[str, bool, str],
) -> None:
    result = call(server_with(searcher), arguments, **extra)
    assert result.is_error is True
    body = body_of(result)
    assert set(body) == ENVELOPE_KEYS
    assert body["isError"] is True
    assert (body["errorCategory"], body["isRetryable"], body["code"]) == expected
    assert isinstance(body["message"], str) and body["message"].strip()
    assert result.structured_content == body
    # Fixed text: nothing of the query, the card, the exception or the connection string.
    shown = json.dumps(body) + json.dumps(result.structured_content)
    for needle in (MARK, CARD, "4242", "hunter2", SECRET, "RuntimeError", "postgresql"):
        assert needle not in shown


@pytest.mark.parametrize("label", ["wrong type", "empty", "nul", "card", "out of scope: staff"])
def test_a_refusal_before_the_search_searches_nothing(label: str) -> None:
    [(_, searcher, arguments, extra, _)] = [c for c in ERROR_CASES if c[0] == label]
    call(server_with(searcher), arguments, **extra)
    assert searcher.calls == 0


def test_found_nothing_and_access_failed_are_distinguishable_on_the_wire() -> None:
    nothing = call(server_with(Searcher([])), {"query": "q"})
    down = call(server_with(Searcher(fail=True)), {"query": "q"})
    store_down = call(server_with(None), {"query": "q"})
    assert nothing.is_error is False and nothing.structured_content is None
    assert [b.text for b in nothing.content if isinstance(b, t.TextContent)] == [NO_SOURCES]
    for failed in (down, store_down):
        assert failed.is_error is True
        assert body_of(failed)["errorCategory"] == "transient"
        assert body_of(failed)["isRetryable"] is True
    assert body_of(down) == body_of(store_down)  # a dead store looks like any other failure


def test_a_found_result_is_text_only_with_no_structured_content() -> None:
    result = call(server_with(Searcher()), {"query": "q"})
    assert result.is_error is False and result.structured_content is None
    assert all(isinstance(b, t.TextContent) for b in result.content)


@pytest.mark.req("NFR-01")
def test_the_real_registry_holds_no_refund_capture_or_assign_tool_and_nothing_that_writes() -> None:
    fragments = FORBIDDEN_FRAGMENTS["shopper-assistant"]
    # #10b: mcp-catalog's registry joins (decision D1 scopes it to the assistant).
    registry = {**TOOLS, **CATALOG_TOOLS}
    for name, spec in registry.items():
        assert not any(f in name.lower() for f in fragments)
        schema = spec.tool.input_schema
        # Hard rule 9: a tool that changed state would take an idempotency key.
        assert not {"idempotency_key", "dry_run"} & set(schema.get("properties", {}))
        assert spec.tool.annotations is not None
        assert spec.tool.annotations.read_only_hint is True
        assert spec.tool.annotations.destructive_hint in (None, False)
    granted = {n for row in AGENT_TOOLS.values() for names in row.values() for n in names}
    assert granted == set(registry)


@pytest.mark.req("NFR-01")
def test_the_staff_agent_cannot_list_or_call_the_search_tool() -> None:
    async def go(url: str) -> list[str]:
        async with connect(url, token("catalog-subagent", None)[0]) as client:
            return [tool.name for tool in (await client.list_tools()).tools]

    with running(server_with(Searcher())) as url:
        assert asyncio.run(go(url)) == []


def test_extra_arguments_with_a_query_never_change_who_is_searching() -> None:
    seen: list[Who] = []

    class Spy:
        def search(self, query: str, caller: Who) -> Sequence[RetrievedChunk]:
            seen.append(caller)
            return [SOURCE]

    arguments = {
        "query": "q",
        "audience": "staff",
        "region": "US",
        "agent": "catalog-subagent",
        "visibility": "internal",
        "turn": "x",
    }
    call(server_with(Spy()), arguments)  # type: ignore[arg-type]
    assert seen == [Who(region="UK", audience="shopper")]
