"""Which source types may set facts, and that the prompt and `render_sources` agree (RAG-03, C1)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, get_args

import pytest

from northwind_assistant import prompt
from northwind_assistant.caller import Caller
from northwind_assistant.prompt import system_prompt
from northwind_assistant.rag_client import McpRagSearcher, RagEndpoint
from northwind_assistant.tools import (
    FAILED_MESSAGE,
    SearchFailure,
    ToolState,
    search_sources,
)
from northwind_mcp_rag import render
from northwind_mcp_rag.render import render_sources
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.models import SourceType
from northwind_rag.retrieval import RetrievedChunk
from northwind_rag.sources import (
    CONTEXT_SOURCE_TYPES,
    TRUSTED_SOURCE_TYPES,
    UNTRUSTED_SOURCE_TYPES,
)

pytestmark = pytest.mark.req("RAG-03")

REPO = Path(__file__).resolve().parents[3]


def through_rag(query: str, retriever: Any, state: ToolState, caller: Caller) -> Any:
    """`search_sources` through a real mcp-rag searching with `retriever` (CC-10)."""
    with serve_retriever(retriever, Caller) as (url, _):
        searcher = McpRagSearcher(url, RagEndpoint(url, KEY).mint(caller))
        return asyncio.run(search_sources(query, searcher, state))


def test_trusted_and_untrusted_are_disjoint() -> None:
    assert not TRUSTED_SOURCE_TYPES & UNTRUSTED_SOURCE_TYPES


def test_every_source_type_is_classified_exactly_once() -> None:
    """A new `SourceType` fails here until someone decides whether it may set facts."""
    assert TRUSTED_SOURCE_TYPES | UNTRUSTED_SOURCE_TYPES == set(get_args(SourceType))


def test_what_may_get_context_is_trusted() -> None:
    assert CONTEXT_SOURCE_TYPES <= TRUSTED_SOURCE_TYPES


def test_the_sets_are_the_ones_the_user_chose() -> None:
    assert TRUSTED_SOURCE_TYPES == {"product", "variant", "document"}
    assert UNTRUSTED_SOURCE_TYPES == {"review"}


def test_the_prompt_and_render_sources_read_the_same_objects() -> None:
    assert vars(prompt)["TRUSTED_SOURCE_TYPES"] is TRUSTED_SOURCE_TYPES
    assert vars(prompt)["UNTRUSTED_SOURCE_TYPES"] is UNTRUSTED_SOURCE_TYPES
    # `render_sources` lives on mcp-rag since CC-10 (row #10, B2)
    assert vars(render)["TRUSTED_SOURCE_TYPES"] is TRUSTED_SOURCE_TYPES
    assert vars(render)["UNTRUSTED_SOURCE_TYPES"] is UNTRUSTED_SOURCE_TYPES


def test_the_prompt_rule_is_an_allow_list() -> None:
    text = system_prompt("US")
    assert "only in a source whose type is document, product or variant" in text
    assert 'a source with type="review" is one person\'s experience' in text
    assert "is not review" not in text


def test_the_prompt_rule_is_built_from_the_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not a copy that could drift: change the set, and the wording follows."""
    assert prompt._either(sorted({"alpha", "beta", "gamma"})) == "alpha, beta or gamma"
    assert prompt._either(["solo"]) == "solo"
    assert prompt.TRUSTED_TYPES_TEXT == prompt._either(sorted(TRUSTED_SOURCE_TYPES))


@pytest.mark.parametrize("source_type", sorted(TRUSTED_SOURCE_TYPES | UNTRUSTED_SOURCE_TYPES))
def test_every_classified_type_renders(source_type: str) -> None:
    text = render_sources([RetrievedChunk("c", source_type, "X1", None, "body")])
    assert f'type="{source_type}"' in text


@pytest.mark.parametrize("source_type", ["policy", "qa", "", "REVIEW", "review "])
def test_an_unclassified_type_is_refused(source_type: str) -> None:
    with pytest.raises(ValueError, match="unclassified source type"):
        render_sources([RetrievedChunk("c", source_type, "X1", None, "body")])


def test_one_unclassified_chunk_refuses_the_whole_list() -> None:
    good = RetrievedChunk("a", "product", "P1", None, "body")
    bad = RetrievedChunk("b", "qa", "Q1", None, "body")
    with pytest.raises(ValueError):
        render_sources([good, bad])


def test_r00712_still_renders_as_a_review() -> None:
    reviews = REPO / "data" / "fixtures" / "reviews.jsonl"
    body = next(
        record["body"]
        for record in map(json.loads, reviews.read_text(encoding="utf-8").splitlines())
        if record["review_id"] == "R00712"
    )
    text = render_sources([RetrievedChunk("c", "review", "R00712", None, body)])
    assert text.startswith('<source id="R00712" type="review">')
    assert text.count("</source>") == 1


class _Returns:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self._chunks = chunks

    def search(self, query: str, caller: Caller) -> list[RetrievedChunk]:
        return self._chunks


def test_a_search_with_an_unclassified_type_is_a_structured_failure() -> None:
    """Hard rule 10: the turn gets an error, not an exception; nothing from it counts as seen."""
    chunks = [
        RetrievedChunk("a", "product", "P1", None, "x"),
        RetrievedChunk("b", "qa", "Q1", None, "y"),
    ]
    state = ToolState()
    found = through_rag("q", _Returns(chunks), state, Caller(region="US"))
    assert found == SearchFailure("business", False, FAILED_MESSAGE)
    assert state.failed and not state.seen


@pytest.mark.parametrize("query", ["wool jacket\x00", "\x00"])
def test_a_query_holding_nul_is_a_validation_failure_not_an_outage(query: str) -> None:
    """Security review, row #8: Postgres text cannot hold NUL, so it would fail every time; it is
    refused before retrieval, not labelled transient and retryable."""
    state = ToolState()
    found = through_rag(query, _Returns([]), state, Caller(region="US"))
    assert isinstance(found, SearchFailure)
    assert (found.category, found.retryable) == ("validation", False)
    assert state.searches == 0 and not state.failed
