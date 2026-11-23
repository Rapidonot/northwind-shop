"""DEV-1 (decision 0020): CI triggers, job timeouts, concurrency and where the xdist flags go.

Cheap YAML and text parsing, plus two `make` runs against a `uv` shim, so these run in `make test`
and are not marked `ship`. The workflow files are read as data; nothing here starts a job.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.req("DEV-1")

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
PAR = "-n auto --dist loadgroup"
# Any spelling of an xdist option: `-n4`, `-n 4`, `-nauto`, `--numprocesses`, `--dist=load`, `--tx`,
# `-p xdist` (security review L2).
WORKER_FLAG = re.compile(r"(?:^|\s)(?:-n\S*|--numprocesses\b|--dist\b|--tx\b|-p\s*xdist)")
GROUP = (
    "${{ github.workflow }}-${{ github.ref }}-"
    "${{ github.event.action == 'synchronize' && 'push' || github.run_id }}"
)
TIMEOUTS = {
    "ci.yml": {
        "python": 35,
        "python-integration": 40,
        "eval-gate": 10,
        "e2e": 10,
        "portal": 5,
    },
    "guard.yml": {
        "baseline-label": 5,
        "eval-integrity-main": 5,
        "eval-gate-main": 10,
    },
}


def load(name: str) -> dict[Any, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    assert isinstance(data, dict)
    return data


def test_ci_runs_on_pull_request_and_pushes_to_main_only() -> None:
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = load("ci.yml")[True]
    assert triggers == {"pull_request": None, "push": {"branches": ["main"]}}


@pytest.mark.parametrize("name", sorted(TIMEOUTS))
def test_every_job_has_the_planned_timeout(name: str) -> None:
    jobs = load(name)["jobs"]
    assert {job: body.get("timeout-minutes") for job, body in jobs.items()} == TIMEOUTS[name]


@pytest.mark.parametrize("name", sorted(TIMEOUTS))
def test_no_job_lacks_a_timeout(name: str) -> None:
    """A job added later without `timeout-minutes` falls back to the 360-minute default."""
    jobs = load(name)["jobs"]
    assert {j for j, body in jobs.items() if "timeout-minutes" in body} == set(jobs)


@pytest.mark.parametrize("name", sorted(TIMEOUTS))
def test_concurrency_cancels_only_superseded_synchronize_runs(name: str) -> None:
    workflow = load(name)
    assert workflow["concurrency"] == {"group": GROUP, "cancel-in-progress": True}
    for job, body in workflow["jobs"].items():
        assert "concurrency" not in body, job


def make_targets() -> dict[str, list[str]]:
    """Recipe lines (tab-indented) per target, in file order."""
    targets: dict[str, list[str]] = {}
    current: str | None = None
    for line in (REPO / "Makefile").read_text().splitlines():
        header = re.match(r"^([A-Za-z0-9_.%-]+(?:\s+[A-Za-z0-9_.%-]+)*)\s*:(?!=)", line)
        if header and not line.startswith("\t"):
            current = header.group(1).split()[0]
            targets.setdefault(current, [])
        elif line.startswith("\t") and current:
            targets[current].append(line)
        elif line and not line.startswith(("#", " ")):
            current = None
    return targets


def test_pytest_par_is_defined_once() -> None:
    """`override`, so no `make ci PYTEST_PAR=…` can shrink the run (security review L3)."""
    text = (REPO / "Makefile").read_text()
    assert re.findall(r"^(?!\s*#).*\bPYTEST_PAR\s*[:?+]?=.*$", text, re.M) == [
        f"override PYTEST_PAR := {PAR}"
    ]


def test_only_test_and_check_recipes_use_the_parallel_flags() -> None:
    using = {
        target
        for target, lines in make_targets().items()
        if any("PYTEST_PAR" in line for line in lines)
    }
    assert using == {"test", "check"}
    for target, lines in make_targets().items():
        for line in lines:
            if target in ("test", "check"):
                continue
            for needle in ("PYTEST_PAR", "xdist"):
                assert needle not in line, f"{target}: {line}"
            if "pytest" in line:  # a shell `[[ -n "$x" ]]` elsewhere is not a worker count
                assert not WORKER_FLAG.search(line), f"{target}: {line}"


def run_make(tmp_path: Path, *args: str) -> list[str]:
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text())
    shim = tmp_path / "bin" / "uv"
    shim.parent.mkdir()
    shim.write_text('#!/bin/sh\necho "uv $*"\n')
    shim.chmod(0o755)
    env = {"PATH": f"{shim.parent}{os.pathsep}/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    done = subprocess.run(  # noqa: S603
        ["make", "-s", *args],  # noqa: S607
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return [ln for ln in done.stdout.splitlines() if ln.startswith("uv run pytest")]


def test_make_test_runs_pytest_in_parallel(tmp_path: Path) -> None:
    calls = run_make(tmp_path, "test", "PNPM=true")
    assert len(calls) == 1
    assert calls[0] == f"uv run pytest {PAR}"


def test_make_test_integration_stays_serial(tmp_path: Path) -> None:
    calls = run_make(tmp_path, "test-integration")
    assert calls == ["uv run pytest -m integration"]


def pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO / "pyproject.toml").read_text())


def test_addopts_carries_no_worker_flags() -> None:
    """Flags in `addopts` would reach `make record` and every nested pytest run."""
    addopts = pyproject()["tool"]["pytest"]["ini_options"]["addopts"]
    joined = " ".join(addopts) if isinstance(addopts, list) else str(addopts)
    assert not WORKER_FLAG.search(joined), joined
    assert "xdist" not in joined


@pytest.mark.parametrize(
    "spelling",
    ["-n4", "-n 4", "-nauto", "--numprocesses=2", "--dist=load", "--tx popen", "-p xdist"],
)
def test_the_worker_flag_pattern_catches_every_spelling(spelling: str) -> None:
    assert WORKER_FLAG.search(f"uv run pytest {spelling}")


@pytest.mark.parametrize(
    "line",
    ["uv run pytest -m integration", 'uv run pytest -m "" --llm-suite "$$SUITE"', "uv run pytest"],
)
def test_the_worker_flag_pattern_leaves_other_options_alone(line: str) -> None:
    assert not WORKER_FLAG.search(line)


def test_xdist_is_a_dev_dependency() -> None:
    dev = pyproject()["dependency-groups"]["dev"]
    assert any(re.match(r"pytest-xdist\b", dep) for dep in dev)
