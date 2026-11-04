"""What RAG-01 may ingest from data/fixtures/ (FOUND-1).

Rules, any one of which refuses a file:
- the file name, or the directory it sits in, starts with "_" (e.g. `_answer_key.json`);
- it is a live table (prices, stock): those are served by tools only (hard rule 2);
- the content carries the `"never_ingest":true` marker (catches a renamed answer key);
- it is not listed in the fixtures' `manifest.json["ingestible"]`.
RAG-01 must call `assert_ingestible` on every file before reading it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LIVE_TABLES: frozenset[str] = frozenset({"prices.jsonl", "stock.jsonl"})
NEVER_INGEST_MARKER = '"never_ingest":true'


class NotIngestibleError(Exception):
    pass


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
