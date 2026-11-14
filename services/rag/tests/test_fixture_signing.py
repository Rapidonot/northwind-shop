"""The test-only re-signing helper refuses the repository's own fixtures (#10b; the user's stop-5
decision, 2026-11-13): fail closed, before anything is read or written."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from northwind_data.ingest_guard import DatasetMismatchError, verify_dataset

from .fixture_signing import REPO_FIXTURES, RefusedTarget, rehash  # noqa: TID252

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("RAG-01")]


def _manifest_bytes() -> bytes:
    return (REPO_FIXTURES / "manifest.json").read_bytes()


@pytest.mark.parametrize("how", ["direct", "relative", "symlink", "inside"])
def test_the_repository_fixtures_are_never_re_signed(tmp_path: Path, how: str) -> None:
    before = _manifest_bytes()
    if how == "direct":
        target = REPO_FIXTURES
    elif how == "relative":
        target = REPO_FIXTURES / ".." / "fixtures"
    elif how == "symlink":
        target = tmp_path / "link"
        target.symlink_to(REPO_FIXTURES, target_is_directory=True)
    else:
        target = REPO_FIXTURES / "sub"
    with pytest.raises(RefusedTarget):
        rehash(target)
    assert _manifest_bytes() == before


def test_an_edited_copy_is_re_signed_and_then_verifies(tmp_path: Path) -> None:
    copy = tmp_path / "fixtures"
    shutil.copytree(REPO_FIXTURES, copy)
    reviews = copy / "reviews.jsonl"
    reviews.write_bytes(reviews.read_bytes() + b"\n")
    with pytest.raises(DatasetMismatchError):
        verify_dataset(copy)
    assert rehash(copy) == copy
    assert verify_dataset(copy) != verify_dataset(REPO_FIXTURES)
