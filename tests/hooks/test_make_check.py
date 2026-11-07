"""AUTO-2, item 7c: the `make check` recipe, run with `uv` and `pnpm` replaced by shims.

test_changed_tests.py covers which tests a path maps to. This covers what the recipe does with the
plan: lint first, one replay pytest call for the Python paths, the portal tests only when named, and
everything through `make test` when the plan is `all`. Nothing real runs, so this spends nothing.
Also pins `tools/finish.sh` as a runner-sync path, because finish reaches the clone only that way.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("AUTO-2")

REPO = Path(__file__).resolve().parents[2]
# Prints $PLAN for the planner, exits non-zero when PLAN_FAIL is set, and logs every other call.
UV_SHIM = """#!/bin/sh
case "$*" in
  *changed_tests.py*)
    [ -n "$PLAN_FAIL" ] && exit 1
    printf '%s\\n' "$PLAN"
    exit 0;;
esac
echo "uv LLM_MODE=$LLM_MODE $*"
"""


def run_check(
    tmp_path: Path, plan: str, *args: str, fail_planner: bool = False
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text())
    shim = tmp_path / "bin" / "uv"
    shim.parent.mkdir()
    shim.write_text(UV_SHIM)
    shim.chmod(0o755)
    env = {
        "PATH": f"{shim.parent}{os.pathsep}/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "PLAN": plan,
    }
    if fail_planner:
        env["PLAN_FAIL"] = "1"
    done = subprocess.run(  # noqa: S603
        ["make", "-s", "check", "PNPM=echo pnpm", *args],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    calls = [ln for ln in done.stdout.splitlines() if ln.startswith(("uv ", "pnpm "))]
    return done, calls


def pytest_calls(calls: list[str]) -> list[str]:
    return [c for c in calls if " run pytest" in c]


def test_lint_runs_before_any_test(tmp_path: Path) -> None:
    done, calls = run_check(tmp_path, "tests/hooks")
    assert done.returncode == 0, done.stderr
    first_pytest = next(i for i, c in enumerate(calls) if " run pytest" in c)
    assert any("ruff check" in c for c in calls[:first_pytest])
    assert any("mypy" in c for c in calls[:first_pytest])


def test_python_paths_run_as_one_replay_pytest_call(tmp_path: Path) -> None:
    done, calls = run_check(tmp_path, "tests/hooks tests/ship")
    assert done.returncode == 0, done.stderr
    assert pytest_calls(calls) == ["uv LLM_MODE=replay run pytest tests/hooks tests/ship"]
    assert "pnpm -r test" not in calls


def test_the_portal_alone_runs_vitest_and_no_pytest(tmp_path: Path) -> None:
    done, calls = run_check(tmp_path, "portal")
    assert done.returncode == 0, done.stderr
    assert pytest_calls(calls) == []
    assert "pnpm -r test" in calls


def test_python_and_portal_paths_run_both(tmp_path: Path) -> None:
    done, calls = run_check(tmp_path, "evals portal")
    assert done.returncode == 0, done.stderr
    assert pytest_calls(calls) == ["uv LLM_MODE=replay run pytest evals"]
    assert "pnpm -r test" in calls


def test_an_empty_plan_says_so_and_runs_no_tests(tmp_path: Path) -> None:
    done, calls = run_check(tmp_path, "")
    assert done.returncode == 0, done.stderr
    assert "make check: no tests for these paths" in done.stdout
    assert pytest_calls(calls) == []
    assert "pnpm -r test" not in calls


def test_the_plan_all_runs_the_whole_suite_through_make_test(tmp_path: Path) -> None:
    done, calls = run_check(tmp_path, "all")
    assert done.returncode == 0, done.stderr
    assert "make check: all" in done.stdout
    assert pytest_calls(calls) == ["uv LLM_MODE=replay run pytest"]
    assert "pnpm -r test" in calls


@pytest.mark.parametrize("plan", ["tests/hooks", "all"])
def test_check_cannot_be_turned_into_a_live_run(tmp_path: Path, plan: str) -> None:
    _, calls = run_check(tmp_path, plan, "LLM_MODE=live")
    assert pytest_calls(calls)
    assert all("LLM_MODE=replay" in c for c in pytest_calls(calls))


def test_a_planner_failure_fails_the_check_and_runs_no_tests(tmp_path: Path) -> None:
    done, calls = run_check(tmp_path, "tests/hooks", fail_planner=True)
    assert done.returncode != 0
    assert pytest_calls(calls) == []
    assert "pnpm -r test" not in calls


def test_a_failing_python_run_skips_the_portal_tests(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    # The shim for this one case fails any pytest call.
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text())
    shim = tmp_path / "bin" / "uv"
    shim.write_text(
        "#!/bin/sh\n"
        'case "$*" in *changed_tests.py*) echo "evals portal"; exit 0;; esac\n'
        'echo "uv LLM_MODE=$LLM_MODE $*"\n'
        'case "$*" in *"run pytest"*) exit 1;; esac\n'
    )
    shim.chmod(0o755)
    done = subprocess.run(  # noqa: S603
        ["make", "-s", "check", "PNPM=echo pnpm"],  # noqa: S607
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
    assert "pnpm -r test" not in done.stdout.splitlines()


def test_finish_reaches_the_runner_clone_only_through_runner_sync() -> None:
    line = next(
        text
        for text in (REPO / "Makefile").read_text().splitlines()
        if text.startswith("RUNNER_SYNC_PATHS :=")
    )
    assert " tools/finish.sh " in line
    assert " tools/ship.sh " in line
