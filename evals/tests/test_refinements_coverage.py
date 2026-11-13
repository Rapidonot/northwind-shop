"""Row #13b: the scanner reads `evals/contexts/refinements.ndjson`, whatever its suffix.

The user approved the `.ndjson` name on the condition that what guards `evals/contexts` also guards
this file. `make scan-cassettes` walks `EVAL_ROOTS` (every file under `evals/contexts`), and this
test holds that for the refinements file by its real path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_evals import cli
from northwind_llm_replay.cli import EVAL_ROOTS
from northwind_llm_replay.scan import load_canaries, scan_paths

pytestmark = pytest.mark.req("NFR-03")

REPO = Path(__file__).resolve().parents[2]


def test_the_refinements_file_is_under_a_scanned_root() -> None:
    assert cli.REFINEMENTS == Path("evals/contexts/refinements.ndjson")
    assert any(cli.REFINEMENTS.is_relative_to(root) for root in EVAL_ROOTS)


def test_the_scanner_finds_a_canary_in_the_refinements_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = sorted(load_canaries(REPO, required=True))[0]
    monkeypatch.chdir(tmp_path)  # EVAL_ROOTS are relative to the checkout
    cli.REFINEMENTS.parent.mkdir(parents=True)
    record = {"item_id": "EV-1", "n": 1, "query": "q", "chunks": [{"text": f"x {canary} y"}]}
    cli.REFINEMENTS.write_text(json.dumps(record) + "\n", encoding="utf-8")
    findings = scan_paths(EVAL_ROOTS, canaries=frozenset({canary}))
    assert [f.path for f in findings] == [str(cli.REFINEMENTS)]
    assert canary not in " ".join(str(f) for f in findings)  # never printed
