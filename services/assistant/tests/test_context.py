"""What actually reaches the model, over the real corpus (SA-02; hard rule 7, RAG-12 finding 3)."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.retriever import IndexRetriever
from northwind_assistant.tools import ALLOWED_TOOL
from northwind_assistant.turn import stream_answer
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_rag.cli import DEFAULT_DATABASE_URL
from northwind_rag.ingest import ingest
from northwind_rag.models import Chunk
from northwind_rag.retrieval import ShopperRegion
from northwind_rag.sources import build_chunks
from northwind_rag.testing import FakeEmbedder

pytestmark = [pytest.mark.integration, pytest.mark.req("SA-02")]

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "data" / "fixtures"


@pytest.fixture
def index_retriever() -> Iterator[IndexRetriever]:
    """A throwaway schema holding the real fixtures, with a fake embedder (no model download)."""
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"sa02_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')

    def connect() -> psycopg.Connection:
        return psycopg.connect(url, autocommit=True, options=f"-csearch_path={schema},public")

    embedder = FakeEmbedder()
    try:
        with connect() as conn:
            ingest(conn, FIXTURES, embedder)
        yield IndexRetriever(connect, embedder)
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def tool_result_text(llm: ScriptedLLM) -> str:
    """Every tool result in the last request, which is the context the model is given."""
    return "".join(
        content["text"]
        for message in llm.requests[-1]["body"]["messages"]
        if isinstance(message["content"], list)
        for block in message["content"]
        if block.get("type") == "tool_result"
        for content in block["content"]
    )


def model_context(query: str, region: ShopperRegion, retriever: IndexRetriever, root: Path) -> str:
    """The tool result the model is given. The fake embedder puts a chunk's own text at distance
    zero from itself, so quoting a chunk makes it the nearest, and only a filter keeps it out."""
    with ScriptedLLM() as llm:
        llm.reply_tool_use(ALLOWED_TOOL, {"query": query})
        llm.reply_stream("done")
        runtime = agent_runtime(base_url=llm.url, root=root)

        async def run() -> None:
            async for _ in stream_answer("hi", Caller(region=region), retriever, runtime):
                pass

        asyncio.run(run())
    return tool_result_text(llm)


def returns_policy(region: str) -> Chunk:
    return next(c for c in build_chunks(FIXTURES) if c.source_id == f"POL-RETURNS-{region}")


@pytest.mark.parametrize(
    ("region", "other"), [("US", "UK"), ("CA", "US"), ("UK", "EU"), ("EU", "CA")]
)
def test_the_model_sees_its_own_regions_policy_and_never_anothers(
    region: ShopperRegion, other: str, index_retriever: IndexRetriever, tmp_path: Path
) -> None:
    """RAG-12 security review, finding 3: one prompt held all four regions' conflicting rules."""
    own = model_context(returns_policy(region).text, region, index_retriever, tmp_path / "own")
    assert f'id="POL-RETURNS-{region}"' in own
    quoted = model_context(returns_policy(other).text, region, index_retriever, tmp_path / "other")
    assert quoted, "the shopper still gets sources"
    assert f'id="POL-RETURNS-{other}"' not in quoted


def test_no_canary_reaches_the_model(index_retriever: IndexRetriever, tmp_path: Path) -> None:
    """Quotes each internal chunk that holds a canary, the closest a query can get."""
    key = json.loads((FIXTURES / "_answer_key.json").read_text(encoding="utf-8"))
    canaries = [entry["canary"] for entry in key["internal_only"]]
    assert len(canaries) == 7
    internal = [c for c in build_chunks(FIXTURES) if c.metadata.visibility == "internal"]
    holding = {canary: [c for c in internal if canary in c.text] for canary in canaries}
    assert all(holding.values())
    for number, (canary, chunks) in enumerate(holding.items()):
        context = model_context(chunks[0].text, "US", index_retriever, tmp_path / str(number))
        assert context and canary not in context
