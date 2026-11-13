"""The index-manifest resource (CC-10 B4; RAG-11 hard rule 7). A throwaway index in Postgres."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
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
        # #10b, Q4 (decision D4): built from the real fixtures, which the server verifies now.
        "dataset_verified": True,
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


# ---- Q4 (#10b, decision D4): `dataset_verified` is checked against the server's fixtures ------


def _tampered_copy(tmp_path: Path) -> Path:
    target = tmp_path / "fixtures"
    shutil.copytree(REPO / "data" / "fixtures", target)
    path = target / "prices.jsonl"
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01  # one byte; the manifest is not re-hashed
    path.write_bytes(bytes(data))
    return target


def test_dataset_verified_is_true_when_the_fixtures_match_the_stored_hash(
    index: Callable[[], psycopg.Connection],
) -> None:
    server = RagServer(Backend(index, EMBEDDER, fixtures_dir=REPO / "data" / "fixtures"), key=KEY)
    with running(server) as url:
        assert read(url, "catalog-subagent", None)["dataset_verified"] is True


def test_dataset_verified_is_false_on_a_tampered_copy_and_the_manifest_is_still_served(
    index: Callable[[], psycopg.Connection], tmp_path: Path
) -> None:
    server = RagServer(Backend(index, EMBEDDER, fixtures_dir=_tampered_copy(tmp_path)), key=KEY)
    with running(server) as url:
        manifest = read(url, "catalog-subagent", None)
    with index() as conn:
        expected = store.index_manifest(conn, "staff")
    assert expected is not None
    assert manifest["dataset_verified"] is False
    assert manifest == {
        **expected,
        "retriever_id": retriever_id(EMBEDDER),
        "dataset_verified": False,
    }


def test_dataset_verified_is_false_for_a_missing_directory_and_never_an_error(
    index: Callable[[], psycopg.Connection], tmp_path: Path
) -> None:
    server = RagServer(Backend(index, EMBEDDER, fixtures_dir=tmp_path / "nowhere"), key=KEY)
    with running(server) as url:
        manifest = read(url, "catalog-subagent", None)
    assert manifest["dataset_verified"] is False
    assert "isError" not in manifest
    assert manifest["index_version"]


def test_the_backend_default_fixtures_directory_is_the_repos(
    index: Callable[[], psycopg.Connection],
) -> None:
    assert Backend(index, EMBEDDER).fixtures_dir.resolve() == (REPO / "data" / "fixtures").resolve()


def test_dataset_verified_is_false_when_verified_fixtures_differ_from_the_stored_hash() -> None:
    """The fixtures verify, but the index was published from another dataset: false. Kills the
    mutant that reports true whenever the fixtures verify (M6, #10b failing-first log)."""
    schema = f"mcprag_other_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(_url(), autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')

    def connect_other() -> psycopg.Connection:
        return psycopg.connect(_url(), autocommit=True, options=f"-csearch_path={schema},public")

    try:
        with connect_other() as conn:
            store.apply_migrations(conn)
            conn.execute(
                "INSERT INTO rag_index_manifest (index_version, data_version, dataset_sha256, "
                "corpus_sha256, chunker_version, embedder_id, chunk_count, is_current) "
                "VALUES ('other', 2, %s, 'c', 1, 'e', 0, true)",
                ("ab" * 32,),
            )
        server = RagServer(
            Backend(connect_other, EMBEDDER, fixtures_dir=REPO / "data" / "fixtures"), key=KEY
        )
        with running(server) as url:
            manifest = read(url, "catalog-subagent", None)
        assert manifest["dataset_sha256"] == "ab" * 32
        assert manifest["dataset_verified"] is False
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()
