"""CC-27 F1/F3 (decision 0005): what surrounds the runner. Make targets run against shims, so
nothing real starts, and nothing here reaches the network or a real key."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from northwind_llm_replay import cli, runner_check
from northwind_llm_replay.ledger import Ledger
from northwind_llm_replay.scan import SYMLINK_REFUSED, scan_paths

pytestmark = pytest.mark.req("CC-27")
pytest_plugins = ["pytester"]

REPO = Path(__file__).resolve().parents[3]
FAKE_KEY = "sk-ant-" + "supportfake00000000000"  # fake; built at runtime
SHIM = '#!/bin/sh\necho "$(basename "$0") $*"\n'


def rig(tmp_path: Path, *tools: str) -> dict[str, str]:
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in tools:
        (bin_dir / tool).write_text(SHIM)
        (bin_dir / tool).chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}


def make(
    tmp_path: Path, env: dict[str, str], *args: str, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["make", "-s", *args],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        input=stdin,
    )


# -- runner-sync (item 3) --------------------------------------------------------------------


def rig_with_subject(tmp_path: Path) -> dict[str, str]:
    env = rig(tmp_path, "git", "uv")
    (tmp_path / "bin" / "git").write_text(
        "#!/bin/sh\n"
        "[ \"$1\" = --no-pager ] && printf 'abc \\033[8mhidden\\n'\nexit 0\n"
    )  # fmt: skip
    return env


def sync(tmp_path: Path, stdin: str) -> subprocess.CompletedProcess[str]:
    env = rig(tmp_path, "git", "uv")
    return make(tmp_path, env, "runner-sync", f"RUNNER_DIR={tmp_path}", stdin=stdin)


def real_repos(tmp_path: Path) -> tuple[Path, Path, str]:
    """An origin one commit ahead of a clone, with real git: a shim cannot show ref updates."""

    def git(cwd: Path, *args: str) -> str:
        done = subprocess.run(  # noqa: S603
            ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],  # noqa: S607
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return done.stdout.strip()

    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "README").write_text("one")
    git(origin, "add", "README")
    git(origin, "commit", "-q", "-m", "first")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    (origin / "README").write_text("two")
    git(origin, "commit", "-qam", "second")
    return origin.resolve(), clone.resolve(), git(origin, "rev-parse", "HEAD")


def origin_main(clone: Path) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "origin/main"],  # noqa: S607
        cwd=clone,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def test_with_real_git_a_no_leaves_origin_main_where_it_was(tmp_path: Path) -> None:
    _, clone, new_head = real_repos(tmp_path)
    before = origin_main(clone)
    env = rig(clone, "uv")
    env["PATH"] = f"{clone / 'bin'}{os.pathsep}{os.environ['PATH']}"
    done = make(clone, env, "runner-sync", f"RUNNER_DIR={clone}", stdin="n\n")
    assert done.returncode != 0
    assert "second" in done.stdout
    assert origin_main(clone) == before != new_head


def test_with_real_git_a_yes_moves_head_and_origin_main(tmp_path: Path) -> None:
    _, clone, new_head = real_repos(tmp_path)
    env = rig(clone, "uv")
    env["PATH"] = f"{clone / 'bin'}{os.pathsep}{os.environ['PATH']}"
    done = make(clone, env, "runner-sync", f"RUNNER_DIR={clone}", stdin="y\n")
    assert done.returncode == 0, done.stdout + done.stderr
    assert origin_main(clone) == new_head
    assert (clone / "README").read_text() == "two"


def test_runner_sync_shows_everything_and_asks_before_merging(tmp_path: Path) -> None:
    done = sync(tmp_path, "y\n")
    assert done.returncode == 0
    lines = done.stdout.splitlines()
    assert lines[0] == "git fetch --refmap= origin +refs/heads/main:refs/runner/incoming"
    log = next(i for i, line in enumerate(lines) if " log " in line)
    stat = next(i for i, line in enumerate(lines) if " --stat HEAD " in line)
    full = next(i for i, line in enumerate(lines) if "--no-textconv HEAD " in line)
    merge = next(i for i, line in enumerate(lines) if line.startswith("git merge"))
    assert log < stat < full < merge
    assert lines[merge] == "git merge --ff-only refs/runner/incoming"
    assert lines[merge + 1] == "git update-ref refs/remotes/origin/main refs/runner/incoming"
    assert lines[-2] == "uv sync --frozen --no-dev --package northwind-llm-replay"
    assert lines[-1] == "uv sync --frozen"


def test_runner_sync_filters_neither_the_log_nor_the_stat(tmp_path: Path) -> None:
    """A workspace member's src loads with the key, so a filtered stat could show nothing."""
    lines = sync(tmp_path, "y\n").stdout.splitlines()
    log = next(line for line in lines if " log " in line)
    stat = next(line for line in lines if " --stat HEAD " in line)
    assert " -- " not in log
    assert " -- " not in stat


def test_runner_sync_shows_the_full_diff_of_the_key_paths(tmp_path: Path) -> None:
    lines = sync(tmp_path, "y\n").stdout.splitlines()
    full = next(line for line in lines if "--no-textconv HEAD " in line)
    for path in (
        "services/llm-replay",
        "services/common",
        "Makefile",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "**/pyproject.toml",
        "services/rag",
        "infra",
        ".gitattributes",
        "uv.toml",
    ):
        assert path in full


def test_answering_no_leaves_origin_main_alone(tmp_path: Path) -> None:
    """Otherwise preflight would block every window until the user said yes."""
    done = sync(tmp_path, "n\n")
    assert "update-ref" not in done.stdout
    assert "refs/remotes/origin/main" not in done.stdout


def test_runner_sync_cannot_print_raw_commit_subjects(tmp_path: Path) -> None:
    """A subject is text the sandbox wrote, and it reaches the user's terminal."""
    env = rig_with_subject(tmp_path)
    done = make(tmp_path, env, "runner-sync", f"RUNNER_DIR={tmp_path}", stdin="n\n")
    assert "\x1b" not in done.stdout
    assert "^[" in done.stdout


@pytest.mark.parametrize("answer", ["n\n", "\n", "yes please\n", ""])
def test_runner_sync_defaults_to_no(tmp_path: Path, answer: str) -> None:
    done = sync(tmp_path, answer)
    assert done.returncode != 0
    assert "git merge" not in done.stdout
    assert "uv sync" not in done.stdout


def test_runner_sync_refuses_outside_the_clone(tmp_path: Path) -> None:
    """From a worktree it would merge main into the working branch."""
    env = rig(tmp_path, "git", "uv")
    other = tmp_path / "other"
    other.mkdir()
    done = make(tmp_path, env, "runner-sync", f"RUNNER_DIR={other}", stdin="y\n")
    assert done.returncode != 0
    assert "git fetch" not in done.stdout


# -- llm-runner (item 8) ---------------------------------------------------------------------


def runner_shim(tmp_path: Path) -> dict[str, str]:
    env = rig(tmp_path, "uv")
    python = tmp_path / ".venv-runner" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(SHIM)
    python.chmod(0o755)
    return env


def test_llm_runner_isolates_python_and_passes_the_defaults(tmp_path: Path) -> None:
    env = runner_shim(tmp_path)
    done = make(tmp_path, env, "llm-runner", "SUITE=rag-enrich", "RUN_BUDGET=1.50")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip() == (
        "python -I -m northwind_llm_replay.cli runner --suite rag-enrich --run-budget 1.50 "
        "--minutes 30 --idle-minutes 10"
    )


def test_llm_runner_refuses_an_environment_that_does_not_match_the_lock(tmp_path: Path) -> None:
    env = runner_shim(tmp_path)
    (tmp_path / "bin" / "uv").write_text('#!/bin/sh\necho "uv $*"\nexit 1\n')
    done = make(tmp_path, env, "llm-runner", "SUITE=a", "RUN_BUDGET=1")
    assert done.returncode != 0
    assert "--run-budget" not in done.stdout
    assert "runner-sync" in done.stdout


def test_llm_runner_takes_the_time_limits(tmp_path: Path) -> None:
    env = runner_shim(tmp_path)
    done = make(
        tmp_path,
        env,
        "llm-runner",
        "SUITE=a",
        "RUN_BUDGET=2",
        "RUNNER_MINUTES=90",
        "RUNNER_IDLE_MINUTES=5",
    )
    assert "--minutes 90 --idle-minutes 5" in done.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["SUITE=a"],
        ["RUN_BUDGET=1"],
        ["SUITE=a", "RUN_BUDGET="],
        ["SUITE=a", "RUN_BUDGET=1; touch injected"],
        ["SUITE=a; touch injected", "RUN_BUDGET=1"],
        ["SUITE=a", "RUN_BUDGET=nan"],
        ["SUITE=a", "RUN_BUDGET=-1"],
        ["SUITE=a", "RUN_BUDGET=1", "RUNNER_MINUTES=x; touch injected"],
    ],
)
def test_llm_runner_needs_a_suite_and_an_explicit_budget(tmp_path: Path, args: list[str]) -> None:
    env = runner_shim(tmp_path)
    done = make(tmp_path, env, "llm-runner", *args)
    assert done.returncode != 0
    assert "--run-budget" not in done.stdout
    assert not (tmp_path / "injected").exists()


def test_inside_the_clone_the_scan_reads_the_key_on_the_runner_python(tmp_path: Path) -> None:
    env = runner_shim(tmp_path)
    done = make(tmp_path, env, "scan-cassettes", f"RUNNER_DIR={tmp_path}", "CASSETTES_DIR=/a/b")
    assert done.stdout.strip() == (
        "python -I -m northwind_llm_replay.cli scan --cassettes-dir /a/b"
    )


def test_inside_the_clone_uv_stays_off_the_shared_cache_and_pythonpath_goes(tmp_path: Path) -> None:
    env = rig(tmp_path, "uv")
    env["PYTHONPATH"] = "/somewhere"
    (tmp_path / "bin" / "uv").write_text(
        '#!/bin/sh\necho "uv cache=$UV_CACHE_DIR frozen=$UV_FROZEN path=$PYTHONPATH"\n'
    )
    done = make(tmp_path, env, "llm-spend", f"RUNNER_DIR={tmp_path}")
    assert done.stdout.strip() == f"uv cache={tmp_path}/.northwind/uv-cache frozen=1 path="


def test_outside_the_clone_uv_is_left_alone(tmp_path: Path) -> None:
    env = rig(tmp_path, "uv")
    (tmp_path / "bin" / "uv").write_text('#!/bin/sh\necho "uv cache=$UV_CACHE_DIR"\n')
    done = make(tmp_path, env, "llm-spend", f"RUNNER_DIR={tmp_path}/elsewhere")
    assert done.stdout.strip() == "uv cache="


def test_compose_keeps_one_project_from_any_checkout(tmp_path: Path) -> None:
    """The name in infra/docker-compose.yml: another name would be another, empty, data volume."""
    env = rig(tmp_path, "docker")
    (tmp_path / "bin" / "docker").write_text('#!/bin/sh\necho "project=$COMPOSE_PROJECT_NAME"\n')
    done = make(tmp_path, env, "up")
    assert done.stdout.strip() == "project=northwind"
    assert "name: northwind\n" in (REPO / "infra" / "docker-compose.yml").read_text()


# -- scan-cassettes (item 7) -----------------------------------------------------------------


def test_scan_cassettes_passes_an_absolute_directory(tmp_path: Path) -> None:
    env = rig(tmp_path, "uv")
    done = make(tmp_path, env, "scan-cassettes", "CASSETTES_DIR=/Users/x/wt/cassettes")
    assert done.stdout.strip() == (
        "uv run python -m northwind_llm_replay.cli scan --cassettes-dir /Users/x/wt/cassettes"
    )


def test_scan_cassettes_needs_no_directory(tmp_path: Path) -> None:
    env = rig(tmp_path, "uv")
    done = make(tmp_path, env, "scan-cassettes")
    assert done.stdout.strip() == "uv run python -m northwind_llm_replay.cli scan"


@pytest.mark.parametrize("value", ["relative/path", "/x; touch injected", "/x`touch injected`"])
def test_scan_cassettes_refuses_anything_but_a_plain_absolute_path(
    tmp_path: Path, value: str
) -> None:
    env = rig(tmp_path, "uv")
    done = make(tmp_path, env, "scan-cassettes", f"CASSETTES_DIR={value}")
    assert done.returncode != 0
    assert "usage: make scan-cassettes" in done.stdout
    assert not (tmp_path / "injected").exists()


def test_a_symlinked_file_is_reported_and_not_read(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text(f"key {FAKE_KEY}")
    root = tmp_path / "cassettes"
    root.mkdir()
    (root / "link.json").symlink_to(outside)
    findings = scan_paths([root])
    assert [f.kind for f in findings] == [SYMLINK_REFUSED]


def test_a_symlinked_directory_is_reported_and_not_entered(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text(f"key {FAKE_KEY}")
    root = tmp_path / "cassettes"
    root.mkdir()
    (root / "dir").symlink_to(outside, target_is_directory=True)
    assert [f.kind for f in scan_paths([root])] == [SYMLINK_REFUSED]


def test_a_symlinked_root_is_reported_and_not_entered(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.txt").write_text(f"key {FAKE_KEY}")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    assert [f.kind for f in scan_paths([link])] == [SYMLINK_REFUSED]


@pytest.mark.parametrize("name", ["a\x1b[8mhidden.txt", "a\nscan clean.txt"])
def test_a_hostile_file_name_cannot_write_to_the_terminal(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text(f"key {FAKE_KEY}")
    lines = [str(f) for f in scan_paths([tmp_path])]
    assert lines
    assert not [line for line in lines if "\x1b" in line or "\n" in line]


def test_a_fifo_does_not_hang_the_scan(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe")
    assert scan_paths([tmp_path]) == []


def test_real_files_are_still_scanned(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text(f"key {FAKE_KEY}")
    assert "anthropic-api-key" in [f.kind for f in scan_paths([tmp_path])]


def test_scan_output_names_the_finding_and_never_the_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    (cassettes / "a.txt").write_text(f"key {FAKE_KEY}")
    key_file = tmp_path / "key"
    key_file.write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\n")
    answer_key = tmp_path / "data" / "fixtures" / "_answer_key.json"
    answer_key.parent.mkdir(parents=True)
    answer_key.write_text(json.dumps({"internal_only": []}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_LEDGER_PATH", str(tmp_path / "l.jsonl"))
    monkeypatch.setattr("northwind_common.localenv.worktree_root", lambda: tmp_path)
    code = cli.main(["scan", "--cassettes-dir", str(cassettes), "--env-file", str(key_file)])
    captured = capsys.readouterr()
    assert code == 1
    assert "api-key-fragment" in captured.err
    assert "a.txt:0: api-key-fragment" in captured.err  # no line: guesses must not be locatable
    assert "ROTATE THE KEY" in captured.err
    assert "regex + key-fragment" in captured.out
    assert FAKE_KEY[:16] not in captured.out + captured.err


# -- llm-spend without the lock (item 2) -----------------------------------------------------


def ledger_with_a_call(tmp_path: Path) -> Ledger:
    ledger = Ledger(tmp_path / "l.jsonl", budget_usd=1, run_budget_usd=None, run_id="r", suite="s")
    res = ledger.reserve(model="claude-haiku-5-5", worst_case_usd=0.5, request_hash="h")
    ledger.settle(res, None)
    return ledger


def test_read_totals_match_totals_and_take_no_lock(tmp_path: Path) -> None:
    ledger = ledger_with_a_call(tmp_path)
    expected = ledger.totals()
    (tmp_path / "l.lock").unlink()
    assert ledger.read_totals() == expected
    assert not (tmp_path / "l.lock").exists()


def test_read_totals_skip_a_half_written_last_line(tmp_path: Path) -> None:
    ledger = ledger_with_a_call(tmp_path)
    expected = ledger.totals()
    with ledger.path.open("a") as f:
        f.write('{"type": "reserve", "id"')
    assert ledger.read_totals() == expected


def test_read_totals_still_fail_on_a_damaged_middle_line(tmp_path: Path) -> None:
    ledger = ledger_with_a_call(tmp_path)
    lines = ledger.path.read_text().splitlines()
    ledger.path.write_text("\n".join([lines[0], "{broken", lines[1]]) + "\n")
    with pytest.raises(json.JSONDecodeError):
        ledger.read_totals()


def test_llm_spend_needs_no_writable_lock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = ledger_with_a_call(tmp_path)
    lock = tmp_path / "l.lock"
    lock.chmod(0o400)
    assert cli.main(["spend", "--ledger", str(ledger.path)]) == 0
    assert "$0.500000" in capsys.readouterr().out


# -- runner-check (item 2) -------------------------------------------------------------------


def test_load_paths_covers_the_interpreter_and_the_import_path(tmp_path: Path) -> None:
    import sys

    paths = runner_check.load_paths(Path(sys.executable))
    assert Path(sys.executable).resolve().parent in paths
    assert Path(os.__file__).resolve().parent in paths
    assert all(path.is_dir() for path in paths)


def test_load_paths_ignores_the_callers_pythonpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    assert tmp_path not in runner_check.load_paths(Path(sys.executable))


def test_check_reports_a_writable_directory_and_leaves_no_probe(tmp_path: Path) -> None:
    assert runner_check.check([tmp_path], [], []) == [f"WRITABLE {tmp_path}"]
    assert list(tmp_path.iterdir()) == []


def test_check_reports_a_writable_file_and_leaves_it_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "ledger"
    path.write_text("x")
    assert runner_check.check([], [path], []) == [f"WRITABLE {path}"]
    assert path.read_text() == "x"


def test_check_passes_what_it_cannot_write(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    locked = tmp_path / "locked"
    locked.mkdir()
    path = locked / "file"
    path.write_text("x")
    path.chmod(0o400)
    locked.chmod(0o500)
    try:
        assert runner_check.check([locked], [path], []) == []
    finally:
        locked.chmod(0o700)


def test_a_denied_stat_counts_as_refused_and_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path in denyRead raises on stat, as the ledger lock does after install."""
    path = tmp_path / "lock"
    path.write_text("")
    real_stat = Path.stat

    def denied(self: Path, **kwargs: object) -> os.stat_result:
        if self == path:
            raise PermissionError(1, "Operation not permitted")
        return real_stat(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", denied)
    assert runner_check.check([], [path], []) == []


def settings_file(tmp_path: Path, sandbox: dict[str, object]) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"sandbox": sandbox}))
    return path


def test_a_checkout_with_exclusions_is_reported(tmp_path: Path) -> None:
    path = settings_file(tmp_path, {"enabled": True, "excludedCommands": ["make record SUITE=*"]})
    assert [line.split()[0] for line in runner_check.excluded_commands(path)] == ["EXCLUSIONS"]


def test_a_checkout_without_the_sandbox_is_reported(tmp_path: Path) -> None:
    problems = runner_check.excluded_commands(settings_file(tmp_path, {}))
    assert problems == [f"NO SANDBOX {tmp_path / 'settings.json'}"]


def test_a_checkout_on_the_new_settings_passes(tmp_path: Path) -> None:
    assert runner_check.excluded_commands(settings_file(tmp_path, {"enabled": True})) == []


def test_a_missing_settings_file_is_reported(tmp_path: Path) -> None:
    assert runner_check.excluded_commands(tmp_path / "none.json")


def test_every_checkout_of_this_repo_is_listed() -> None:
    assert REPO in [path.resolve() for path in runner_check.checkouts()]


def test_a_key_file_that_is_not_there_proves_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "key"
    assert runner_check.check([], [], [missing]) == [
        f"MISSING {missing}: nothing was proved about it"
    ]


def test_a_denied_key_file_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(_: Path) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(Path, "stat", denied)
    assert runner_check.is_hidden(tmp_path / "key")


def test_check_reports_a_key_file_it_can_see_without_opening_it(tmp_path: Path) -> None:
    path = tmp_path / "key"
    path.write_text("x")
    path.chmod(0)
    try:
        assert runner_check.check([], [], [path]) == [f"VISIBLE {path}"]
    finally:
        path.chmod(0o600)


def test_a_missing_runner_is_an_error_not_a_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert runner_check.main(tmp_path / "nothing") == 2
    assert "no runner interpreter" in capsys.readouterr().out


# -- the pytest plugin -----------------------------------------------------------------------


PLUGIN_TEST = """
import pytest

@pytest.mark.llm_suite("demo")
def test_x(llm_proxy):
    pass
"""


def test_record_with_no_window_stops_with_the_command_to_open_one(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("LLM_MODE", "record")
    monkeypatch.setenv("LLM_RUNNER_PORT", "1")
    pytester.makepyfile(PLUGIN_TEST)
    result = pytester.runpytest_subprocess()
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*llm-runner SUITE=demo RUN_BUDGET=*"])


def test_a_spending_run_refuses_xdist_workers(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("LLM_MODE", "record")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    pytester.makepyfile("def test_x(): pass")
    result = pytester.runpytest_subprocess()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*one claimed process*"])


def test_replay_runs_under_xdist_workers(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    pytester.makepyfile("def test_x(): pass")
    assert pytester.runpytest_subprocess().ret == pytest.ExitCode.OK
