"""RAG-02: what `make enrich`, `make ingest-enriched` and `make eval-enrichment-report` start, with
`uv` replaced by a shim that reports its environment. Nothing real runs, so this spends nothing."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("RAG-02")

REPO = Path(__file__).resolve().parents[3]
SHIM = '#!/bin/sh\necho "uv LLM_MODE=$LLM_MODE $*"\n'


def uv_calls(tmp_path: Path, target: str, *args: str, mode: str | None = None) -> list[str]:
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text())
    shim = tmp_path / "bin" / "uv"
    shim.parent.mkdir()
    shim.write_text(SHIM)
    shim.chmod(0o755)
    env = {"PATH": f"{shim.parent}{os.pathsep}/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    if mode is not None:
        env["LLM_MODE"] = mode
    out = subprocess.run(  # noqa: S603
        ["make", "-s", target, "PNPM=true", *args],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout
    return [line for line in out.splitlines() if line.startswith("uv ")]


ENRICH = ["uv LLM_MODE=live run python -m northwind_evals.enrich"]


def test_enrich_runs_the_enrich_module_in_live_mode(tmp_path: Path) -> None:
    assert uv_calls(tmp_path, "enrich") == ENRICH


@pytest.mark.parametrize("mode", ["replay", "record", "live", ""])
def test_enrich_is_live_whatever_the_shell_says(tmp_path: Path, mode: str) -> None:
    assert uv_calls(tmp_path, "enrich", mode=mode) == ENRICH


@pytest.mark.parametrize("mode", ["replay", "record", "mock"])
def test_enrich_is_live_whatever_make_is_told(tmp_path: Path, mode: str) -> None:
    assert uv_calls(tmp_path, "enrich", f"LLM_MODE={mode}") == ENRICH


@pytest.mark.parametrize(
    ("target", "command"),
    [
        ("ingest-enriched", "run python -m northwind_rag.cli --fixtures data/fixtures --enriched"),
        ("eval-enrichment-report", "run python -m northwind_evals.cli enrichment-report"),
    ],
)
def test_the_no_spend_targets_set_no_spending_mode(
    tmp_path: Path, target: str, command: str
) -> None:
    unset = tmp_path / "unset"
    unset.mkdir()
    assert uv_calls(unset, target) == [f"uv LLM_MODE= {command}"]
    replay = tmp_path / "replay"
    replay.mkdir()
    assert uv_calls(replay, target, mode="replay") == [f"uv LLM_MODE=replay {command}"]


@pytest.mark.parametrize("target", ["ingest-enriched", "eval-enrichment-report"])
def test_the_no_spend_targets_do_not_name_a_spending_mode(tmp_path: Path, target: str) -> None:
    [call] = uv_calls(tmp_path, target)
    assert "live" not in call and "record" not in call
    text = (REPO / "Makefile").read_text()
    recipe = text.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
    assert "LLM_MODE=live" not in recipe and "LLM_MODE=record" not in recipe


def test_only_the_enrich_recipe_gains_a_live_mode() -> None:
    """Besides the old spending targets, `LLM_MODE=live` appears in exactly one recipe."""
    lines = [
        line
        for line in (REPO / "Makefile").read_text().splitlines()
        if line.startswith("\t") and "LLM_MODE=live" in line
    ]
    assert lines == ["\tLLM_MODE=live uv run python -m northwind_evals.enrich"]
