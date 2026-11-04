"""Makefile and GitHub CI wiring for the eval gate (RAG-12; user conditions e and f).

`uv` is replaced by a shim that prints the LLM_MODE it was started with and its arguments, so
nothing real runs and nothing is spent. The style follows
services/llm-replay/tests/test_makefile_targets.py.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("RAG-12")

REPO = Path(__file__).resolve().parents[2]
MAKEFILE = (REPO / "Makefile").read_text(encoding="utf-8")
KEY_FILE = "." + "env"
SHIM = '#!/bin/sh\necho "uv LLM_MODE=$LLM_MODE $*"\n'


def run_make(
    tmp_path: Path, target: str, *args: str, env: dict[str, str] | None = None
) -> tuple[int, list[str], str]:
    (tmp_path / "Makefile").write_text(MAKEFILE)
    shim = tmp_path / "bin" / "uv"
    shim.parent.mkdir(exist_ok=True)
    shim.write_text(SHIM)
    shim.chmod(0o755)
    done = subprocess.run(  # noqa: S603
        ["make", "-s", target, "PNPM=true", *args],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{shim.parent}{os.pathsep}/usr/bin:/bin:/usr/local/bin",
            "HOME": str(tmp_path),
            **(env or {}),
        },
    )
    calls = [x for x in done.stdout.splitlines() if x.startswith("uv ")]
    return done.returncode, calls, done.stderr + done.stdout


def recipe(target: str) -> str:
    """The recipe lines of one Makefile target."""
    match = re.search(rf"^{re.escape(target)}:.*\n((?:\t.*\n|#.*\n)*)", MAKEFILE, re.MULTILINE)
    assert match, target
    return match.group(1)


# --- make eval-gate --------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["shell", "command-line", "key-file"])
def test_eval_gate_always_replays(tmp_path: Path, source: str) -> None:
    env: dict[str, str] = {}
    args: list[str] = []
    if source == "shell":
        env["LLM_MODE"] = "record"
    elif source == "command-line":
        args.append("LLM_MODE=record")
    else:
        (tmp_path / KEY_FILE).write_text("LLM_MODE=record\n")
    code, calls, _ = run_make(tmp_path, "eval-gate", *args, env=env)
    assert code == 0
    gate = [c for c in calls if "northwind_evals.cli gate" in c]
    assert len(gate) == 1
    assert gate[0].startswith("uv LLM_MODE=replay ")


def test_eval_gate_builds_the_index_first_and_has_no_spending_step(tmp_path: Path) -> None:
    code, calls, _ = run_make(tmp_path, "eval-gate")
    assert code == 0
    assert "northwind_rag.cli" in calls[0]
    assert "northwind_evals.cli gate" in calls[-1]
    assert not any("LLM_MODE=record" in c or "LLM_MODE=live" in c for c in calls)


def test_eval_gate_takes_no_slice_and_passes_none(tmp_path: Path) -> None:
    _, calls, _ = run_make(tmp_path, "eval-gate", "EVAL_SLICE=3")
    assert not any("--slice" in c for c in calls)


# --- make eval-live --------------------------------------------------------------------------


@pytest.mark.parametrize("args", [[], ["EVAL_SLICE="]], ids=["unset", "empty"])
def test_a_bare_eval_live_refuses_and_makes_no_call(tmp_path: Path, args: list[str]) -> None:
    code, calls, output = run_make(tmp_path, "eval-live", *args)
    assert code != 0
    assert calls == []
    assert "EVAL_SLICE" in output


def test_eval_live_with_a_slice_runs_the_cli_run_command_in_record_mode(tmp_path: Path) -> None:
    code, calls, _ = run_make(tmp_path, "eval-live", "EVAL_SLICE=20")
    assert code == 0
    [call] = calls
    assert call.startswith("uv LLM_MODE=record ")
    assert call.endswith("northwind_evals.cli run --slice 20")


def test_eval_live_takes_the_slice_from_the_shell_too(tmp_path: Path) -> None:
    _, calls, _ = run_make(tmp_path, "eval-live", env={"EVAL_SLICE": "100"})
    assert calls and calls[0].endswith("run --slice 100")


# --- who may spend ---------------------------------------------------------------------------


def test_only_record_and_eval_live_recipes_can_run_in_a_spending_mode() -> None:
    spenders = set()
    for match in re.finditer(r"^([a-z][\w-]*):.*\n((?:\t.*\n|#.*\n)*)", MAKEFILE, re.MULTILINE):
        target, body = match.groups()
        if re.search(r"LLM_MODE=(record|live)", body):
            spenders.add(target)
    assert spenders == {"record", "eval-live"}


@pytest.mark.parametrize("target", ["eval-snapshot", "eval-review-list", "eval-gate"])
def test_other_eval_targets_never_set_a_spending_mode(target: str) -> None:
    assert "record" not in recipe(target).replace("record the", "")
    assert "LLM_MODE=live" not in recipe(target)


@pytest.mark.parametrize(
    ("target", "command"),
    [("eval-snapshot", "snapshot"), ("eval-review-list", "review-list")],
)
def test_snapshot_and_review_list_call_their_commands(
    tmp_path: Path, target: str, command: str
) -> None:
    code, calls, _ = run_make(tmp_path, target)
    assert code == 0
    assert calls and calls[-1].endswith(f"northwind_evals.cli {command}")


def test_the_spending_targets_are_phony_and_listed_in_help() -> None:
    phony = re.search(r"^\.PHONY:((?:.*\\\n)*.*)", MAKEFILE, re.MULTILINE)
    assert phony
    for target in ("eval-gate", "eval-live", "eval-snapshot", "eval-review-list"):
        assert target in phony.group(1)
        assert re.search(rf"^{target}:.*## ", MAKEFILE, re.MULTILINE), target


def test_the_live_target_help_says_it_spends() -> None:
    assert re.search(r"^eval-live:.*## .*spends", MAKEFILE, re.MULTILINE | re.IGNORECASE)
    assert re.search(r"^record:.*## .*spends", MAKEFILE, re.MULTILINE | re.IGNORECASE)


# --- condition f: DB-backed tests are not silently deselected -------------------------------


def test_default_pytest_deselects_integration_and_e2e_so_record_must_override_it() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = pyproject["tool"]["pytest"]["ini_options"]["addopts"]
    assert addopts[addopts.index("-m") + 1] == "not integration and not e2e"


def test_make_record_overrides_the_default_marker_filter() -> None:
    """Without `-m ""`, a DB-backed test with an llm_suite would be deselected and never
    recorded, with only a "deselected" count to show for it."""
    body = recipe("record")
    assert '-m ""' in body
    assert body.index('-m ""') < body.index("--llm-suite")


def test_pytest_really_lets_a_later_marker_option_select_integration_tests() -> None:
    """The mechanism `make record` relies on, checked against the real config, collect only."""
    base = ["uv", "run", "pytest", "--collect-only", "-q", "services/rag/tests/test_retrieval.py"]
    env = {**os.environ, "LLM_MODE": "replay"}
    default = subprocess.run(base, cwd=REPO, capture_output=True, text=True, env=env, check=False)  # noqa: S603
    override = subprocess.run(  # noqa: S603
        [*base, "-m", ""], cwd=REPO, capture_output=True, text=True, env=env, check=False
    )
    assert "test_retrieval.py::" not in default.stdout
    assert "test_retrieval.py::" in override.stdout


def test_make_ci_runs_the_gate_last_after_the_integration_tests(tmp_path: Path) -> None:
    code, calls, _ = run_make(tmp_path, "ci")
    assert code == 0
    index = {
        "scan": next(i for i, c in enumerate(calls) if "northwind_llm_replay.cli scan" in c),
        "unit": next(i for i, c in enumerate(calls) if "run pytest" in c and " -m " not in c),
        "integration": next(i for i, c in enumerate(calls) if "pytest -m integration" in c),
        "gate": next(i for i, c in enumerate(calls) if "northwind_evals.cli gate" in c),
    }
    assert index["scan"] < index["unit"] < index["integration"] < index["gate"]
    assert calls[index["gate"]].startswith("uv LLM_MODE=replay ")


# --- GitHub CI -------------------------------------------------------------------------------


CI_TEXT = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")


def job_text(job: str) -> str:
    """One job's block of ci.yml (no YAML parser is a dependency, so this reads the text)."""
    match = re.search(rf"^  {re.escape(job)}:\n((?:(?:    .*)?\n)+)", CI_TEXT, re.MULTILINE)
    assert match, job
    return match.group(1)


def steps(job: str) -> list[str]:
    """The text of each step of a job."""
    body = job_text(job).split("    steps:\n", 1)[1]
    return ["      - " + part for part in re.split(r"^      - ", body, flags=re.MULTILINE)[1:]]


def runs(job: str) -> list[str]:
    return [m.group(1).strip() for step in steps(job) if (m := re.search(r"run: (.*)", step))]


@pytest.mark.req("SA-02")
def test_ci_runs_the_browser_test_on_a_canned_model_and_replayed() -> None:
    block = job_text("e2e")
    assert "LLM_MODE: replay" in block
    assert "secrets." not in block
    assert block.index("--with-deps chromium") < block.index("make e2e-scripted")
    assert block.index("make e2e-scripted") < block.index("run: make e2e\n")


def test_ci_has_an_eval_gate_job_that_runs_the_gate_command() -> None:
    assert any("northwind_evals.cli gate" in c for c in runs("eval-gate"))


def test_ci_gate_job_replays_and_has_a_database_and_an_index_before_the_gate() -> None:
    text = job_text("eval-gate")
    assert re.search(r"^      LLM_MODE: replay$", text, re.MULTILINE)
    assert "image: pgvector/pgvector" in text
    commands = runs("eval-gate")
    ingest = next(i for i, c in enumerate(commands) if "northwind_rag.cli" in c)
    gate = next(i for i, c in enumerate(commands) if "northwind_evals.cli gate" in c)
    assert ingest < gate


def test_ci_can_never_spend_or_loosen_the_gate() -> None:
    text = CI_TEXT
    assert "secrets." not in text
    assert "ANTHROPIC" not in text
    assert "eval-live" not in text
    assert "EVAL_SLICE" not in text
    assert "EVAL_ALLOW_STALE" not in text
    assert "northwind_evals.cli run" not in text
    assert "make record" not in text
    modes = [line.split(":", 1)[1].strip() for line in text.splitlines() if "LLM_MODE" in line]
    assert modes
    assert set(modes) == {"replay"}


def test_the_eval_gate_job_does_not_continue_on_error() -> None:
    assert "continue-on-error" not in job_text("eval-gate")


def test_ci_integration_step_runs_after_a_unit_failure() -> None:
    python = steps("python")
    unit = next(i for i, s in enumerate(python) if re.search(r"run: uv run pytest\s*$", s, re.M))
    integration = next(i for i, s in enumerate(python) if "pytest -m integration" in s)
    assert unit < integration
    assert "!cancelled()" in python[integration]
    assert "if:" not in python[unit]
    assert "continue-on-error" not in python[unit]


def test_ci_python_job_lints_the_evals_package_and_scans_saved_contexts() -> None:
    commands = runs("python")
    assert any(c.startswith("uv run mypy") and "evals" in c for c in commands)
    assert any("scan cassettes" in c and "evals/contexts" in c for c in commands)


def test_ci_gate_job_installs_the_same_extension_and_model_cache_as_the_python_job() -> None:
    def fingerprint(job: str) -> list[str]:
        return [
            line.strip()
            for step in steps(job)
            if "psql" in step or "actions/cache" in step
            for line in step.splitlines()
            if "psql" in line or "key:" in line or "path:" in line
        ]

    assert fingerprint("eval-gate") == fingerprint("python")


def test_the_ci_scan_covers_the_contexts_the_cli_replays_from() -> None:
    from northwind_evals import cli

    assert str(cli.CONTEXTS.parent) == "evals/contexts"
