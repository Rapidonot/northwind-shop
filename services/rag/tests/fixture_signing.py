"""Re-sign an edited copy of the fixtures (#10b, Q4; the user's stop-5 decision, 2026-11-13).

Since #10b ingest verifies the dataset first, a test that changes data on purpose must re-sign its
copy to reach chunking, diffs or retrieval. Only such tests use this: `test_index.py` and
`test_retrieve_context_isolation.py`. It never touches the repository's own `data/fixtures`: a
target that resolves there is refused before anything is read or written (fail closed).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_FIXTURES = (Path(__file__).resolve().parents[3] / "data" / "fixtures").resolve()


class RefusedTarget(Exception):
    """The target is the repository's own fixtures, which are never re-signed."""


def rehash(fixtures: Path) -> Path:
    """Re-sign an edited fixtures copy: each listed file's hash and `dataset_sha256`, by the
    generator's formula (`data/src/northwind_data/generate.py`). Returns the same path."""
    target = Path(fixtures).resolve()
    if target == REPO_FIXTURES or REPO_FIXTURES in target.parents:
        raise RefusedTarget("the repository's own data/fixtures are never re-signed")
    path = target / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    files = {
        name: hashlib.sha256((target / name).read_bytes()).hexdigest()
        for name in sorted(manifest["files"])
    }
    manifest["files"] = files
    manifest["dataset_sha256"] = hashlib.sha256(
        "".join(f"{n} {h}\n" for n, h in files.items()).encode()
    ).hexdigest()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return Path(fixtures)
