"""The index-manifest resource (CC-10 B4; RAG-11 hard rule 7). A throwaway index in Postgres."""

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
from northwind_mcp_rag.server import MANIFEST_URI, Backend, RagServer
from northwind_mcp_rag.testing import KEY, connect, running, token
from northwind_rag import store
from northwind_rag.cli import DEFAULT_DATABASE_URL
from northwind_rag.ingest import ingest
from northwind_rag.retrieval import retriever_id
from northwind_rag.testing import FakeEmbedder

pytestmark = [pytest.mark.integration, pytest.mark.req("CC-10")]

REPO = Path(__file__).resolve().parents[3]
EMBEDDER = FakeEmbedder()


def _url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


@pytest.fixture(scope="module")
def index() -> Iterator[Callable[[], psycopg.Connection]]:
    schema = f"mcprag_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(_url(), autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')

    def connect_index() -> psycopg.Connection:
        return psycopg.connect(_url(), autocommit=True, options=f"-csearch_path={schema},public")

    try:
        with connect_index() as conn:
            store.apply_migrations(conn)
            ingest(conn, REPO / "data" / "fixtures", EMBEDDER)
        yield connect_index
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def read(url: str, agent: str, region: str | None) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        async with connect(url, token(agent, region)[0]) as client:
            result = await client.read_resource(MANIFEST_URI)
        [content] = result.contents
        return dict(json.loads(content.text))  # type: ignore[union-attr]

    return asyncio.run(go())


def counts(conn: psycopg.Connection, visibilities: tuple[str, ...]) -> dict[str, int]:
    rows = conn.execute(
        "SELECT source_type, count(*) FROM rag_chunks WHERE visibility = ANY(%s) "
        "GROUP BY source_type",
        (list(visibilities),),
    ).fetchall()
    return {source_type: int(n) for source_type, n in rows}


def test_a_shopper_manifest_counts_only_public_chunks(
    index: Callable[[], psycopg.Connection],
) -> None:
    with index() as conn:
        shopper = store.index_manifest(conn, "shopper")
        staff = store.index_manifest(conn, "staff")
        public = counts(conn, ("public",))
        everything = counts(conn, ("public", "internal"))
    assert shopper is not None and staff is not None
    assert shopper["chunks_by_source_type"] == public
    assert staff["chunks_by_source_type"] == everything
    assert public != everything  # the fixtures hold internal chunks, so the filter is real
    assert {k: v for k, v in shopper.items() if k != "chunks_by_source_type"} == {
        k: v for k, v in staff.items() if k != "chunks_by_source_type"
    }


def test_an_unknown_audience_is_refused(index: Callable[[], psycopg.Connection]) -> None:
    with index() as conn, pytest.raises(ValueError):
        store.index_manifest(conn, "partner")


def test_the_staff_agent_reads_the_manifest_over_mcp(
    index: Callable[[], psycopg.Connection],
) -> None:
    server = RagServer(Backend(index, EMBEDDER), key=KEY)
    with running(server) as url:
        manifest = read(url, "catalog-subagent", None)
    with index() as conn:
        expected = store.index_manifest(conn, "staff")
    assert expected is not None
    assert manifest == {
        **expected,
        "retriever_id": retriever_id(EMBEDDER),
        "dataset_verified": False,
    }
    assert set(manifest) == {
        "index_version",
        "data_version",
        "dataset_sha256",
        "dataset_verified",
        "chunker_version",
        "embedder_id",
        "retriever_id",
        "refreshed_at",
        "chunks_by_source_type",
    }


@pytest.mark.req("RAG-11")
def test_no_canary_and_no_chunk_text_is_in_the_manifest(
    index: Callable[[], psycopg.Connection],
) -> None:
    canaries = load_canaries(REPO, required=True)
    assert len(canaries) == 7
    with index() as conn:
        shopper = json.dumps(store.index_manifest(conn, "shopper"))
    server = RagServer(Backend(index, EMBEDDER), key=KEY)
    with running(server) as url:
        staff = json.dumps(read(url, "catalog-subagent", None))
    for text in (shopper, staff):
        assert "NWCANARY-" not in text
        assert not [c for c in canaries if c in text]
        assert "P0009" not in text
