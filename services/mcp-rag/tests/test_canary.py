"""C8 (rag): no internal record reaches a shopper through `retrieve_context` (CC-10; RAG-11, hard
rule 7). A throwaway index of the real fixtures in Postgres, shopper tokens in every region, and a
battery of queries aimed at each internal document and at P0009 by title, ID and canary."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from northwind_llm_replay.scan import load_canaries
from northwind_mcp_rag.retrieve import TOOLS
from northwind_mcp_rag.server import Backend, RagServer
from northwind_mcp_rag.testing import KEY, call_as_dict, running, token
from northwind_rag import store
from northwind_rag.cli import DEFAULT_DATABASE_URL
from northwind_rag.ingest import ingest
from northwind_rag.testing import FakeEmbedder

pytestmark = [pytest.mark.integration, pytest.mark.req("CC-10"), pytest.mark.req("RAG-11")]

REPO = Path(__file__).resolve().parents[3]
KEY_FILE = REPO / "data" / "fixtures" / "_answer_key.json"
INTERNAL = json.loads(KEY_FILE.read_text(encoding="utf-8"))["internal_only"]
REGIONS = ("US", "CA", "UK", "EU")
QUERIES = sorted(
    {
        q
        for e in INTERNAL
        for q in (e["title"], e.get("doc_id", ""), e.get("product_id", ""), e["canary"])
        if q
    }
    | {"Corvane Ascent Pro Shell price", "internal playbook", "staff only policy", "P0009-BLK-M"}
)
EMBEDDER = FakeEmbedder()


@pytest.fixture(scope="module")
def index() -> Iterator[Callable[[], psycopg.Connection]]:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"mcprag_canary_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')

    def connect_index() -> psycopg.Connection:
        return psycopg.connect(url, autocommit=True, options=f"-csearch_path={schema},public")

    try:
        with connect_index() as conn:
            store.apply_migrations(conn)
            ingest(conn, REPO / "data" / "fixtures", EMBEDDER)
        yield connect_index
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def battery(url: str, region: str) -> list[dict[str, Any]]:
    async def go() -> list[dict[str, Any]]:
        out = []
        for query in QUERIES:
            tok = token("shopper-assistant", region)[0]  # a turn each: the cap is per turn
            out.append(await call_as_dict(url, tok, {"query": query}))
        return out

    return asyncio.run(go())


def test_the_battery_is_aimed_at_every_internal_record() -> None:
    assert len(INTERNAL) == 7 and len(load_canaries(REPO, required=True)) == 7
    assert {"INT-001", "INT-006", "P0009"} <= set(QUERIES)


@pytest.mark.parametrize("region", REGIONS)
def test_no_internal_record_reaches_a_shopper_in_any_region(
    index: Callable[[], psycopg.Connection], region: str
) -> None:
    canaries = load_canaries(REPO, required=True)
    server = RagServer(Backend(index, EMBEDDER), key=KEY, tools=TOOLS)
    with running(server) as url:
        results = battery(url, region)
    assert not [r for r in results if r.get("is_error")]  # every search ran
    found_something = [r for r in results if "<source id=" in json.dumps(r)]
    assert found_something  # the searches returned public sources, so this is not vacuous
    sent = json.dumps(results)
    assert "NWCANARY-" not in sent
    assert not [c for c in canaries if c in sent]
    assert 'id="P0009' not in sent and 'id="INT-' not in sent


def test_the_same_battery_reaches_internal_records_for_staff(
    index: Callable[[], psycopg.Connection],
) -> None:
    """The control: the index holds the internal records and the queries find them, so a shopper's
    clean result is the filter at work. Searched as staff through the server's own path."""
    server = RagServer(Backend(index, EMBEDDER), key=KEY, tools=TOOLS)
    claims = token("catalog-subagent", None)[1]
    hits = [c.source_id for q in QUERIES for c in server.search(q, claims)]
    assert any(h.startswith(("INT-", "P0009")) for h in hits)
