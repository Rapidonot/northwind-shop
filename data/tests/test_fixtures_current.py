"""FOUND-1: committed fixtures equal the generator's output, and DATA_VERSION guards changes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_data.cli import main
from northwind_data.dataset import FIXTURES_DIR, Dataset
from northwind_data.version import DATA_VERSION


@pytest.mark.req("FOUND-1")
def test_committed_fixtures_match_the_generator(ds: Dataset) -> None:
    committed = Dataset.from_dir(FIXTURES_DIR).files
    stale = sorted(n for n in ds.files if committed.get(n) != ds.files[n])
    extra = sorted(set(committed) - set(ds.files))
    assert not stale and not extra, (
        f"data/fixtures is out of date (stale: {stale}, unexpected: {extra}). Run `make data` "
        "(and bump DATA_VERSION if the content changed)."
    )


@pytest.mark.req("FOUND-1")
def test_dataset_is_stamped_with_data_version(ds: Dataset) -> None:
    assert ds.manifest["data_version"] == DATA_VERSION
    assert ds.answer_key["data_version"] == DATA_VERSION


@pytest.mark.req("FOUND-1")
def test_make_data_refuses_changed_output_without_a_version_bump(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--out", str(tmp_path)]) == 0
    manifest_path = tmp_path / "manifest.json"
    # Pretend the committed data was produced by different generator code, same version.
    old = json.loads(manifest_path.read_text("utf-8"))
    old["dataset_sha256"] = "0" * 64
    old["files"]["reviews.jsonl"] = "0" * 64
    manifest_path.write_text(json.dumps(old), "utf-8")
    before = manifest_path.read_bytes()

    assert main(["--out", str(tmp_path)]) == 2
    assert "Bump DATA_VERSION" in capsys.readouterr().err
    assert manifest_path.read_bytes() == before  # nothing was overwritten

    # Once the old data is an older version, regenerating is allowed.
    old["data_version"] = DATA_VERSION - 1
    manifest_path.write_text(json.dumps(old), "utf-8")
    assert main(["--out", str(tmp_path)]) == 0
