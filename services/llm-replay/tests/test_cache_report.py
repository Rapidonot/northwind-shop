"""The cache-hit report and the price move that carries it (RAG-13, NFR-16).

`make llm-spend` reads the ledger, and the runner prices calls from `northwind_common.pricing`,
which has to load in `.venv-runner` (only llm-replay and common). Synthetic ledgers, no spend.
"""

from __future__ import annotations

import ast
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from northwind_common import localenv
from northwind_common import pricing as common_pricing
from northwind_llm_replay import cli, runner_check
from northwind_llm_replay import pricing as replay_pricing
from northwind_llm_replay.ledger import Ledger

pytestmark = [pytest.mark.req("RAG-13"), pytest.mark.req("NFR-16")]

HAIKU = "claude-haiku-5-5"


def ledger_at(path: Path, suite: str = "s") -> Ledger:
    return Ledger(path, budget_usd=100, run_budget_usd=None, run_id="r", suite=suite)


def settle(ledger: Ledger, usage: dict[str, object] | None) -> None:
    reservation = ledger.reserve(model=HAIKU, worst_case_usd=0.5, request_hash="h")
    ledger.settle(reservation, usage)


def spend(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    assert cli.main(["spend", "--ledger", str(path)]) == 0
    return capsys.readouterr().out


def suite_line(out: str, suite: str) -> str:
    [line] = [
        ln
        for ln in out.splitlines()
        if ln.strip().startswith(f"suite {suite} ") and "calls" in ln and "hit" in ln
    ]
    return line


# --- 3. the spend table ----------------------------------------------------------------------


def test_the_spend_table_shows_calls_tokens_hit_rate_and_cost_per_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = ledger_at(tmp_path / "l.jsonl", "chat")
    settle(ledger, {"input_tokens": 100, "cache_read_input_tokens": 300, "output_tokens": 10})
    settle(
        ledger,
        {
            "input_tokens": 100,
            "cache_read_input_tokens": 300,
            "cache_creation_input_tokens": 200,
            "output_tokens": 30,
        },
    )
    line = suite_line(spend(ledger.path, capsys), "chat")
    assert "calls 2" in line and "(no usage: 0," in line
    assert "input 200" in line and "read 600" in line and "write 200" in line
    assert "output 40" in line
    assert "hit 60.0%" in line  # 600 / (200 + 600 + 200)
    expected = ledger.read_totals().by_suite["chat"] / 2
    assert f"per call ${expected:.6f}" in line


def test_the_report_object_matches_the_table(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path / "l.jsonl", "chat")
    settle(ledger, {"input_tokens": 50, "cache_read_input_tokens": 150, "output_tokens": 5})
    row = ledger.usage_by_suite()["chat"]
    assert (row.calls, row.unreported) == (1, 0)
    assert row.cache_hit_rate == pytest.approx(0.75)
    assert row.cost_per_call == pytest.approx(row.cost_usd)


def test_suites_are_reported_separately(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "l.jsonl"
    settle(ledger_at(path, "one"), {"input_tokens": 10, "output_tokens": 1})
    settle(ledger_at(path, "two"), {"input_tokens": 1, "cache_read_input_tokens": 9})
    out = spend(path, capsys)
    assert "calls 1" in suite_line(out, "one") and "hit 0.0%" in suite_line(out, "one")
    assert "hit 90.0%" in suite_line(out, "two")


def test_a_call_with_no_usage_is_counted_in_no_usage_not_dropped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = ledger_at(tmp_path / "l.jsonl", "chat")
    settle(ledger, None)
    settle(ledger, {"input_tokens": 100, "cache_read_input_tokens": 100, "output_tokens": 1})
    line = suite_line(spend(ledger.path, capsys), "chat")
    assert "calls 2" in line and "(no usage: 1," in line
    assert "hit 50.0%" in line  # the call with no usage adds no tokens to the rate


def test_a_suite_with_only_no_usage_calls_prints_na_for_the_rate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = ledger_at(tmp_path / "l.jsonl", "chat")
    settle(ledger, None)
    line = suite_line(spend(ledger.path, capsys), "chat")
    assert "calls 1" in line and "(no usage: 1," in line and "hit n/a" in line


def test_no_prompt_tokens_prints_na_not_a_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = ledger_at(tmp_path / "l.jsonl", "chat")
    settle(ledger, {"input_tokens": 0, "output_tokens": 9})
    line = suite_line(spend(ledger.path, capsys), "chat")
    assert "hit n/a" in line and "output 9" in line
    assert ledger.usage_by_suite()["chat"].cache_hit_rate is None


def test_a_reserve_with_no_settle_is_not_counted_as_a_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = ledger_at(tmp_path / "l.jsonl", "chat")
    settle(ledger, {"input_tokens": 10, "output_tokens": 1})
    ledger.reserve(model=HAIKU, worst_case_usd=0.5, request_hash="open")  # never settled
    line = suite_line(spend(ledger.path, capsys), "chat")
    assert "calls 1" in line
    assert ledger.usage_by_suite()["chat"].calls == 1


def test_a_suite_with_only_an_open_reserve_is_not_in_the_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = ledger_at(tmp_path / "l.jsonl", "pending")
    ledger.reserve(model=HAIKU, worst_case_usd=0.5, request_hash="open")
    assert "pending" not in ledger.usage_by_suite()
    out = spend(ledger.path, capsys)
    assert not [ln for ln in out.splitlines() if "calls" in ln and "pending" in ln]


def test_a_half_written_last_line_does_not_crash_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = ledger_at(tmp_path / "l.jsonl", "chat")
    settle(ledger, {"input_tokens": 10, "cache_read_input_tokens": 10, "output_tokens": 1})
    with ledger.path.open("a") as f:
        f.write('{"type": "settle", "id"')
    line = suite_line(spend(ledger.path, capsys), "chat")
    assert "calls 1" in line and "hit 50.0%" in line


def test_the_report_needs_no_writable_lock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = ledger_at(tmp_path / "l.jsonl", "chat")
    settle(ledger, {"input_tokens": 10, "output_tokens": 1})
    (tmp_path / "l.lock").chmod(0o400)
    assert "calls 1" in suite_line(spend(ledger.path, capsys), "chat")


def test_an_empty_ledger_reports_without_a_suite_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = spend(tmp_path / "none.jsonl", capsys)
    assert "settled calls  0" in out


# --- 4. the pricing move ---------------------------------------------------------------------


def test_the_proxy_import_path_re_exports_the_same_objects() -> None:
    for name in replay_pricing.__all__:
        assert getattr(replay_pricing, name) is getattr(common_pricing, name), name
    assert replay_pricing.__all__
    assert replay_pricing.cost_usd is common_pricing.cost_usd
    assert replay_pricing.PRICES is common_pricing.PRICES


def test_the_common_pricing_module_imports_only_the_standard_library() -> None:
    tree = ast.parse(Path(common_pricing.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import reaches outside the module"
            imported.add((node.module or "").split(".")[0])
    assert imported
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


# --- 5. runner-check proves the runner can price ---------------------------------------------


def fake_python(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake-python"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_a_real_interpreter_names_the_pricing_module_and_has_no_problems() -> None:
    module, problems = runner_check.pricing_module(Path(sys.executable))
    assert problems == []
    assert module and module.startswith("northwind_")


def test_an_interpreter_whose_import_fails_gives_a_pricing_problem(tmp_path: Path) -> None:
    python = fake_python(tmp_path, 'echo "ModuleNotFoundError: no module named x" >&2; exit 1')
    module, problems = runner_check.pricing_module(python)
    assert module is None
    [problem] = problems
    assert problem.startswith("PRICING")
    assert "ModuleNotFoundError" in problem


def test_a_failed_import_with_empty_stderr_still_fails(tmp_path: Path) -> None:
    python = fake_python(tmp_path, "exit 1")
    module, problems = runner_check.pricing_module(python)
    assert module is None
    assert len(problems) == 1 and problems[0].startswith("PRICING")


def test_a_failed_import_with_only_blank_stderr_lines_still_fails(tmp_path: Path) -> None:
    python = fake_python(tmp_path, "printf '\\n\\n   \\n' >&2; exit 3")
    module, problems = runner_check.pricing_module(python)
    assert module is None
    assert len(problems) == 1 and problems[0].startswith("PRICING")


def isolate_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the pricing probe decides the exit code, and nothing here touches the real repo, so
    neither the sandbox nor a terminal can fail these tests."""
    monkeypatch.setattr(runner_check, "load_paths", lambda _python: [])
    monkeypatch.setattr(runner_check, "check", lambda *_args: [])
    monkeypatch.setattr(runner_check, "checkouts", lambda: [])
    monkeypatch.setattr(runner_check, "git_redirect_files", lambda *_args: ([], []))
    monkeypatch.setattr(localenv, "main_checkout", lambda: Path("/nonexistent"))


def test_main_exits_1_and_names_pricing_when_the_import_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolate_main(monkeypatch)
    python = fake_python(tmp_path, "exit 1")  # empty stderr: nothing may hide the failure
    assert runner_check.main(python) == 1
    out = capsys.readouterr().out
    assert any(line.startswith("PRICING") for line in out.splitlines())
    assert "1 problem(s)" in out


def test_main_exits_0_with_a_real_interpreter_when_nothing_else_is_wrong(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    isolate_main(monkeypatch)
    assert runner_check.main(Path(sys.executable)) == 0
    out = capsys.readouterr().out
    assert "PRICING" not in out and "0 problem(s)" in out


def test_the_probe_runs_isolated_from_the_callers_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A planted `northwind_llm_replay` on PYTHONPATH must not be what gets priced."""
    planted = tmp_path / "planted" / "northwind_llm_replay"
    planted.mkdir(parents=True)
    (planted / "__init__.py").write_text("raise ImportError('planted')")
    monkeypatch.setenv("PYTHONPATH", str(planted.parent))
    module, problems = runner_check.pricing_module(Path(sys.executable))
    assert problems == [] and module
    done = subprocess.run(  # noqa: S603 - proves the plant would have broken a non-isolated run
        [sys.executable, "-c", "import northwind_llm_replay"],
        capture_output=True,
        check=False,
    )
    assert done.returncode != 0


def test_a_call_released_unsent_is_not_a_call(tmp_path: Path) -> None:
    """The proxy releases at $0 with `usage={}` when nothing reached the API. That is not the same
    as `usage=None`, a call charged at worst case that may really have been billed."""
    ledger = Ledger(tmp_path / "l.jsonl", budget_usd=10, run_budget_usd=None, run_id="r", suite="s")
    sent = ledger.reserve(model="claude-sonnet-5-5", worst_case_usd=1.0, request_hash="a")
    ledger.settle(sent, {"input_tokens": 10, "output_tokens": 1})
    released = ledger.reserve(model="claude-sonnet-5-5", worst_case_usd=1.0, request_hash="b")
    ledger.settle(released, {})
    lost = ledger.reserve(model="claude-sonnet-5-5", worst_case_usd=1.0, request_hash="c")
    ledger.settle(lost, None)
    row = ledger.usage_by_suite()["s"]
    assert (row.calls, row.unsent, row.unreported) == (2, 1, 1)
    assert row.cost_per_call == pytest.approx(row.cost_usd / 2)
