"""Row 18a, part 2 (triage M8): CI scanned five roots by hand and left out `data/enrichment`, which
holds model output, and `data/embeddings`. Now CI and `make scan-cassettes` scan one list, the
CLI's default. Failing first: on `81683c2` the CI step names its own paths."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from northwind_llm_replay.cli import EVAL_ROOTS

pytestmark = [pytest.mark.req("NFR-02"), pytest.mark.req("FOUND-2")]

REPO = Path(__file__).resolve().parents[3]


def scan_commands(text: str) -> list[list[str]]:
    lines = [line.strip() for line in text.splitlines()]
    return [
        shlex.split(line.split("northwind_llm_replay.cli scan", 1)[1].split("#")[0])
        for line in lines
        if "northwind_llm_replay.cli scan" in line and not line.startswith("#")
    ]


def test_ci_scans_the_cli_default_list() -> None:
    commands = scan_commands((REPO / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    assert commands == [[]], commands  # one scan step, no paths of its own


def test_make_scan_cassettes_scans_the_cli_default_list() -> None:
    makefile = (REPO / "Makefile").read_text("utf-8")
    recipe = re.search(r"^scan-cassettes:.*?\n((?:\t.*\n)+)", makefile, re.M)
    assert recipe is not None
    [command] = [line for line in recipe.group(1).splitlines() if "cli scan" in line]
    assert command.rstrip().endswith('--cassettes-dir "$$CASSETTES_DIR"}')  # only a folder swap


@pytest.mark.parametrize("root", ["data/enrichment", "data/embeddings", "evals/contexts"])
def test_the_default_list_holds_the_model_output_and_vector_folders(root: str) -> None:
    assert Path(root) in EVAL_ROOTS
