"""FOUND-1: the answer key and live tables are refused by the ingest guard RAG-01 must use."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from northwind_data.dataset import FIXTURES_DIR
from northwind_data.ingest_guard import (
    DatasetMismatchError,
    NotIngestibleError,
    assert_ingestible,
    ingestible_files,
    load_manifest,
    verify_dataset,
)


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


# ---- Q4 (#10b): `verify_dataset` -----------------------------------------------------------------

MANIFEST = json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))
LISTED = sorted(MANIFEST["files"])
MARKER = "TAMPERMARKER7c1e"


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    target = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_DIR, target)
    return target


def edit_manifest(directory: Path, change: object) -> None:
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    change(manifest)  # type: ignore[operator]
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


@pytest.mark.req("RAG-01")
@pytest.mark.req("CC-10")
def test_the_real_fixtures_verify_and_return_the_manifest_hash() -> None:
    assert verify_dataset(FIXTURES_DIR) == MANIFEST["dataset_sha256"]
    assert verify_dataset(str(FIXTURES_DIR)) == MANIFEST["dataset_sha256"]  # type: ignore[arg-type]


@pytest.mark.req("RAG-01")
@pytest.mark.req("CC-10")
def test_every_manifest_file_is_listed_for_the_guard() -> None:
    named = [*MANIFEST["ingestible"], *MANIFEST["live_tables"], *MANIFEST["config"]]
    assert named and set(named) <= set(MANIFEST["files"])
    assert "_answer_key.json" in MANIFEST["files"]


@pytest.mark.req("RAG-01")
@pytest.mark.req("CC-10")
@pytest.mark.parametrize("name", LISTED)
def test_one_changed_byte_in_any_listed_file_is_refused_by_name(copy: Path, name: str) -> None:
    path = copy / name
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    with pytest.raises(DatasetMismatchError) as caught:
        verify_dataset(copy)
    assert name in str(caught.value)


@pytest.mark.req("RAG-01")
@pytest.mark.req("CC-10")
def test_the_message_never_carries_file_content(copy: Path) -> None:
    with (copy / "products.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(MARKER + "\n")
    with pytest.raises(DatasetMismatchError) as caught:
        verify_dataset(copy)
    assert MARKER not in str(caught.value) and "products.jsonl" in str(caught.value)


@pytest.mark.req("RAG-01")
@pytest.mark.req("CC-10")
def test_a_wrong_dataset_hash_is_refused(copy: Path) -> None:
    edit_manifest(copy, lambda m: m.__setitem__("dataset_sha256", "0" * 64))
    with pytest.raises(DatasetMismatchError, match="dataset_sha256"):
        verify_dataset(copy)


@pytest.mark.req("RAG-01")
@pytest.mark.req("CC-10")
@pytest.mark.parametrize("bad", [None, 7, "", "abc"])
def test_a_missing_or_malformed_dataset_hash_is_refused(copy: Path, bad: object) -> None:
    def change(m: dict[str, object]) -> None:
        if bad is None:
            del m["dataset_sha256"]
        else:
            m["dataset_sha256"] = bad

    edit_manifest(copy, change)
    with pytest.raises(DatasetMismatchError):
        verify_dataset(copy)


@pytest.mark.req("RAG-01")
@pytest.mark.req("CC-10")
@pytest.mark.parametrize("name", LISTED)
def test_a_missing_listed_file_is_refused_by_name(copy: Path, name: str) -> None:
    (copy / name).unlink()
    with pytest.raises(DatasetMismatchError) as caught:
        verify_dataset(copy)
    assert name in str(caught.value)


@pytest.mark.req("RAG-01")
@pytest.mark.req("CC-10")
def test_a_missing_manifest_or_directory_is_refused(copy: Path, tmp_path: Path) -> None:
    (copy / "manifest.json").unlink()
    with pytest.raises(DatasetMismatchError):
        verify_dataset(copy)
    with pytest.raises(DatasetMismatchError):
        verify_dataset(tmp_path / "nowhere")


@pytest.mark.req("RAG-01")
@pytest.mark.req("CC-10")
@pytest.mark.parametrize(
    "name",
    [
        "../products.jsonl",
        "/etc/hosts",
        "sub/../products.jsonl",
        "..",
        "a/b.jsonl",
        "",
        ".hidden/..",
    ],
)
def test_a_bad_file_name_in_files_is_refused(copy: Path, tmp_path: Path, name: str) -> None:
    (tmp_path / "products.jsonl").write_bytes((copy / "products.jsonl").read_bytes())
    digest = MANIFEST["files"]["products.jsonl"]
    edit_manifest(copy, lambda m: m["files"].__setitem__(name, digest))
    with pytest.raises(DatasetMismatchError):
        verify_dataset(copy)


@pytest.mark.req("RAG-01")
@pytest.mark.req("CC-10")
def test_a_symlinked_file_is_refused_even_with_identical_bytes(copy: Path, tmp_path: Path) -> None:
    real = tmp_path / "elsewhere.jsonl"
    real.write_bytes((copy / "documents.jsonl").read_bytes())
    (copy / "documents.jsonl").unlink()
    (copy / "documents.jsonl").symlink_to(real)
    with pytest.raises(DatasetMismatchError, match=r"documents\.jsonl"):
        verify_dataset(copy)


@pytest.mark.req("RAG-01")
@pytest.mark.req("CC-10")
@pytest.mark.parametrize("group", ["ingestible", "live_tables", "config"])
def test_a_file_named_for_ingest_live_or_config_but_not_hashed_is_refused(
    copy: Path, group: str
) -> None:
    victim = MANIFEST[group][0]

    def change(m: dict[str, object]) -> None:
        files: dict[str, str] = m["files"]  # type: ignore[assignment]
        del files[victim]

    edit_manifest(copy, change)
    with pytest.raises(DatasetMismatchError):
        verify_dataset(copy)


@pytest.mark.req("RAG-01")
@pytest.mark.req("CC-10")
def test_a_new_file_added_to_ingestible_without_a_hash_is_refused(copy: Path) -> None:
    (copy / "extra.jsonl").write_text("{}\n", encoding="utf-8")
    edit_manifest(copy, lambda m: m["ingestible"].append("extra.jsonl"))
    with pytest.raises(DatasetMismatchError):
        verify_dataset(copy)
    assert "extra.jsonl" in load_manifest(copy)["ingestible"]


# ---- re-review N1 (#10b): a FIFO is refused at once, never blocks the read ----------------------


@pytest.mark.req("CC-10")
def test_a_fifo_in_place_of_a_listed_file_is_refused_without_blocking(tmp_path: Path) -> None:
    import os
    import threading

    from northwind_data.ingest_guard import DatasetMismatchError, verify_dataset

    copy = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_DIR, copy)
    (copy / "stock.jsonl").unlink()
    os.mkfifo(copy / "stock.jsonl")
    outcome: list[BaseException | str] = []

    def run() -> None:
        try:
            outcome.append(verify_dataset(copy))
        except BaseException as error:  # noqa: BLE001 - recorded for the assertion
            outcome.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "verify_dataset blocked on a FIFO"
    [result] = outcome
    assert isinstance(result, DatasetMismatchError)
    assert "stock.jsonl" in str(result)
