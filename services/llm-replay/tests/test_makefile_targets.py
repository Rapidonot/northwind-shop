"""What `make test` and `make ci` actually run, with `uv` replaced by a shim that reports its
environment (FOUND-2 review #2, #13). Nothing real runs, so this spends nothing."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
KEY_FILE = "." + "env"
SHIM = '#!/bin/sh\necho "uv LLM_MODE=$LLM_MODE $*"\n'


def make_uv_calls(
    tmp_path: Path, target: str, *args: str, env: dict[str, str] | None = None
) -> list[str]:
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text())
    shim = tmp_path / "bin" / "uv"
    shim.parent.mkdir()
    shim.write_text(SHIM)
    shim.chmod(0o755)
    out = subprocess.run(  # noqa: S603
        ["make", "-s", target, "PNPM=true", *args],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": f"{shim.parent}{os.pathsep}/usr/bin:/bin:/usr/local/bin",
            "HOME": str(tmp_path),
            **(env or {}),
        },
    ).stdout
    return [line for line in out.splitlines() if line.startswith("uv ")]


@pytest.mark.req("FOUND-2")
@pytest.mark.parametrize(("target", "runs"), [("test", 1), ("test-integration", 1), ("ci", 2)])
@pytest.mark.parametrize("source", ["shell", "command-line", "key-file"])
def test_test_targets_always_run_pytest_in_replay(
    tmp_path: Path, target: str, runs: int, source: str
) -> None:
    """Review #2: a spending LLM_MODE from any of the three places must not reach pytest."""
    env: dict[str, str] = {}
    args: list[str] = []
    if source == "shell":
        env["LLM_MODE"] = "record"
    elif source == "command-line":
        args.append("LLM_MODE=record")
    else:
        (tmp_path / KEY_FILE).write_text("LLM_MODE=record\n")
    calls = make_uv_calls(tmp_path, target, *args, env=env)
    pytest_calls = [c for c in calls if " run pytest" in c]
    assert len(pytest_calls) == runs
    assert all(c.startswith("uv LLM_MODE=replay ") for c in pytest_calls)


@pytest.mark.req("FOUND-2")
def test_ci_runs_the_cassette_scan_before_the_tests_like_github(tmp_path: Path) -> None:
    """Review #13: `ci.yml` scans cassettes, so `make ci` has to as well."""
    calls = make_uv_calls(tmp_path, "ci")
    scan = [i for i, c in enumerate(calls) if "northwind_llm_replay.cli scan" in c]
    tests = [i for i, c in enumerate(calls) if " run pytest" in c]
    assert len(scan) == 1
    assert scan[0] < tests[0]
