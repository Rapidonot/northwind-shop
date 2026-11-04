"""FOUND-1: the answer key and live tables are refused by the ingest guard RAG-01 must use."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from northwind_data.dataset import FIXTURES_DIR
from northwind_data.ingest_guard import NotIngestibleError, assert_ingestible, ingestible_files


@pytest.mark.req("FOUND-1")
@pytest.mark.parametrize(
    "name", ["_answer_key.json", "prices.jsonl", "stock.jsonl", "regions.json", "manifest.json"]
)
def test_guard_refuses_non_ingestible_files(name: str) -> None:
    with pytest.raises(NotIngestibleError):
        assert_ingestible(FIXTURES_DIR / name)


@pytest.mark.req("FOUND-1")
def test_guard_refuses_a_renamed_copy_of_the_answer_key(tmp_path: Path) -> None:
    copy = tmp_path / "products.jsonl"  # even under an ingestible name
    shutil.copy(FIXTURES_DIR / "_answer_key.json", copy)
    with pytest.raises(NotIngestibleError, match="never_ingest"):
        assert_ingestible(copy, manifest={"ingestible": ["products.jsonl"]})


@pytest.mark.req("FOUND-1")
def test_guard_refuses_anything_under_an_underscore_directory(tmp_path: Path) -> None:
    hidden = tmp_path / "_private" / "documents.jsonl"
    hidden.parent.mkdir()
    shutil.copy(FIXTURES_DIR / "documents.jsonl", hidden)
    with pytest.raises(NotIngestibleError):
        assert_ingestible(hidden, manifest={"ingestible": ["documents.jsonl"]})


@pytest.mark.req("FOUND-1")
def test_only_the_four_text_files_are_ingestible() -> None:
    assert [p.name for p in ingestible_files(FIXTURES_DIR)] == [
        "documents.jsonl",
        "products.jsonl",
        "reviews.jsonl",
        "variants.jsonl",
    ]
