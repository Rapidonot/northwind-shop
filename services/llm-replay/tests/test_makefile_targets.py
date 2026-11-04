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


@pytest.mark.req("CC-27")
@pytest.mark.parametrize(
    "suite",
    [
        "x; touch injected",
        "x`touch injected`",
        "x && touch injected",
        "",
    ],
)
def test_record_refuses_a_suite_that_is_not_a_name(tmp_path: Path, suite: str) -> None:
    """SUITE must not reach a shell. `$(shell ...)` is make's own, and the run is sandboxed."""
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text())
    done = subprocess.run(  # noqa: S603
        ["make", "-s", "record", f"SUITE={suite}"],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)},
    )
    assert done.returncode != 0
    assert "usage: make record" in done.stdout
    assert not (tmp_path / "injected").exists()


@pytest.mark.req("CC-27")
def test_record_passes_a_plain_suite_name(tmp_path: Path) -> None:
    calls = make_uv_calls(tmp_path, "record", "SUITE=eval-answers_2")
    # The shim prints `-m ""` as `-m` and an empty word, hence the two spaces.
    assert calls == ["uv LLM_MODE=record run pytest -m  --llm-suite eval-answers_2"]


@pytest.mark.req("CC-27")
@pytest.mark.parametrize(
    "size",
    [
        "x; touch injected",
        "x`touch injected`",
        "x && touch injected",
        "1; touch injected",
        "1\ntouch injected",
        "all",
        "-1",
        "",
    ],
)
def test_eval_live_refuses_a_slice_that_is_not_digits(tmp_path: Path, size: str) -> None:
    """EVAL_SLICE must not reach a shell. `$(shell ...)` is make's own, and the run is sandboxed."""
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text())
    shim = tmp_path / "bin" / "uv"
    shim.parent.mkdir()
    shim.write_text(SHIM)
    shim.chmod(0o755)
    done = subprocess.run(  # noqa: S603
        ["make", "-s", "eval-live", f"EVAL_SLICE={size}"],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{shim.parent}{os.pathsep}/usr/bin:/bin:/usr/local/bin",
            "HOME": str(tmp_path),
        },
    )
    assert done.returncode != 0
    assert "usage: make eval-live" in done.stdout
    assert "uv " not in done.stdout  # refused before anything that could spend
    assert not (tmp_path / "injected").exists()


@pytest.mark.req("CC-27")
def test_eval_live_passes_a_digit_slice(tmp_path: Path) -> None:
    calls = make_uv_calls(tmp_path, "eval-live", "EVAL_SLICE=20")
    assert calls == ["uv LLM_MODE=record run python -m northwind_evals.cli run --slice 20"]


@pytest.mark.req("CC-27")
@pytest.mark.parametrize("override", [None, "/elsewhere"])
def test_model_cache_is_stable_and_overridable(tmp_path: Path, override: str | None) -> None:
    """Claude's sandbox has another temp dir, so the cache cannot be fastembed's default."""
    probe = "\ncachepath:\n\t@echo cache=$$FASTEMBED_CACHE_PATH\n"
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text() + probe)
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    if override:
        env["FASTEMBED_CACHE_PATH"] = override
    out = subprocess.run(  # noqa: S603
        ["make", "-s", "cachepath"],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout
    assert out.strip() == f"cache={override or tmp_path / '.cache' / 'fastembed'}"


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
@pytest.mark.parametrize("target", ["up", "down"])
@pytest.mark.parametrize("has_key_file", [False, True])
def test_compose_gets_the_env_file_only_when_it_exists(
    tmp_path: Path, target: str, has_key_file: bool
) -> None:
    """Review #12: compose exits 1 on a missing --env-file, and linked worktrees never have one."""
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text())
    if has_key_file:
        (tmp_path / KEY_FILE).write_text("POSTGRES_DB=northwind\n")
    shim = tmp_path / "bin" / "docker"
    shim.parent.mkdir()
    shim.write_text('#!/bin/sh\necho "docker $*"\n')
    shim.chmod(0o755)
    path = f"{shim.parent}{os.pathsep}/usr/bin:/bin:/usr/local/bin"
    out = subprocess.run(  # noqa: S603
        ["make", "-s", target],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": path, "HOME": str(tmp_path)},
    ).stdout
    env_file = f"--env-file {KEY_FILE} " if has_key_file else ""
    verb = "up -d --wait" if target == "up" else "down"
    assert out.strip() == f"docker compose {env_file}-f infra/docker-compose.yml {verb}"


@pytest.mark.req("FOUND-2")
def test_ci_runs_the_cassette_scan_before_the_tests_like_github(tmp_path: Path) -> None:
    """Review #13: `ci.yml` scans cassettes, so `make ci` has to as well."""
    calls = make_uv_calls(tmp_path, "ci")
    scan = [i for i, c in enumerate(calls) if "northwind_llm_replay.cli scan" in c]
    tests = [i for i, c in enumerate(calls) if " run pytest" in c]
    assert len(scan) == 1
    assert scan[0] < tests[0]
