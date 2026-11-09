"""RAG-11 (row #9): for every red-team item, nothing internal and no other region's policy reaches
the model, on the server's first search and on a refinement search alike. Scripted model, real
corpus, fake embedder: no spend.

The fake embedder puts a chunk's own text at distance zero from itself, so a query that quotes a
chunk makes it the nearest, and only the filter keeps it out (as in `test_context.py`).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
from northwind_rag.sources import build_chunks
from northwind_rag.testing import FakeEmbedder

pytestmark = [pytest.mark.integration, pytest.mark.req("RAG-11")]

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "data" / "fixtures"
RED_TEAM = REPO / "evals" / "adversarial" / "rag11_red_team.jsonl"
ANSWER_KEY = FIXTURES / "_answer_key.json"
SOURCE_ID = re.compile(r'<source id="([^"]*)"')


def items() -> list[dict[str, Any]]:
    return [json.loads(line) for line in RED_TEAM.read_text(encoding="utf-8").splitlines()]


ITEMS = items()


@pytest.fixture(scope="module")
def index_retriever() -> Iterator[IndexRetriever]:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"rag11_test_{uuid.uuid4().hex[:12]}"
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


def strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def everything_sent(query: str, region: Any, retriever: IndexRetriever, root: Path) -> str:
    """Every string in every request of a turn that searches twice: the server's search on the
    shopper's message and a refinement with the same text."""
    with ScriptedLLM() as llm:
        llm.reply_tool_use(ALLOWED_TOOL, {"query": query})
        llm.reply_stream("done")
        runtime = agent_runtime(base_url=llm.url, root=root)

        async def run() -> None:
            async for _ in stream_answer(query, Caller(region=region), retriever, runtime):
                pass

        asyncio.run(run())
    assert len(llm.requests) == 2  # the refinement ran, so its result is in the second request
    return "\n".join(s for request in llm.requests for s in strings(request["body"]))


_SENT: dict[str, str] = {}


def sent_for(item: dict[str, Any], retriever: IndexRetriever, root: Path) -> str:
    """One scripted turn per item, shared by the two tests below."""
    if item["id"] not in _SENT:
        _SENT[item["id"]] = everything_sent(item["query"], item["region"], retriever, root)
    return _SENT[item["id"]]


def canaries() -> list[str]:
    key = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    return [str(entry["canary"]) for entry in key["internal_only"]]


def internal_source_ids() -> set[str]:
    return {c.source_id for c in build_chunks(FIXTURES) if c.metadata.visibility != "public"}


@pytest.mark.parametrize("item", ITEMS, ids=[i["id"] for i in ITEMS])
def test_nothing_internal_reaches_the_model_for_a_red_team_item(
    item: dict[str, Any], index_retriever: IndexRetriever, tmp_path: Path
) -> None:
    sent = sent_for(item, index_retriever, tmp_path)
    cited = SOURCE_ID.findall(sent)
    assert cited, "no sources reached the model, so the check would be empty"
    assert not set(cited) & internal_source_ids(), sorted(set(cited) & internal_source_ids())
    assert not any(c.startswith(("INT-", "P0009")) for c in cited)
    for mark in canaries():
        assert mark not in sent
    assert "NWCANARY" not in sent


def source_regions() -> dict[str, set[str]]:
    """Each source's regions from chunk metadata, so regional FAQs (FAQ-019 to FAQ-030, no region
    in the ID) are checked too (security review pass 1, F3). A source with no region (P0009's
    record) maps to an empty set, which no shopper region meets."""
    regions: dict[str, set[str]] = {}
    for c in build_chunks(FIXTURES):
        regions.setdefault(c.source_id, set()).update(c.metadata.region or ())
    return regions


@pytest.mark.parametrize("item", ITEMS, ids=[i["id"] for i in ITEMS])
def test_the_model_gets_only_its_regions_policies(
    item: dict[str, Any], index_retriever: IndexRetriever, tmp_path: Path
) -> None:
    sent = sent_for(item, index_retriever, tmp_path)
    regions = source_regions()
    allowed = {item["region"], "ALL"}
    wrong = {c for c in SOURCE_ID.findall(sent) if not regions[c] & allowed}
    assert not wrong, (item["id"], sorted(wrong))


def test_the_region_map_knows_the_regional_faqs() -> None:
    regions = source_regions()
    assert regions["FAQ-028"] == {"EU"}
    assert regions["POL-RETURNS-CA"] == {"CA"}


def test_the_internal_source_ids_are_the_six_documents_and_p0009() -> None:
    ids = internal_source_ids()
    assert {f"INT-00{n}" for n in range(1, 7)} <= ids
    assert any(i.startswith("P0009") for i in ids)
