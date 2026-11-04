"""Shared fixtures for the RAG-01 tests.

Integration tests never touch `public.rag_chunks`: each one gets its own throwaway schema, with
`public` second on the search_path because the `vector` type lives there.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from northwind_rag.cli import DEFAULT_DATABASE_URL

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "data" / "fixtures"

Records = list[dict[str, Any]]
Edit = Callable[[str, Callable[[Records], Records | None]], Path]


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def fixtures_copy(tmp_path: Path) -> Path:
    """A private copy of data/fixtures. Tests edit this one, never the committed files."""
    target = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, target)
    return target


@pytest.fixture
def edit(fixtures_copy: Path) -> Edit:
    """edit("products.jsonl", fn): fn mutates the record list in place (or returns a new one)."""

    def _edit(name: str, fn: Callable[[Records], Records | None]) -> Path:
        path = fixtures_copy / name
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        result = fn(records)
        if result is not None:
            records = result
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records),
            encoding="utf-8",
        )
        return fixtures_copy

    return _edit


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    """A connection whose search_path is a throwaway schema first, then public."""
    schema = f"rag01_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(_database_url(), autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    connection = psycopg.connect(
        _database_url(), autocommit=True, options=f"-csearch_path={schema},public"
    )
    try:
        yield connection
    finally:
        connection.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()
