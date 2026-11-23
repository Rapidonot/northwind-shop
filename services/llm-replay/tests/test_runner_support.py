"""CC-27 F1/F3 (decision 0005): what surrounds the runner. Make targets run against shims, so
nothing real starts, and nothing here reaches the network or a real key."""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from northwind_common import localenv
from northwind_llm_replay import cli, runner_check
from northwind_llm_replay.ledger import Ledger
from northwind_llm_replay.scan import SYMLINK_REFUSED, scan_paths
from northwind_llm_replay.scripted import blocks_sse

pytestmark = pytest.mark.req("CC-27")
pytest_plugins = ["pytester"]

REPO = Path(__file__).resolve().parents[3]
FAKE_KEY = "sk-ant-" + "supportfake00000000000"  # fake; built at runtime
SHIM = '#!/bin/sh\necho "$(basename "$0") $*"\n'


def rig(tmp_path: Path, *tools: str) -> dict[str, str]:
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text())
    (tmp_path / "tools").mkdir(exist_ok=True)
    (tmp_path / "tools" / "refused-paths.sh").write_text(
        (REPO / "tools" / "refused-paths.sh").read_text()
    )
    (tmp_path / "tmp").mkdir(exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in tools:
        (bin_dir / tool).write_text(SHIM)
        (bin_dir / tool).chmod(0o755)
    return {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path / "tmp"),
        "LLM_RUNNER_PORT": "1",
    }


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
    assert lines[-1] == "uv sync --frozen --no-dev --package northwind-rag"


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
        "data/src",
        "**/sitecustomize.py",
        "**/usercustomize.py",
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


def test_inside_the_clone_model_runs_on_its_own_small_environment(tmp_path: Path) -> None:
    """The full .venv has a .pth per workspace member: a sitecustomize.py in any would load."""
    env = rig(tmp_path)
    python = tmp_path / ".venv-model" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(SHIM)
    python.chmod(0o755)
    done = make(tmp_path, env, "model", f"RUNNER_DIR={tmp_path}")
    assert done.stdout.startswith("python -I -c ")


def test_outside_the_clone_model_uses_uv(tmp_path: Path) -> None:
    env = rig(tmp_path, "uv")
    done = make(tmp_path, env, "model")
    assert done.stdout.startswith("uv run python -c ")


def test_inside_the_clone_uv_stays_off_the_shared_cache_and_pythonpath_goes(tmp_path: Path) -> None:
    env = rig(tmp_path, "uv")
    env["PYTHONPATH"] = "/somewhere"
    (tmp_path / "bin" / "uv").write_text(
        '#!/bin/sh\necho "uv cache=$UV_CACHE_DIR frozen=$UV_FROZEN path=$PYTHONPATH"\n'
    )
    done = make(tmp_path, env, "data", f"RUNNER_DIR={tmp_path}")
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


def test_the_scan_never_says_where_a_fragment_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Neither by path, nor by its place among the lines that match a key's shape."""
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    key = FAKE_KEY
    (cassettes / "hit.txt").write_text(f"sk-ant-aaaaaaaaaa\n{key}\nsk-ant-bbbbbbbbbb\n")
    (cassettes / "fragment-only.txt").write_text(key[7:23])
    key_file = tmp_path / "key"
    key_file.write_text(f"ANTHROPIC_API_KEY={key}\n")
    answer_key = tmp_path / "data" / "fixtures" / "_answer_key.json"
    answer_key.parent.mkdir(parents=True)
    answer_key.write_text(json.dumps({"internal_only": []}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_LEDGER_PATH", str(tmp_path / "l.jsonl"))
    monkeypatch.setattr("northwind_common.localenv.worktree_root", lambda: tmp_path)
    code = cli.main(["scan", "--cassettes-dir", str(cassettes), "--env-file", str(key_file)])
    err = capsys.readouterr().err
    assert code == 1
    assert "fragment-only.txt" not in err
    assert err.strip().splitlines()[-1].endswith("ROTATE THE KEY")


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
    assert "ROTATE THE KEY" in captured.err
    assert "api-key-fragment" not in captured.err  # a guess per line must not be locatable
    assert "regex + key-fragment" in captured.out
    assert FAKE_KEY[:16] not in captured.out + captured.err


@pytest.mark.req("SA-02")
def test_a_key_split_across_deltas_is_reported_without_its_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Decision 0005: a hit must not be locatable, split across deltas or not."""
    cassettes = tmp_path / "cassettes" / "suite"
    cassettes.mkdir(parents=True)
    chunks = [FAKE_KEY[i : i + 8] for i in range(0, len(FAKE_KEY), 8)]
    usage = {"input_tokens": 1, "output_tokens": 1}
    body = blocks_sse("m", [{"type": "text", "chunks": chunks}], "end_turn", usage, "msg_1")
    planted = cassettes / "planted-guess.json"
    planted.write_text(json.dumps({"body": body, "recorded_at": "2026-11-04T00:00:00+00:00"}))
    key_file = tmp_path / "key"
    key_file.write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\n")
    answer_key = tmp_path / "data" / "fixtures" / "_answer_key.json"
    answer_key.parent.mkdir(parents=True)
    answer_key.write_text(json.dumps({"internal_only": []}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_LEDGER_PATH", str(tmp_path / "l.jsonl"))
    monkeypatch.setattr("northwind_common.localenv.worktree_root", lambda: tmp_path)
    code = cli.main(
        ["scan", "--cassettes-dir", str(tmp_path / "cassettes"), "--env-file", str(key_file)]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "ROTATE THE KEY" in captured.err
    assert "api-key-fragment" not in captured.err


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
    """A path in denyRead raises on stat, as the ledger lock does after install, and the sandbox
    refuses the open too. Row 5b: the open is now really tried, so the rig has to refuse it. A
    denied stat over a file that can be opened is reported: see the M1 test below."""
    path = tmp_path / "lock"
    path.write_text("")
    real_stat = Path.stat
    real_open = os.open

    def denied(self: Path, **kwargs: object) -> os.stat_result:
        if self == path:
            raise PermissionError(1, "Operation not permitted")
        return real_stat(self, **kwargs)  # type: ignore[arg-type]

    def refused(name: object, *args: object, **kwargs: object) -> int:
        if name == path:
            raise PermissionError(1, "Operation not permitted")
        return real_open(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", denied)
    monkeypatch.setattr(os, "open", refused)
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


def test_a_local_settings_file_needs_no_sandbox_block_but_may_not_exclude(tmp_path: Path) -> None:
    clean = settings_file(tmp_path, {})
    assert runner_check.excluded_commands(clean, must_enable=False) == []
    bad = settings_file(tmp_path, {"excludedCommands": ["make record SUITE=*"]})
    assert runner_check.excluded_commands(bad, must_enable=False)


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
    # Under xdist the child inherits the worker's marker, and the plugin then refuses the run before
    # it reaches the window check this test is about (DEV-1). The refusal has its own test, below.
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
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


@pytest.mark.req("DEV-1")
def test_a_spending_run_with_real_xdist_workers_refuses_before_any_claim(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEV-1, security review L1: the test above fakes the worker marker. This one starts real
    workers, so a later xdist that set the marker after configure (or renamed it) turns it red."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setenv("LLM_MODE", "record")
    monkeypatch.setenv("LLM_RUNNER_PORT", "1")  # a claim attempt would fail with the window hint
    pytester.makepyfile(PLUGIN_TEST)
    result = pytester.runpytest_subprocess("-n", "2")
    assert result.ret != pytest.ExitCode.OK
    output = result.stdout.str() + result.stderr.str()
    assert "a window is one claimed process: record without xdist workers" in output
    assert "llm-runner SUITE=demo" not in output  # no worker reached the claim
    assert result.parseoutcomes().get("passed", 0) == 0


def test_replay_runs_under_xdist_workers(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    pytester.makepyfile("def test_x(): pass")
    assert pytester.runpytest_subprocess().ret == pytest.ExitCode.OK


# --- git redirect files (row 5a, second review finding 1) ---------------------------------------


def linked_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A real main checkout and one real linked worktree."""
    main, tree = tmp_path / "main", tmp_path / "tree"
    main.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        [
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "base",
        ],
        ["worktree", "add", "-q", "-b", "side", str(tree)],
    ):
        subprocess.run(["git", *args], cwd=main, check=True, capture_output=True)  # noqa: S603, S607
    return main, tree


@pytest.mark.req("RAG-12")
def test_redirect_files_are_the_pointer_and_three_admin_files(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    admin = main / ".git" / "worktrees" / "tree"
    existing, absent = runner_check.git_redirect_files(main / ".git", [main, tree])
    assert existing == [tree / ".git", admin / "gitdir", admin / "commondir"]
    assert absent == [admin / "config.worktree"]


@pytest.mark.req("RAG-12")
def test_a_main_checkouts_git_directory_is_never_listed(tmp_path: Path) -> None:
    """A commit there needs that directory, and the session in it must be able to commit."""
    main, _ = linked_worktree(tmp_path)
    existing, absent = runner_check.git_redirect_files(main / ".git", [main])
    assert main / ".git" not in [*existing, *absent]


@pytest.mark.req("RAG-12")
def test_commit_state_is_not_listed(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    existing, absent = runner_check.git_redirect_files(main / ".git", [main, tree])
    names = {path.name for path in [*existing, *absent]}
    assert not names & {"HEAD", "ORIG_HEAD", "index", "refs", "logs", "config"}


@pytest.mark.req("RAG-12")
def test_no_linked_worktree_means_nothing_to_list(tmp_path: Path) -> None:
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=main, check=True)  # noqa: S603, S607
    assert runner_check.git_redirect_files(main / ".git", [main]) == ([], [])


@pytest.mark.req("RAG-12")
def test_checking_a_redirect_file_leaves_it_unchanged(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    existing, _ = runner_check.git_redirect_files(main / ".git", [main, tree])
    before = [path.read_bytes() for path in existing]
    assert runner_check.check([], existing, []) == [f"WRITABLE {path}" for path in existing]
    assert [path.read_bytes() for path in existing] == before


@pytest.mark.req("RAG-12")
def test_can_create_reports_a_creatable_file_and_leaves_nothing(tmp_path: Path) -> None:
    path = tmp_path / "config.worktree"
    assert runner_check.can_create(path)
    assert not path.exists()


@pytest.mark.req("RAG-12")
def test_can_create_is_false_when_refused_and_never_touches_an_existing_file(
    tmp_path: Path,
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o555)
    try:
        assert not runner_check.can_create(locked / "config.worktree")
    finally:
        locked.chmod(0o755)
    existing = tmp_path / "config.worktree"
    existing.write_text("keep", encoding="utf-8")
    assert not runner_check.can_create(existing)
    assert existing.read_text(encoding="utf-8") == "keep"


@pytest.mark.req("RAG-12")
def test_main_reports_a_writable_path_a_creatable_file_and_a_pricing_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The wiring in `main()`: the real `check`, `git_redirect_files` and `can_create`, against a
    throwaway repo. Unit tests of those functions stay green if `main()` stops calling them."""
    main, tree = linked_worktree(tmp_path)
    admin = main / ".git" / "worktrees" / "tree"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(runner_check, "load_paths", lambda _python: [])
    monkeypatch.setattr(runner_check, "checkouts", lambda: [main, tree])
    monkeypatch.setattr(localenv, "main_checkout", lambda: main)
    monkeypatch.setattr(localenv, "ledger_path", lambda: tmp_path / "ledger.jsonl")
    monkeypatch.setattr(localenv, "env_file", lambda: tmp_path / "key-file")
    python = tmp_path / "fake-python"
    python.write_text("#!/bin/sh\necho 'no module named pricing' >&2\nexit 1\n")
    python.chmod(0o755)

    assert runner_check.main(python) == 1

    lines = capsys.readouterr().out.splitlines()
    assert f"WRITABLE {tree / '.git'}" in lines  # a writable path
    assert f"WRITABLE {admin / 'config.worktree'}" in lines  # a creatable absent file
    assert "PRICING the runner cannot load its prices: no module named pricing" in lines
    assert not (admin / "config.worktree").exists()


# --- row 5b: the merge review's findings on runner_check.py -----------------------------------


def wired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trees: list[Path] | None = None
) -> tuple[Path, Path, Path]:
    """`main()` against a throwaway repo and home. Returns the main checkout, tree and python."""
    main, tree = linked_worktree(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(runner_check, "load_paths", lambda _python: [])
    monkeypatch.setattr(runner_check, "checkouts", lambda: trees or [main, tree])
    monkeypatch.setattr(localenv, "main_checkout", lambda: main)
    monkeypatch.setattr(localenv, "ledger_path", lambda: tmp_path / "ledger.jsonl")
    monkeypatch.setattr(localenv, "env_file", lambda: tmp_path / "key-file")
    python = tmp_path / "fake-python"
    python.write_text("#!/bin/sh\necho northwind_common.pricing\n")
    python.chmod(0o755)
    return main, tree, python


def stat_denied(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """A path in denyRead raises on stat and lstat alike, as the ledger lock does."""
    real_stat = Path.stat

    def denied(self: Path, **kwargs: object) -> os.stat_result:
        if self == path:
            raise PermissionError(1, "Operation not permitted")
        return real_stat(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", denied)


@pytest.mark.req("RAG-12")
def test_m1_an_absent_file_that_could_be_created_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "gitconfig"
    assert runner_check.check([], [path], []) == [f"WRITABLE {path}"]
    assert not path.exists()


@pytest.mark.req("RAG-12")
def test_m1_an_absent_directory_that_could_be_created_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b"
    assert runner_check.check([path], [], []) == [f"WRITABLE {path}"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.req("RAG-12")
def test_m1_a_file_under_an_absent_directory_is_not_read_as_safe(tmp_path: Path) -> None:
    """`~/.config/git/config` with no `~/.config/git`: creating it gives ENOENT, which is not a
    refusal. Only the first missing component says whether the path could come to exist."""
    path = tmp_path / "a" / "b" / "config"
    assert runner_check.check([], [path], []) == [f"WRITABLE {path}"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.req("RAG-12")
def test_m1_absent_paths_under_a_refused_parent_pass(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o555)
    try:
        assert runner_check.check([locked / "d"], [locked / "f", locked / "g" / "f"], []) == []
    finally:
        locked.chmod(0o755)


@pytest.mark.req("RAG-12")
def test_m1_a_stat_denied_file_is_probed_in_place_and_never_read_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creating an existing file fails with EEXIST, which would read as refused."""
    path = tmp_path / "lock"
    path.write_text("x")
    stat_denied(monkeypatch, path)
    assert runner_check.check([], [path], []) == [f"WRITABLE {path}"]
    assert path.read_text() == "x"


@pytest.mark.req("RAG-12")
def test_m1_main_measures_the_user_git_config_and_the_main_config_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    main, _, python = wired(tmp_path, monkeypatch)
    assert runner_check.main(python) == 1
    lines = capsys.readouterr().out.splitlines()
    assert f"WRITABLE {tmp_path / 'home' / '.config' / 'git' / 'config'}" in lines
    assert f"WRITABLE {main / '.git' / 'config.worktree'}" in lines
    assert not (main / ".git" / "config.worktree").exists()
    assert not (tmp_path / "home" / ".config").exists()


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("shape", ["directory", "missing", "fifo"])
def test_m2_a_linked_worktree_whose_git_is_not_a_regular_file_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    shape: str,
) -> None:
    _, tree, python = wired(tmp_path, monkeypatch)
    pointer = tree / ".git"
    pointer.unlink()
    if shape == "directory":
        pointer.mkdir()
    elif shape == "fifo":
        os.mkfifo(pointer)
    assert runner_check.main(python) == 1
    assert f"NOT A FILE {pointer}" in capsys.readouterr().out.splitlines()


@pytest.mark.req("RAG-12")
def test_m2_only_the_main_checkout_is_exempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    main, _, python = wired(tmp_path, monkeypatch)
    runner_check.main(python)
    assert f"NOT A FILE {main / '.git'}" not in capsys.readouterr().out.splitlines()


@pytest.mark.req("RAG-12")
def test_m3_a_dangling_symlink_is_reported_not_read_as_refused(tmp_path: Path) -> None:
    link = tmp_path / "gitdir"
    link.symlink_to(tmp_path / "nowhere")
    assert runner_check.check([], [link], []) == [f"SYMLINK {link}"]
    assert not (tmp_path / "nowhere").exists()


@pytest.mark.req("RAG-12")
def test_m3_a_live_symlink_is_reported_and_its_target_is_left_alone(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("x")
    link = tmp_path / "gitdir"
    link.symlink_to(target)
    assert runner_check.check([], [link], []) == [f"SYMLINK {link}"]
    assert target.read_text() == "x"


@pytest.mark.req("RAG-12")
def test_m3_a_dangling_symlink_at_an_admin_file_reaches_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    main, _, python = wired(tmp_path, monkeypatch)
    admin = main / ".git" / "worktrees" / "tree" / "config.worktree"
    admin.symlink_to(tmp_path / "nowhere")
    assert runner_check.main(python) == 1
    assert f"SYMLINK {admin}" in capsys.readouterr().out.splitlines()


@pytest.mark.req("RAG-12")
def test_l1_a_failing_stage_is_a_line_and_keeps_what_was_collected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, tree, python = wired(tmp_path, monkeypatch)

    def boom(_python: Path) -> tuple[str | None, list[str]]:
        raise OSError("no such thing")

    monkeypatch.setattr(runner_check, "pricing_module", boom)
    assert runner_check.main(python) == 1
    lines = capsys.readouterr().out.splitlines()
    assert f"WRITABLE {tree / '.git'}" in lines
    assert any(line.startswith("ERROR pricing: ") for line in lines)


@pytest.mark.req("RAG-12")
def test_l1_a_failing_worktree_list_still_runs_the_other_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, python = wired(tmp_path, monkeypatch)

    def boom() -> list[Path]:
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(runner_check, "checkouts", boom)
    assert runner_check.main(python) == 1
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("ERROR checkouts: ") for line in lines)
    assert f"WRITABLE {tmp_path / 'ledger.jsonl'}" in lines
    assert "runner prices from: northwind_common.pricing" in lines


@pytest.mark.req("RAG-12")
def test_l1_checkouts_is_called_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    main, tree, python = wired(tmp_path, monkeypatch)
    calls: list[int] = []

    def counted() -> list[Path]:
        calls.append(1)
        return [main, tree]

    monkeypatch.setattr(runner_check, "checkouts", counted)
    runner_check.main(python)
    assert len(calls) == 1


@pytest.mark.req("RAG-12")
def test_l2_a_missing_admin_folder_is_the_only_empty_one(tmp_path: Path) -> None:
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    assert runner_check.git_redirect_files(main / ".git", [main]) == ([], [])
    (main / ".git" / "worktrees").write_text("not a folder")
    with pytest.raises(OSError):
        runner_check.git_redirect_files(main / ".git", [main])


@pytest.mark.req("RAG-12")
def test_l2_an_unlistable_admin_folder_is_reported_not_read_as_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    main, _, python = wired(tmp_path, monkeypatch)
    admin = main / ".git" / "worktrees"
    admin.chmod(0o000)
    try:
        assert runner_check.main(python) == 1
    finally:
        admin.chmod(0o755)
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("ERROR redirect files: ") for line in lines)


@pytest.mark.req("RAG-12")
def test_l2_a_git_dir_that_is_not_a_directory_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = tmp_path / "fake-main"
    fake.mkdir()
    (fake / ".git").write_text("gitdir: elsewhere\n")
    _, tree, python = wired(tmp_path, monkeypatch)
    monkeypatch.setattr(localenv, "main_checkout", lambda: fake)
    assert runner_check.main(python) == 1
    assert f"NOT A DIRECTORY {fake / '.git'}" in capsys.readouterr().out.splitlines()


@pytest.mark.req("RAG-12")
def test_l3_the_replacement_limit_is_stated_in_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, python = wired(tmp_path, monkeypatch)
    runner_check.main(python)
    assert runner_check.REPLACEMENT_LIMIT in capsys.readouterr().out.splitlines()
    assert "replacing a file through its folder" in runner_check.REPLACEMENT_LIMIT


@pytest.mark.req("RAG-12")
def test_l4_a_failed_cleanup_is_reported_and_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.worktree"

    def refused(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "unlink", refused)
    assert runner_check.can_create(path)
    assert runner_check.dir_is_writable(tmp_path)
    out = capsys.readouterr().out
    assert f"LEFT BEHIND {path}" in out
    assert f"LEFT BEHIND {tmp_path}/.runner-check-" in out


@pytest.mark.req("RAG-12")
def test_l5_a_fifo_with_no_reader_does_not_hang_and_is_reported(tmp_path: Path) -> None:
    fifo = tmp_path / "ledger"
    os.mkfifo(fifo)
    result: list[object] = []

    def probe() -> None:
        result.append(runner_check.file_is_writable(fifo))
        result.append(runner_check.check([], [fifo], []))

    worker = threading.Thread(target=probe, daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "opening a FIFO hung"
    assert result == [False, [f"NOT A FILE {fifo}"]]


@pytest.mark.req("RAG-12")
def test_l5_probing_an_absent_file_never_creates_it(tmp_path: Path) -> None:
    path = tmp_path / "absent"
    assert runner_check.file_is_writable(path) is False
    assert not path.exists()


@pytest.mark.req("RAG-12")
def test_l6_printed_paths_are_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Names are the sandbox's to choose, and an escape sequence could hide a failing line."""
    hostile = tmp_path / "tree\x1b[2Kok"
    hostile.mkdir()
    (hostile / ".git").write_text("gitdir: elsewhere\n")
    main = tmp_path / "main"
    _, _, python = wired(tmp_path, monkeypatch, trees=[main, hostile])
    assert runner_check.main(python) == 1
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "tree\\x1b[2Kok" in out


# --- row 5b: gaps found by trying to break the findings above ---------------------------------


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("as_file", [True, False])
def test_m1_a_path_under_a_dangling_symlinked_folder_is_not_read_as_safe(
    tmp_path: Path, as_file: bool
) -> None:
    """ENOENT through a dangling link says nothing: creating the target makes the path real."""
    link = tmp_path / "dir"
    link.symlink_to(tmp_path / "nowhere")
    path = link / "config"
    problems = runner_check.check([] if as_file else [path], [path] if as_file else [], [])
    assert problems == [f"SYMLINK {link}"], "a path behind a dangling link was read as refused"
    assert not (tmp_path / "nowhere").exists()


@pytest.mark.req("RAG-12")
def test_m1_a_dangling_symlinked_admin_folder_reaches_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    main, _, python = wired(tmp_path, monkeypatch)
    planted = main / ".git" / "worktrees" / "planted"
    planted.symlink_to(tmp_path / "nowhere")
    assert runner_check.main(python) == 1
    assert f"SYMLINK {planted}" in capsys.readouterr().out.splitlines()


@pytest.mark.req("RAG-12")
def test_m1_a_probe_never_leaves_a_folder_it_made_when_it_cannot_remove_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "rmdir", refused)
    path = tmp_path / "a" / "config"
    assert runner_check.check([], [path], []) == [f"WRITABLE {path}"]
    assert f"LEFT BEHIND {tmp_path / 'a'}" in capsys.readouterr().out


@pytest.mark.req("RAG-12")
def test_m1_a_file_in_the_way_of_a_path_is_not_a_creatable_one(tmp_path: Path) -> None:
    """Row 5b review: nothing was measured there, so it is a finding, not a clean result."""
    (tmp_path / "f").write_text("x")
    problems = runner_check.check([tmp_path / "f" / "d"], [tmp_path / "f" / "g"], [])
    assert problems == [f"NOT A DIRECTORY {tmp_path / 'f'}"] * 2
    assert (tmp_path / "f").read_text() == "x"


@pytest.mark.req("RAG-12")
def test_m2_a_linked_worktree_whose_git_is_a_symlink_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, tree, python = wired(tmp_path, monkeypatch)
    pointer = tree / ".git"
    real = tmp_path / "real-pointer"
    pointer.rename(real)
    pointer.symlink_to(real)
    assert runner_check.main(python) == 1
    out = capsys.readouterr().out.splitlines()
    assert f"SYMLINK {pointer}" in out
    assert real.read_text().startswith("gitdir:")


@pytest.mark.req("RAG-12")
def test_m2_the_main_checkout_listed_by_a_symlinked_path_is_still_exempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    main, tree, python = wired(tmp_path, monkeypatch)
    alias = tmp_path / "alias-of-main"
    alias.symlink_to(main)
    monkeypatch.setattr(runner_check, "checkouts", lambda: [alias, tree])
    runner_check.main(python)
    out = capsys.readouterr().out
    assert "NOT A FILE" not in out


@pytest.mark.req("RAG-12")
def test_m3_a_main_git_dir_that_is_a_symlink_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, python = wired(tmp_path, monkeypatch)
    fake = tmp_path / "fake-main"
    fake.mkdir()
    (fake / ".git").symlink_to(tmp_path / "main" / ".git")
    monkeypatch.setattr(localenv, "main_checkout", lambda: fake)
    assert runner_check.main(python) == 1
    assert f"NOT A DIRECTORY {fake / '.git'}" in capsys.readouterr().out.splitlines()


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("stage", ["load_paths", "checkouts", "pricing_module"])
def test_l1_every_stage_failure_is_a_line_and_the_exit_is_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    _, tree, python = wired(tmp_path, monkeypatch)

    def boom(*_args: object) -> None:
        raise RuntimeError("stage broke")

    monkeypatch.setattr(runner_check, stage, boom)
    assert runner_check.main(python) == 1
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("ERROR ") and "stage broke" in line for line in lines)
    assert f"WRITABLE {tmp_path / 'ledger.jsonl'}" in lines or stage == "checkouts"
    assert any(line.startswith("runner-check: ") for line in lines)


@pytest.mark.req("RAG-12")
def test_l1_a_failing_measurement_keeps_the_redirect_and_settings_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, tree, python = wired(tmp_path, monkeypatch)

    def boom() -> Path:
        raise OSError("no ledger path")

    monkeypatch.setattr(localenv, "ledger_path", boom)
    assert runner_check.main(python) == 1
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("ERROR measure: ") for line in lines)
    assert f"WRITABLE {tree / '.git'}" in lines
    assert any(line.startswith("UNREADABLE ") for line in lines)


@pytest.mark.req("RAG-12")
def test_l1_a_clean_run_with_no_findings_exits_zero(tmp_path: Path) -> None:
    """The other side: a path can still read as safe, or the check proves nothing."""
    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o555)
    gone = tmp_path / "gone"
    try:
        assert runner_check.check([locked], [locked / "f"], []) == []
        assert runner_check.check([gone], [], []) == [f"WRITABLE {gone}"]
    finally:
        locked.chmod(0o755)


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("hostile", ["x\x1b[2Kok", "x\nrunner-check: 0 directories, 0 problem(s)"])
def test_l6_every_printed_line_escapes_what_it_prints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    hostile: str,
) -> None:
    """Not only paths: an error's own text, the module name and the interpreter path as well."""
    _, _, python = wired(tmp_path, monkeypatch)
    python.write_text(f"#!/bin/sh\nprintf '%s\\n' '{hostile}'\n")
    boom = RuntimeError(hostile)

    def raising(*_args: object) -> None:
        raise boom

    monkeypatch.setattr(runner_check, "settings_problems", raising)
    runner_check.main(python)
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert sum(line.startswith("runner-check: ") for line in out.splitlines()) == 1


@pytest.mark.req("RAG-12")
def test_l6_a_bidi_override_in_a_name_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hostile = tmp_path / "tree‮ok"
    hostile.mkdir()
    (hostile / ".git").write_text("gitdir: elsewhere\n")
    _, _, python = wired(tmp_path, monkeypatch, trees=[tmp_path / "main", hostile])
    runner_check.main(python)
    assert "‮" not in capsys.readouterr().out


@pytest.mark.req("RAG-12")
def test_l6_a_missing_interpreter_path_is_escaped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert runner_check.main(tmp_path / "py\x1b[2Kthon") == 2
    assert "\x1b" not in capsys.readouterr().out


# -- after the triage (Runner 5, 13; addition b) ------------------------------------------------


@pytest.mark.req("RAG-12")
def test_a_file_at_a_directory_path_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "hooks"
    path.write_text("x")
    assert runner_check.check([path], [], []) == [f"NOT A DIRECTORY {path}"]


@pytest.mark.req("RAG-12")
def test_a_stat_denied_absent_file_is_still_tried_with_a_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Append needs the file to exist, so on its own it cannot see that one could be created."""
    path = tmp_path / "lock"
    stat_denied(monkeypatch, path)
    assert runner_check.check([], [path], []) == [f"WRITABLE {path}"]
    assert not os.path.lexists(path)  # `Path.exists` would hit the denied stat


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("code", [errno.EMFILE, errno.ENOSPC, errno.EIO])
def test_only_a_refusal_counts_as_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """A full disk proves nothing about the sandbox, so it must not read as a clean result."""
    path = tmp_path / "ledger"
    path.write_text("x")

    def failing(*_args: object, **_kwargs: object) -> int:
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(os, "open", failing)
    [line] = runner_check.check([], [path], [])
    assert line.startswith(f"ERROR {path}")


@pytest.mark.req("RAG-12")
def test_one_path_that_cannot_be_measured_keeps_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good, bad = tmp_path / "good", tmp_path / "bad"
    good.write_text("x")
    bad.write_text("x")
    real_kind = runner_check.kind

    def failing(path: Path) -> str:
        if path == bad:
            raise OSError(errno.EIO, "I/O error")
        return real_kind(path)

    monkeypatch.setattr(runner_check, "kind", failing)
    lines = runner_check.check([], [bad, good], [])
    assert lines[0].startswith(f"ERROR {bad}")
    assert lines[1] == f"WRITABLE {good}"


@pytest.mark.req("RAG-12")
def test_main_measures_what_promote_trusts_in_the_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, python = wired(tmp_path, monkeypatch)
    runner_check.main(python)
    lines = capsys.readouterr().out.splitlines()
    clone = tmp_path / "home" / ".northwind" / "runner" / "evals"
    for path in (clone / "src" / "northwind_evals", clone / "fixtures", clone / "baselines"):
        assert f"WRITABLE {path}" in lines


@pytest.mark.req("RAG-12")
def test_l2_a_main_checkout_that_does_not_exist_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Addition b: the case `isolate_main` used to rely on, now asserted on purpose."""
    gone = tmp_path / "gone"
    _, _, python = wired(tmp_path, monkeypatch)
    monkeypatch.setattr(localenv, "main_checkout", lambda: gone)
    assert runner_check.main(python) == 1
    assert f"NOT A DIRECTORY {gone / '.git'}" in capsys.readouterr().out.splitlines()


CLONE_TARGETS = {
    "llm-runner": ["SUITE=a", "RUN_BUDGET=1"],
    "scan-cassettes": [],
    "model": [],
    "eval-promote": [],
    "llm-spend": [],
    "runner-sync": [],
}


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("target", CLONE_TARGETS)
def test_every_target_the_clone_runs_uses_an_isolated_python(tmp_path: Path, target: str) -> None:
    """Addition a: unsandboxed, so no recipe may load a working directory or a PYTHONPATH. Your
    shell's PYTHONPATH starts with `:`, which is an empty entry: the working directory."""
    env = rig(tmp_path)
    done = subprocess.run(  # noqa: S603
        ["make", "-n", target, f"RUNNER_DIR={tmp_path}", *CLONE_TARGETS[target]],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    for line in done.stdout.splitlines():
        assert "uv run" not in line, line
        if re.search(r"\bpython3?\b", line) and "/bin/python" in line:
            assert re.search(r"/bin/python -I\b", line), line


# --- AUTO-2: what would make `git status` outside the sandbox run a child ---------------------


def plain_repo(path: Path) -> tuple[Path, str]:
    path.mkdir()

    def git(*arguments: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed arguments, throwaway repo
            ["git", "-C", str(path), *arguments],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@x.invalid")
    git("config", "user.name", "t")
    (path / "README.md").write_text("x\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    return path, git("rev-parse", "HEAD")


def run_git(repo: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - fixed arguments, throwaway repo
        ["git", "-C", str(repo), *arguments],  # noqa: S607
        capture_output=True,
        check=True,
    )


@pytest.mark.req("AUTO-2")
def test_a_clean_checkout_has_no_git_problems(tmp_path: Path) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    assert runner_check.child_git_problems(repo) == []


@pytest.mark.req("AUTO-2")
def test_a_linked_worktrees_pointer_file_is_not_a_problem(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    assert runner_check.child_git_problems(main) == []
    assert runner_check.child_git_problems(tree) == []


@pytest.mark.req("AUTO-2")
def test_a_gitlink_in_the_index_is_reported(tmp_path: Path) -> None:
    repo, base = plain_repo(tmp_path / "repo")
    run_git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},sub")
    assert f"GITLINK {repo}: sub" in runner_check.child_git_problems(repo)


@pytest.mark.req("AUTO-2")
def test_a_gitlink_only_at_head_is_reported(tmp_path: Path) -> None:
    """Re-review finding A: the index alone is not enough."""
    repo, base = plain_repo(tmp_path / "repo")
    run_git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},sub")
    run_git(repo, "commit", "-q", "-m", "gitlink")
    run_git(repo, "update-index", "--force-remove", "sub")
    assert f"GITLINK {repo}: sub" in runner_check.child_git_problems(repo)


@pytest.mark.req("AUTO-2")
def test_a_tracked_gitmodules_is_reported_even_with_no_file_on_disk(tmp_path: Path) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    (repo / ".gitmodules").write_text("[submodule]\n")
    run_git(repo, "add", ".gitmodules")
    (repo / ".gitmodules").unlink()
    assert f"GITMODULES {repo}: .gitmodules" in runner_check.child_git_problems(repo)


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize("name", [".gitmodules", "sub/.gitmodules", "sub/.GitModules"])
def test_an_untracked_gitmodules_is_reported(tmp_path: Path, name: str) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    (repo / "sub").mkdir()
    (repo / name).write_text("[submodule]\n")
    assert f"GITMODULES {repo / name}" in runner_check.child_git_problems(repo)


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize("name", [".git", ".GIT", ".Git"])
def test_a_nested_git_file_is_reported_in_any_case(tmp_path: Path, name: str) -> None:
    """Measured: on this volume git finds `sub/.GIT`, and the child runs."""
    repo, _ = plain_repo(tmp_path / "repo")
    (repo / "a" / "sub").mkdir(parents=True)
    (repo / "a" / "sub" / name).write_text("gitdir: ../gd\n")
    assert f"NESTED GIT {repo / 'a' / 'sub' / name}" in runner_check.child_git_problems(repo)


@pytest.mark.req("AUTO-2")
def test_a_nested_git_folder_is_reported(tmp_path: Path) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    (repo / "sub" / ".git").mkdir(parents=True)
    assert f"NESTED GIT {repo / 'sub' / '.git'}" in runner_check.child_git_problems(repo)


@pytest.mark.req("AUTO-2")
def test_a_nested_git_symlink_is_reported_and_not_followed(tmp_path: Path) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    (repo / "sub").mkdir()
    (repo / "sub" / ".git").symlink_to(tmp_path / "nowhere")
    assert f"NESTED GIT {repo / 'sub' / '.git'}" in runner_check.child_git_problems(repo)


@pytest.mark.req("AUTO-2")
def test_a_nested_git_inside_an_ignored_folder_is_reported(tmp_path: Path) -> None:
    """`.git` under `node_modules` still makes a child run if a gitlink points there."""
    repo, _ = plain_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("node_modules\n")
    (repo / "node_modules" / "x").mkdir(parents=True)
    (repo / "node_modules" / "x" / ".git").write_text("gitdir: y\n")
    found = f"NESTED GIT {repo / 'node_modules' / 'x' / '.git'}"
    assert found in runner_check.child_git_problems(repo)


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize("name", [".gitignore", ".github", ".gitattributes", "x.git", ".gitkeep"])
def test_a_lookalike_name_is_not_a_problem(tmp_path: Path, name: str) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    (repo / "sub").mkdir()
    (repo / "sub" / name).write_text("x\n")
    assert runner_check.child_git_problems(repo) == []


@pytest.mark.req("AUTO-2")
def test_a_folder_that_cannot_be_read_is_an_error_not_a_clean_result(tmp_path: Path) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    locked = repo / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        lines = runner_check.child_git_problems(repo)
    finally:
        locked.chmod(0o700)
    assert any(line.startswith(f"ERROR {locked}") for line in lines)


@pytest.mark.req("AUTO-2")
def test_a_checkout_git_cannot_read_is_an_error_not_a_clean_result(tmp_path: Path) -> None:
    lines = runner_check.child_git_problems(tmp_path)
    assert any(line.startswith(f"ERROR {tmp_path}") for line in lines)


@pytest.mark.req("AUTO-2")
def test_the_git_commands_never_read_the_working_tree() -> None:
    """Sandboxed here, but it must stay safe from a terminal: no status, and no fsmonitor."""
    source = (REPO / "services/llm-replay/src/northwind_llm_replay/runner_check.py").read_text()
    assert "core.fsmonitor=false" in source
    assert '"status"' not in source


@pytest.mark.req("AUTO-2")
def test_a_replace_ref_cannot_hide_a_gitlink_at_head(tmp_path: Path) -> None:
    repo, base = plain_repo(tmp_path / "repo")
    clean = subprocess.run(  # noqa: S603 - fixed arguments, throwaway repo
        ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    run_git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},sub")
    run_git(repo, "commit", "-q", "-m", "gitlink")
    run_git(repo, "update-index", "--force-remove", "sub")
    run_git(repo, "replace", "HEAD^{tree}", clean)
    assert f"GITLINK {repo}: sub" in runner_check.child_git_problems(repo)


@pytest.mark.req("AUTO-2")
def test_main_reports_every_checkout_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The wiring: a unit test stays green if `main()` stops calling it."""
    main, tree, python = wired(tmp_path, monkeypatch)
    (main / "a").mkdir()
    (main / "a" / ".git").write_text("gitdir: x\n")
    (tree / "b").mkdir()
    (tree / "b" / ".GIT").write_text("gitdir: x\n")
    assert runner_check.main(python) == 1
    lines = capsys.readouterr().out.splitlines()
    assert f"NESTED GIT {main / 'a' / '.git'}" in lines
    assert f"NESTED GIT {tree / 'b' / '.GIT'}" in lines


@pytest.mark.req("AUTO-2")
def test_main_says_how_many_checkouts_it_searched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, python = wired(tmp_path, monkeypatch)
    runner_check.main(python)
    assert runner_check.CHILD_GIT_SUMMARY.format(2) in capsys.readouterr().out


# --- AUTO-2, second re-review: a worktree's admin folder, read and not only probed -----------


@pytest.mark.req("AUTO-2")
def test_a_real_linked_worktree_has_no_admin_problems(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    assert runner_check.admin_problems(main / ".git", [main, tree]) == []


@pytest.mark.req("AUTO-2")
def test_a_redirected_commondir_is_reported(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    file = main / ".git" / "worktrees" / "tree" / "commondir"
    file.write_text("/somewhere/else\n")
    assert f"REDIRECTED {file}" in runner_check.admin_problems(main / ".git", [main, tree])


@pytest.mark.req("AUTO-2")
def test_a_gitdir_for_no_checkout_is_reported(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    file = main / ".git" / "worktrees" / "tree" / "gitdir"
    file.write_text("/somewhere/else/.git\n")
    assert f"REDIRECTED {file}" in runner_check.admin_problems(main / ".git", [main, tree])


@pytest.mark.req("AUTO-2")
def test_a_config_worktree_is_reported(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    file = main / ".git" / "worktrees" / "tree" / "config.worktree"
    file.write_text("[core]\n")
    assert f"REDIRECTED {file}" in runner_check.admin_problems(main / ".git", [main, tree])


@pytest.mark.req("AUTO-2")
def test_an_admin_folder_git_does_not_list_is_reported(tmp_path: Path) -> None:
    """Second re-review: `git worktree list` skips one with no `gitdir`."""
    main, tree = linked_worktree(tmp_path)
    (main / ".git" / "worktrees" / "tree" / "gitdir").unlink()
    lines = runner_check.admin_problems(main / ".git", [main])
    assert any(line.startswith("MISSING ") and "gitdir" in line for line in lines), lines


@pytest.mark.req("AUTO-2")
def test_a_symlinked_admin_folder_is_reported(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    admin = main / ".git" / "worktrees" / "tree"
    moved = tmp_path / "moved"
    admin.rename(moved)
    admin.symlink_to(moved)
    assert f"SYMLINK {admin}" in runner_check.admin_problems(main / ".git", [main, tree])


@pytest.mark.req("AUTO-2")
def test_a_main_checkout_with_no_worktrees_folder_is_fine(tmp_path: Path) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    assert runner_check.admin_problems(repo / ".git", [repo]) == []


@pytest.mark.req("AUTO-2")
def test_main_reads_the_admin_folder_and_measures_commondir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    main, _, python = wired(tmp_path, monkeypatch)
    file = main / ".git" / "worktrees" / "tree" / "commondir"
    file.write_text("/somewhere/else\n")
    assert runner_check.main(python) == 1
    lines = capsys.readouterr().out.splitlines()
    assert f"REDIRECTED {file}" in lines
    assert f"WRITABLE {main / '.git' / 'commondir'}" in lines
    assert not (main / ".git" / "commondir").exists()


# --- AUTO-2, extra cases: what the first set of tests misses ---------------------------------


@pytest.mark.req("AUTO-2")
def test_a_gitlink_in_a_subfolder_is_named_by_its_path(tmp_path: Path) -> None:
    repo, base = plain_repo(tmp_path / "repo")
    run_git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},deep/er/sub")
    assert f"GITLINK {repo}: deep/er/sub" in runner_check.child_git_problems(repo)


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize("name", [".GITMODULES", "sub/.GitModules"])
def test_a_tracked_gitmodules_is_reported_in_any_case(tmp_path: Path, name: str) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    (repo / "sub").mkdir()
    (repo / name).write_text("[submodule]\n")
    run_git(repo, "add", name)
    (repo / name).unlink()
    assert f"GITMODULES {repo}: {name}" in runner_check.child_git_problems(repo)


@pytest.mark.req("AUTO-2")
def test_a_gitmodules_only_at_head_is_reported(tmp_path: Path) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    (repo / ".gitmodules").write_text("[submodule]\n")
    run_git(repo, "add", ".gitmodules")
    run_git(repo, "commit", "-q", "-m", "modules")
    run_git(repo, "rm", "-q", "--cached", ".gitmodules")
    (repo / ".gitmodules").unlink()
    assert f"GITMODULES {repo}: .gitmodules" in runner_check.child_git_problems(repo)


@pytest.mark.req("AUTO-2")
def test_every_problem_is_reported_not_only_the_first(tmp_path: Path) -> None:
    repo, base = plain_repo(tmp_path / "repo")
    run_git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},sub")
    (repo / "a").mkdir()
    (repo / "a" / ".git").write_text("gitdir: x\n")
    (repo / "b").mkdir()
    (repo / "b" / ".gitmodules").write_text("[submodule]\n")
    lines = runner_check.child_git_problems(repo)
    assert f"GITLINK {repo}: sub" in lines
    assert f"NESTED GIT {repo / 'a' / '.git'}" in lines
    assert f"GITMODULES {repo / 'b' / '.gitmodules'}" in lines


@pytest.mark.req("AUTO-2")
def test_a_symlinked_folder_is_not_followed_into_another_tree(tmp_path: Path) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    outside = tmp_path / "outside"
    (outside / "x").mkdir(parents=True)
    (outside / "x" / ".git").write_text("gitdir: y\n")
    (repo / "link").symlink_to(outside, target_is_directory=True)
    assert runner_check.child_git_problems(repo) == []


@pytest.mark.req("AUTO-2")
def test_a_symlink_named_git_to_a_folder_is_named_and_not_entered(tmp_path: Path) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    (repo / "sub").mkdir()
    (repo / "sub" / ".GIT").symlink_to(outside, target_is_directory=True)
    lines = runner_check.child_git_problems(repo)
    assert f"NESTED GIT {repo / 'sub' / '.GIT'}" in lines
    assert not any(str(outside) in line for line in lines)


@pytest.mark.req("AUTO-2")
def test_a_dangling_git_symlink_is_named_without_an_error(tmp_path: Path) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    (repo / "sub").mkdir()
    (repo / "sub" / ".git").symlink_to(tmp_path / "gone")
    lines = runner_check.child_git_problems(repo)
    assert lines == [f"NESTED GIT {repo / 'sub' / '.git'}"]


@pytest.mark.req("AUTO-2")
def test_what_lies_inside_a_nested_git_folder_is_not_listed_again(tmp_path: Path) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    (repo / "sub" / ".git" / "deeper" / ".git").mkdir(parents=True)
    (repo / "sub" / ".git" / ".gitmodules").write_text("x\n")
    assert runner_check.child_git_problems(repo) == [f"NESTED GIT {repo / 'sub' / '.git'}"]


@pytest.mark.req("AUTO-2")
def test_a_failing_git_is_an_error_line_not_a_clean_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    monkeypatch.setenv("PATH", str(tmp_path / "no-git-here"))
    lines = runner_check.child_git_problems(repo)
    assert lines, "git could not run and the result was clean"
    assert any(line.startswith("ERROR ") for line in lines)


@pytest.mark.req("AUTO-2")
def test_a_git_that_times_out_is_an_error_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = plain_repo(tmp_path / "repo")

    def hang(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired("git", 30)

    monkeypatch.setattr(subprocess, "run", hang)
    lines = runner_check.child_git_problems(repo)
    assert any(line.startswith(f"ERROR {repo}") for line in lines), lines


@pytest.mark.req("AUTO-2")
def test_an_unreadable_folder_does_not_hide_a_problem_beside_it(tmp_path: Path) -> None:
    repo, _ = plain_repo(tmp_path / "repo")
    (repo / "ok").mkdir()
    (repo / "ok" / ".git").write_text("gitdir: x\n")
    locked = repo / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        lines = runner_check.child_git_problems(repo)
    finally:
        locked.chmod(0o700)
    assert f"NESTED GIT {repo / 'ok' / '.git'}" in lines
    assert any(line.startswith(f"ERROR {locked}") for line in lines)


def admin(main: Path) -> Path:
    return main / ".git" / "worktrees" / "tree"


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize("text", ["../../\n", "../..\nextra\n", "..\n", "", "/abs/path\n"])
def test_a_commondir_that_is_not_exactly_two_levels_up_is_reported(
    tmp_path: Path, text: str
) -> None:
    main, tree = linked_worktree(tmp_path)
    file = admin(main) / "commondir"
    file.write_text(text)
    assert f"REDIRECTED {file}" in runner_check.admin_problems(main / ".git", [main, tree])


@pytest.mark.req("AUTO-2")
def test_a_missing_commondir_is_reported(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    (admin(main) / "commondir").unlink()
    lines = runner_check.admin_problems(main / ".git", [main, tree])
    assert f"MISSING {admin(main) / 'commondir'}" in lines


@pytest.mark.req("AUTO-2")
def test_a_gitdir_naming_a_checkout_that_is_not_listed_is_reported(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    file = admin(main) / "gitdir"
    assert f"REDIRECTED {file}" in runner_check.admin_problems(main / ".git", [main])


@pytest.mark.req("AUTO-2")
def test_a_non_folder_entry_under_worktrees_is_reported(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    stray = main / ".git" / "worktrees" / "stray"
    stray.write_text("not a folder\n")
    lines = runner_check.admin_problems(main / ".git", [main, tree])
    assert f"NOT A DIRECTORY {stray}" in lines
    assert lines == [f"NOT A DIRECTORY {stray}"]


@pytest.mark.req("AUTO-2")
def test_a_dangling_symlink_under_worktrees_is_reported(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    stray = main / ".git" / "worktrees" / "stray"
    stray.symlink_to(tmp_path / "gone")
    assert f"SYMLINK {stray}" in runner_check.admin_problems(main / ".git", [main, tree])


@pytest.mark.req("AUTO-2")
def test_an_empty_admin_folder_reports_both_missing_files(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    empty = main / ".git" / "worktrees" / "empty"
    empty.mkdir()
    lines = runner_check.admin_problems(main / ".git", [main, tree])
    assert f"MISSING {empty / 'commondir'}" in lines
    assert f"MISSING {empty / 'gitdir'}" in lines


@pytest.mark.req("AUTO-2")
def test_one_bad_admin_folder_does_not_hide_another(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    second = main / ".git" / "worktrees" / "second"
    second.mkdir()
    (second / "commondir").write_text("/else\n")
    (second / "gitdir").write_text(f"{tmp_path / 'elsewhere' / '.git'}\n")
    (second / "config.worktree").write_text("[core]\n")
    lines = runner_check.admin_problems(main / ".git", [main, tree])
    assert f"REDIRECTED {second / 'commondir'}" in lines
    assert f"REDIRECTED {second / 'gitdir'}" in lines
    assert f"REDIRECTED {second / 'config.worktree'}" in lines
    assert not any(str(admin(main)) in line for line in lines)


@pytest.mark.req("AUTO-2")
def test_an_unreadable_admin_file_is_an_error_not_a_clean_result(tmp_path: Path) -> None:
    main, tree = linked_worktree(tmp_path)
    file = admin(main) / "commondir"
    file.chmod(0)
    try:
        lines = runner_check.admin_problems(main / ".git", [main, tree])
    finally:
        file.chmod(0o644)
    folder = admin(main)
    assert any(line.startswith(f"ERROR {folder}") for line in lines), lines
    assert f"REDIRECTED {file}" not in lines


@pytest.mark.req("AUTO-2")
def test_main_reports_an_unreadable_worktrees_folder_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Called directly, `admin_problems` raises here; `main` must turn that into a line."""
    main, _, python = wired(tmp_path, monkeypatch)
    folder = main / ".git" / "worktrees"
    folder.chmod(0)
    try:
        code = runner_check.main(python)
    finally:
        folder.chmod(0o755)
    assert code == 1
    assert any(line.startswith("ERROR ") for line in capsys.readouterr().out.splitlines())


# --- AUTO-2, third read ----------------------------------------------------------------------


def sibling_worktree(tmp_path: Path, name: str = "main-b") -> tuple[Path, Path, Path]:
    """Named as the real ones are, beside the main checkout. Returns main, tree, admin folder."""
    main, _ = linked_worktree(tmp_path)
    tree = tmp_path / name
    run_git(main, "worktree", "add", "-q", "--detach", str(tree))
    return main, tree, main / ".git" / "worktrees" / name


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize("value", [" ../..", "../.. ", "\t../..", " ../..\n"])
def test_a_commondir_git_reads_differently_is_reported(tmp_path: Path, value: str) -> None:
    """Git trims line ends only, so ` ../..` is another path, which `strip()` read as clean."""
    main, tree = linked_worktree(tmp_path)
    file = main / ".git" / "worktrees" / "tree" / "commondir"
    file.write_text(value)
    assert f"REDIRECTED {file}" in runner_check.admin_problems(main / ".git", [main, tree])


@pytest.mark.req("AUTO-2")
def test_the_expected_checkouts_do_not_come_from_git_worktree_list(tmp_path: Path) -> None:
    """Git builds that list from the same `gitdir` files, so the check was circular."""
    main, tree, _ = sibling_worktree(tmp_path)
    assert runner_check.sibling_checkouts(main / ".git") == [main, tree]


@pytest.mark.req("AUTO-2")
def test_a_real_sibling_worktree_is_clean(tmp_path: Path) -> None:
    main, _, _ = sibling_worktree(tmp_path)
    siblings = runner_check.sibling_checkouts(main / ".git")
    lines = runner_check.admin_problems(main / ".git", siblings)
    assert [line for line in lines if "main-b" in line] == []


@pytest.mark.req("AUTO-2")
def test_a_gitdir_at_a_lookalike_outside_the_siblings_is_reported(tmp_path: Path) -> None:
    main, _, admin = sibling_worktree(tmp_path)
    lookalike = tmp_path / "else"
    lookalike.mkdir()
    (lookalike / ".git").write_text(f"gitdir: {admin}\n")
    (admin / "gitdir").write_text(f"{lookalike / '.git'}\n")
    siblings = runner_check.sibling_checkouts(main / ".git")
    assert f"REDIRECTED {admin / 'gitdir'}" in runner_check.admin_problems(main / ".git", siblings)


@pytest.mark.req("AUTO-2")
def test_a_gitdir_naming_another_worktree_is_reported(tmp_path: Path) -> None:
    main, tree, admin = sibling_worktree(tmp_path)
    other = tmp_path / "main-c"
    run_git(main, "worktree", "add", "-q", "--detach", str(other))
    (admin / "gitdir").write_text(f"{other / '.git'}\n")
    assert f"REDIRECTED {admin / 'gitdir'}" in runner_check.admin_problems(
        main / ".git", [main, tree, other]
    )


@pytest.mark.req("AUTO-2")
def test_a_gitdir_naming_the_main_git_is_reported(tmp_path: Path) -> None:
    main, tree, admin = sibling_worktree(tmp_path)
    (admin / "gitdir").write_text(f"{main / '.git'}\n")
    assert f"REDIRECTED {admin / 'gitdir'}" in runner_check.admin_problems(
        main / ".git", [main, tree]
    )


@pytest.mark.req("AUTO-2")
def test_a_relative_gitdir_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    main, tree, admin = sibling_worktree(tmp_path)
    (admin / "gitdir").write_text("main-b/.git\n")
    monkeypatch.chdir(tmp_path)
    assert f"REDIRECTED {admin / 'gitdir'}" in runner_check.admin_problems(
        main / ".git", [main, tree]
    )


@pytest.mark.req("AUTO-2")
def test_main_does_not_take_the_checkouts_from_git_for_the_admin_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`tree` here is not named as a sibling. Listing it must not vouch for it."""
    main, _, python = wired(tmp_path, monkeypatch)
    assert runner_check.main(python) == 1
    file = main / ".git" / "worktrees" / "tree" / "gitdir"
    assert f"REDIRECTED {file}" in capsys.readouterr().out.splitlines()


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize("name", ["commondir", "gitdir"])
def test_a_fifo_in_the_admin_folder_is_reported_not_read(tmp_path: Path, name: str) -> None:
    """A read would block for ever, and output only prints at the end."""
    main, tree = linked_worktree(tmp_path)
    file = main / ".git" / "worktrees" / "tree" / name
    file.unlink()
    os.mkfifo(file)
    assert f"NOT A FILE {file}" in runner_check.admin_problems(main / ".git", [main, tree])


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize("name", ["commondir", "gitdir"])
def test_a_symlink_in_the_admin_folder_is_named(tmp_path: Path, name: str) -> None:
    main, tree = linked_worktree(tmp_path)
    file = main / ".git" / "worktrees" / "tree" / name
    moved = tmp_path / "moved"
    file.rename(moved)
    file.symlink_to(moved)
    assert f"SYMLINK {file}" in runner_check.admin_problems(main / ".git", [main, tree])


@pytest.mark.req("AUTO-2")
def test_a_commondir_that_is_not_text_is_reported_and_keeps_the_folders_after_it(
    tmp_path: Path,
) -> None:
    main, tree = linked_worktree(tmp_path)
    file = main / ".git" / "worktrees" / "tree" / "commondir"
    file.write_bytes(b"\xff\xfe")
    assert f"REDIRECTED {file}" in runner_check.admin_problems(main / ".git", [main, tree])


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize("dangling", [True, False], ids=["dangling", "to-an-empty-folder"])
def test_a_symlinked_worktrees_folder_is_reported(tmp_path: Path, dangling: bool) -> None:
    """Either way it used to read as clean, with nothing read."""
    main, tree = linked_worktree(tmp_path)
    folder = main / ".git" / "worktrees"
    shutil.rmtree(folder)
    target = tmp_path / "elsewhere"
    if not dangling:
        target.mkdir()
    folder.symlink_to(target)
    assert f"SYMLINK {folder}" in runner_check.admin_problems(main / ".git", [main, tree])
