"""What RAG-01 may ingest from data/fixtures/ (FOUND-1).

Rules, any one of which refuses a file:
- the file name, or the directory it sits in, starts with "_" (e.g. `_answer_key.json`);
- it is a live table (prices, stock): those are served by tools only (hard rule 2);
- the content carries the `"never_ingest":true` marker (catches a renamed answer key);
- it is not listed in the fixtures' `manifest.json["ingestible"]`.
RAG-01 must call `assert_ingestible` on every file before reading it.

`verify_dataset` (#10b, Q4) recomputes `dataset_sha256` from the bytes on disk, the way the
generator computes it, and raises on any mismatch. Ingest calls it before it reads anything, and
mcp-catalog before it seeds its live store.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

LIVE_TABLES: frozenset[str] = frozenset({"prices.jsonl", "stock.jsonl"})
NEVER_INGEST_MARKER = '"never_ingest":true'


class NotIngestibleError(Exception):
    pass


class DatasetMismatchError(Exception):
    """The fixtures on disk are not the dataset their manifest describes. The message names a file
    or the manifest field, never content."""


_FILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")


def load_manifest(fixtures_dir: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((fixtures_dir / "manifest.json").read_text("utf-8"))
    return data


def assert_ingestible(path: Path, manifest: dict[str, Any] | None = None) -> None:
    path = Path(path)
    if path.name.startswith("_") or path.parent.name.startswith("_"):
        raise NotIngestibleError(f"{path}: names starting with '_' are never ingested")
    if path.name in LIVE_TABLES:
        raise NotIngestibleError(f"{path}: live table, served by tools only (hard rule 2)")
    if NEVER_INGEST_MARKER in path.read_text("utf-8").replace(" ", "").replace("\n", ""):
        raise NotIngestibleError(f"{path}: content is marked never_ingest")
    m = manifest if manifest is not None else load_manifest(path.parent)
    if path.name not in m["ingestible"]:
        raise NotIngestibleError(f"{path}: not listed as ingestible in manifest.json")


def ingestible_files(fixtures_dir: Path) -> list[Path]:
    manifest = load_manifest(fixtures_dir)
    files = [fixtures_dir / name for name in sorted(manifest["ingestible"])]
    for f in files:
        assert_ingestible(f, manifest)
    return files


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class VerifiedDataset:
    """The verified hash and the exact bytes that were verified, so a caller parses what was
    checked, not a second read of the files (security review L2, #10b)."""

    dataset_sha256: str
    files: Mapping[str, bytes]


def _read_regular(path: Path) -> bytes:
    """The bytes of a regular file, opened once without following a symlink in the last path
    component, so the check and the read are the same open file. `O_NONBLOCK` makes a FIFO's open
    return at once, so `fstat` refuses it instead of the open blocking forever (re-review N1)."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        # A regular file: back to blocking reads, so no filesystem can return a short `EAGAIN`
        # read (re-review pass 3, unconfirmed item 1).
        os.set_blocking(fd, True)
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd >= 0:
            os.close(fd)


def load_verified(fixtures_dir: Path) -> VerifiedDataset:
    """Every file the manifest lists, read once, each hashing to the manifest's value, and the
    dataset hash recomputed from them (the generator's formula, `generate.py`). Every file the
    manifest names as ingestible, live or config must be listed. A file is hashed as bytes and
    never parsed here, so the answer key is read but not interpreted.

    This proves the listed files' bytes on disk match their manifest. It does not authenticate the
    manifest: a re-signed manifest passes, and the manifest's own fields (`data_version`,
    `ingestible`, ...) are not covered by the hash (re-review N7). The anchor against that is CI's
    `data/tests/test_fixtures_current.py::test_committed_fixtures_match_the_generator`, over the
    committed fixtures (security review L1, #10b). Raises `DatasetMismatchError`."""
    fixtures_dir = Path(fixtures_dir)
    try:
        manifest = json.loads(_read_regular(fixtures_dir / "manifest.json").decode("utf-8"))
    except (OSError, ValueError):
        raise DatasetMismatchError("manifest.json is missing or unreadable") from None
    files = manifest.get("files") if isinstance(manifest, dict) else None
    expected = manifest.get("dataset_sha256") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not files or not isinstance(expected, str):
        raise DatasetMismatchError("manifest.json has no files or dataset_sha256")
    named = [
        *manifest.get("ingestible", []),
        *manifest.get("live_tables", []),
        *manifest.get("config", []),
    ]
    for name in named:
        if not isinstance(name, str) or name not in files:
            raise DatasetMismatchError("manifest.json names a file it does not hash")
    hashes: dict[str, str] = {}
    contents: dict[str, bytes] = {}
    for name in sorted(files):
        digest = files[name]
        if not isinstance(name, str) or not _FILE_NAME.fullmatch(name) or ".." in name:
            raise DatasetMismatchError("manifest.json lists a file outside the fixtures")
        try:
            data = _read_regular(fixtures_dir / name)
        except OSError:
            raise DatasetMismatchError(f"{name}: missing or not a regular file") from None
        actual = _sha256(data)
        if not isinstance(digest, str) or actual != digest:
            raise DatasetMismatchError(f"{name}: hash does not match manifest.json")
        hashes[name] = actual
        contents[name] = data
    recomputed = _sha256("".join(f"{n} {h}\n" for n, h in hashes.items()).encode())
    if recomputed != expected:
        raise DatasetMismatchError("dataset_sha256 does not match the listed files")
    return VerifiedDataset(expected, MappingProxyType(contents))


def verify_dataset(fixtures_dir: Path) -> str:
    """The manifest's `dataset_sha256`, once `load_verified` has checked every listed file.
    Raises `DatasetMismatchError`."""
    return load_verified(fixtures_dir).dataset_sha256
