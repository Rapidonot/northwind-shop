"""CC-27 F1/F3 (decision 0005): the parts test_runner.py and test_runner_support.py leave open.

`serve_window` and its order of checks, the once-per-process claim and its release at exit, a
spending run with no window (`cli serve`, the eval CLI), and the rule that only the runner side
writes the ledger. No network beyond loopback, no real key, no live call: the key is FAKE_KEY and
the upstream is ScriptedLLM.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import pytest

from northwind_common import localenv
from northwind_common.llm import PLACEHOLDER_API_KEY
from northwind_evals import cli as eval_cli
from northwind_llm_replay import cli, runner
from northwind_llm_replay.ledger import Ledger
from northwind_llm_replay.proxy import RunningProxy
from northwind_llm_replay.runner import (
    RunnerError,
    RunnerUnavailableError,
    Window,
    create_runner_app,
    proxy_config,
)
from northwind_llm_replay.scripted import ScriptedLLM, ServerThread
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("CC-27")

FAKE_KEY = "sk-ant-" + "api03-" + "criteriakey" * 5  # fake; built at runtime
SUITE = "t"
PLACEHOLDER = PLACEHOLDER_API_KEY


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def post(url: str, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.post(url, json=body, headers=headers or {}, timeout=30, trust_env=False)


def message(**extra: Any) -> dict[str, Any]:
    return {
        "model": "claude-haiku-5-5",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hello"}],
        **extra,
    }


def private_key_file(tmp_path: Path) -> Path:
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700, exist_ok=True)
    path = secrets / "key"
    path.write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\n")
    path.chmod(0o600)
    return path


@pytest.fixture(autouse=True)
def no_cached_sessions() -> Iterator[None]:
    runner.SESSIONS.clear()
    yield
    runner.SESSIONS.clear()


@contextmanager
def runner_server(
    tmp_path: Path, suite: str, lines: list[str], upstream_url: str, name: str = "ledger"
) -> Iterator[tuple[str, Window, Ledger]]:
    ledger = Ledger(
        tmp_path / f"{name}.jsonl",
        budget_usd=1.0,
        run_budget_usd=0.5,
        run_id=f"w-{name}",
        suite=suite,
    )
    window = Window(suite=suite, minutes=30, idle_minutes=10)
    app, _ = create_runner_app(
        window, FAKE_KEY, ledger, tmp_path, upstream_url=upstream_url, out=lines.append
    )
    server = ServerThread(app)
    server.start()
    try:
        yield server.url, window, ledger
    finally:
        server.stop()


# -- serve_window: the order of checks (item 1) ----------------------------------------------

STEPS = ["budget", "preflight", "key", "port"]


@pytest.mark.parametrize("fail_at", STEPS)
def test_serve_window_checks_in_order_and_stops_at_the_first_failure(
    monkeypatch: pytest.MonkeyPatch, fail_at: str
) -> None:
    """Run budget, then preflight, then key, then port. Nothing later runs, nothing is bound."""
    calls: list[str] = []
    real_budget = runner.checked_run_budget

    def budget(value: float) -> float:
        calls.append("budget")
        return real_budget(value)

    def step(name: str, result: Any = None) -> Any:
        def fake(*_: object, **__: object) -> Any:
            calls.append(name)
            if name == fail_at:
                raise RunnerError(name)
            return result

        return fake

    def never(what: str) -> Any:
        def boom(*_: object, **__: object) -> None:
            calls.append(what)
            raise AssertionError(f"{what} was reached after a failed check")

        return boom

    monkeypatch.setattr(runner, "checked_run_budget", budget)
    monkeypatch.setattr(runner, "preflight", step("preflight"))
    monkeypatch.setattr(runner, "load_runner_key", step("key", FAKE_KEY))
    monkeypatch.setattr(runner, "refuse_if_port_taken", step("port"))
    monkeypatch.setattr(runner, "Ledger", never("ledger"))
    monkeypatch.setattr(runner, "create_runner_app", never("app"))
    monkeypatch.setattr(runner, "ServerThread", never("bind"))
    run_budget = 0.0 if fail_at == "budget" else 1.0
    with pytest.raises(RunnerError):
        runner.serve_window(SUITE, run_budget, 30, 10, 90)
    assert calls == STEPS[: STEPS.index(fail_at) + 1]


@pytest.mark.parametrize(("minutes", "idle"), [(0, 10), (30, 0), (-1, 10), (30, -1)])
def test_serve_window_refuses_a_zero_time_limit_before_reading_anything(
    monkeypatch: pytest.MonkeyPatch, minutes: float, idle: float
) -> None:
    def touched(*_: object, **__: object) -> None:
        raise AssertionError("a check ran for a window that cannot open")

    monkeypatch.setattr(runner, "preflight", touched)
    monkeypatch.setattr(runner, "load_runner_key", touched)
    monkeypatch.setattr(runner, "refuse_if_port_taken", touched)
    with pytest.raises(RunnerError, match="above 0"):
        runner.serve_window(SUITE, 1.0, minutes, idle, 90)


def test_serve_window_never_reads_the_key_file_when_preflight_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real preflight and real loader: this directory is not the runner clone."""
    key = private_key_file(tmp_path)
    monkeypatch.setenv("LLM_ENV_FILE", str(key))
    monkeypatch.setattr(localenv, "runner_checkout", lambda: tmp_path / "elsewhere")
    opened: list[object] = []
    real_open = os.open

    def spy_open(path: Any, *args: Any, **kwargs: Any) -> int:
        opened.append(path)
        fd: int = real_open(path, *args, **kwargs)
        return fd

    monkeypatch.setattr(os, "open", spy_open)
    with pytest.raises(RunnerError, match="run it from"):
        runner.serve_window(SUITE, 1.0, 30, 10, 90)
    assert key not in [Path(str(p)) for p in opened]


# -- serve_window: a window that opens and closes (item 1) ------------------------------------


@pytest.fixture
def window_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[Ledger]:
    """A real serve_window on a free loopback port, with a fake key and a throwaway ledger. Only
    preflight is stubbed: the sandbox has no runner clone. Returns the ledgers it built."""
    monkeypatch.setenv("LLM_RUNNER_PORT", str(free_port()))
    monkeypatch.setenv("LLM_ENV_FILE", str(private_key_file(tmp_path)))
    monkeypatch.setenv("LLM_LEDGER_PATH", str(tmp_path / "runner-ledger.jsonl"))
    monkeypatch.delenv("LLM_BUDGET_USD", raising=False)
    monkeypatch.setattr(runner, "preflight", lambda root, expected: None)
    ledgers: list[Ledger] = []
    real_app = runner.create_runner_app

    def spy(window: Window, key: str, ledger: Ledger, root: Path, **kwargs: Any) -> Any:
        assert "upstream_url" not in kwargs, "serve_window must use the constant upstream"
        assert key == FAKE_KEY
        ledgers.append(ledger)
        return real_app(window, key, ledger, root, **kwargs)

    monkeypatch.setattr(runner, "create_runner_app", spy)
    return ledgers


def open_window(target: Any, *args: Any) -> tuple[threading.Thread, dict[str, Any]]:
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["code"] = target(*args)
        except BaseException as exc:  # reported to the test thread
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def wait_until_listening(port: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise AssertionError("the window never opened")


def claim_and_release(port: int, suite: str = SUITE) -> None:
    url = f"http://127.0.0.1:{port}"
    claimed = post(f"{url}/_runner/claim", {"suite": suite})
    assert claimed.status_code == 200, claimed.text
    released = post(f"{url}/_runner/release", {}, {"x-api-key": claimed.json()["token"]})
    assert released.status_code == 200


def test_a_window_closed_by_release_ends_serve_window_with_exit_0(
    window_env: list[Ledger], capsys: pytest.CaptureFixture[str]
) -> None:
    port = localenv.runner_port()
    thread, result = open_window(runner.serve_window, SUITE, 0.75, 30, 10, 7.0)
    wait_until_listening(port)
    claim_and_release(port)
    thread.join(20)
    assert not thread.is_alive()
    assert result == {"code": 0}
    out = capsys.readouterr().out
    assert "window open: suite t" in out
    assert "window closed: released" in out
    with socket.socket() as probe:  # the port is free again: one window is one run
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_the_ledger_uses_the_run_budget_and_the_total_budget_it_was_given(
    window_env: list[Ledger],
) -> None:
    port = localenv.runner_port()
    thread, result = open_window(runner.serve_window, SUITE, 0.75, 30, 10, 7.0)
    wait_until_listening(port)
    claim_and_release(port)
    thread.join(20)
    assert result == {"code": 0}
    (ledger,) = window_env
    assert ledger.run_budget_usd == 0.75
    assert ledger.budget_usd == 7.0
    assert ledger.suite == SUITE
    assert ledger.path == localenv.ledger_path()


def test_every_window_gets_a_fresh_run_id(window_env: list[Ledger]) -> None:
    for _ in range(2):
        port = localenv.runner_port()
        thread, result = open_window(runner.serve_window, SUITE, 1.0, 30, 10, 90.0)
        wait_until_listening(port)
        claim_and_release(port)
        thread.join(20)
        assert result == {"code": 0}
    first, second = window_env
    assert first.run_id != second.run_id
    assert first.run_id.startswith("runner-")
    assert second.run_id.startswith("runner-")


def test_a_window_for_one_suite_refuses_a_claim_for_another(window_env: list[Ledger]) -> None:
    port = localenv.runner_port()
    thread, result = open_window(runner.serve_window, SUITE, 1.0, 30, 10, 90.0)
    wait_until_listening(port)
    other = post(f"http://127.0.0.1:{port}/_runner/claim", {"suite": "other"})
    assert other.status_code == 403
    claim_and_release(port)
    thread.join(20)
    assert result == {"code": 0}


@pytest.mark.parametrize(
    ("minutes", "idle", "reason"), [(0.01, 10, "time limit"), (30, 0.01, "idle")]
)
def test_a_window_that_runs_out_of_time_or_sits_idle_ends_with_exit_0(
    window_env: list[Ledger],
    capsys: pytest.CaptureFixture[str],
    minutes: float,
    idle: float,
    reason: str,
) -> None:
    thread, result = open_window(runner.serve_window, SUITE, 1.0, minutes, idle, 90.0)
    thread.join(30)
    assert not thread.is_alive()
    assert result == {"code": 0}
    assert f"window closed: {reason}" in capsys.readouterr().out


def test_the_runner_subcommand_passes_the_total_budget_from_the_environment(
    window_env: list[Ledger], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_BUDGET_USD", "7")
    port = localenv.runner_port()
    argv = ["runner", "--suite", SUITE, "--run-budget", "0.75"]
    thread, result = open_window(cli.main, argv)
    wait_until_listening(port)
    claim_and_release(port)
    thread.join(20)
    assert result == {"code": 0}
    (ledger,) = window_env
    assert (ledger.run_budget_usd, ledger.budget_usd) == (0.75, 7.0)


# -- cli runner refuses without a traceback (item 1) ------------------------------------------


def test_cli_runner_outside_the_clone_exits_2_with_a_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(localenv, "runner_checkout", lambda: tmp_path / "elsewhere")

    def no_key(*_: object) -> str:
        raise AssertionError("the key was loaded by a refused runner")

    monkeypatch.setattr(runner, "load_runner_key", no_key)
    assert cli.main(["runner", "--suite", SUITE, "--run-budget", "1"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert "run it from" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("budget", ["0", "-1", "nan", "inf"])
def test_cli_runner_refuses_a_bad_run_budget_with_exit_2(
    capsys: pytest.CaptureFixture[str], budget: str
) -> None:
    assert cli.main(["runner", "--suite", SUITE, f"--run-budget={budget}"]) == 2
    err = capsys.readouterr().err
    assert "run budget" in err
    assert "Traceback" not in err


def test_cli_runner_has_no_default_run_budget() -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.main(["runner", "--suite", SUITE])
    assert stopped.value.code == 2


def test_cli_runner_takes_no_upstream_or_key_option() -> None:
    for option in ("--upstream", "--key", "--upstream-url", "--env-file"):
        with pytest.raises(SystemExit) as stopped:
            cli.main(["runner", "--suite", SUITE, "--run-budget", "1", option, "x"])
        assert stopped.value.code == 2


# -- claims: once per process and suite, released at exit (item 2) ---------------------------


def test_a_second_claim_in_one_process_reuses_the_first_and_does_not_reach_the_runner(
    tmp_path: Path,
) -> None:
    lines: list[str] = []
    with ScriptedLLM(require_placeholder=False) as upstream:
        with runner_server(tmp_path, SUITE, lines, upstream.url) as (url, _, _):
            first = runner.claim(SUITE, url)
            again = runner.claim(SUITE, url)
            assert again is first
            assert [line for line in lines if line.startswith("claimed")] == [
                "claimed: the run has started"
            ]
            assert not [line for line in lines if "REFUSED" in line]


def test_a_second_suite_in_one_process_is_a_separate_claim(tmp_path: Path) -> None:
    lines: list[str] = []
    with ScriptedLLM(require_placeholder=False) as upstream:
        with (
            runner_server(tmp_path, "a", lines, upstream.url, "a") as (url_a, window_a, _),
            runner_server(tmp_path, "b", lines, upstream.url, "b") as (url_b, window_b, _),
        ):
            first = runner.claim("a", url_a)
            second = runner.claim("b", url_b)
            assert first is not second
            assert first.token != second.token
            assert set(runner.SESSIONS) == {(url_a, "a"), (url_b, "b")}
            assert runner.claim("a", url_a) is first
            assert runner.claim("b", url_b) is second
            assert window_a.credential() == first.token
            assert window_b.credential() == second.token


def test_a_suite_the_window_was_not_opened_for_is_not_claimed(tmp_path: Path) -> None:
    lines: list[str] = []
    with ScriptedLLM(require_placeholder=False) as upstream:
        with runner_server(tmp_path, SUITE, lines, upstream.url) as (url, window, _):
            with pytest.raises(RunnerUnavailableError, match="another suite"):
                runner.claim("other", url)
            assert ("other", url) not in runner.SESSIONS
            assert window.credential() is None  # still unclaimed: the real run can still start
            assert runner.claim(SUITE, url).token


def test_every_claim_registers_exactly_one_release_at_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registered: list[tuple[Any, tuple[Any, ...]]] = []

    def register(func: Any, *args: Any) -> Any:
        registered.append((func, args))
        return func

    monkeypatch.setattr(runner, "atexit", SimpleNamespace(register=register))
    lines: list[str] = []
    with ScriptedLLM(require_placeholder=False) as upstream:
        with runner_server(tmp_path, SUITE, lines, upstream.url) as (url, window, _):
            session = runner.claim(SUITE, url)
            runner.claim(SUITE, url)
            assert registered == [(runner._release, (session,))]
            assert not window.closed.is_set()  # nothing is released before the process exits
            func, args = registered[0]
            func(*args)  # what atexit does
            assert window.closed.is_set()
            assert window.reason == "released"


def test_a_process_that_claims_and_exits_releases_the_window(tmp_path: Path) -> None:
    """The real atexit hook, in a real second process."""
    lines: list[str] = []
    with ScriptedLLM(require_placeholder=False) as upstream:
        with runner_server(tmp_path, SUITE, lines, upstream.url) as (url, window, _):
            code = (
                "import sys\n"
                "from northwind_llm_replay import runner\n"
                f"runner.claim({SUITE!r}, {url!r})\n"
            )
            done = subprocess.run(  # noqa: S603 - this interpreter, fixed code
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            assert done.returncode == 0, done.stderr
            assert window.closed.is_set()
            assert window.reason == "released"


def test_a_release_with_no_runner_left_is_silent(tmp_path: Path) -> None:
    session = runner.RunnerSession(url=f"http://127.0.0.1:{free_port()}", suite=SUITE, token="x")  # noqa: S106 - not a secret
    runner._release(session)  # the idle limit closes the window instead; nothing is raised


def test_a_closed_window_cannot_be_claimed_again_by_a_later_process(tmp_path: Path) -> None:
    lines: list[str] = []
    with ScriptedLLM(require_placeholder=False) as upstream:
        with runner_server(tmp_path, SUITE, lines, upstream.url) as (url, _, _):
            session = runner.claim(SUITE, url)
            runner._release(session)
            runner.SESSIONS.clear()  # a new process has an empty cache
            with pytest.raises(RunnerUnavailableError, match="open a new one|already claimed"):
                runner.claim(SUITE, url)


# -- a spending run with no window (item 3) --------------------------------------------------


@pytest.fixture
def no_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Record mode, a port nothing listens on, and a throwaway ledger path that must stay absent."""
    monkeypatch.setenv("LLM_MODE", "record")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("LLM_RUNNER_PORT", str(free_port()))
    monkeypatch.setenv("LLM_ENV_FILE", str(private_key_file(tmp_path)))
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("LLM_LEDGER_PATH", str(ledger))
    monkeypatch.chdir(tmp_path)

    def no_key(*_: object) -> str:
        raise AssertionError("the key was loaded outside the runner")

    monkeypatch.setattr("northwind_llm_replay.proxy.load_api_key", no_key)
    monkeypatch.setattr(runner, "load_runner_key", no_key)
    return ledger


def test_serve_in_record_mode_with_no_window_exits_2_with_the_command(
    no_window: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What `make llm-proxy` runs, with LLM_MODE=record."""

    def no_proxy(*_: object, **__: object) -> None:
        raise AssertionError("a proxy was started with no window")

    monkeypatch.setattr("northwind_llm_replay.proxy.RunningProxy", no_proxy)
    assert cli.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "make -C ~/.northwind/runner llm-runner SUITE=dev RUN_BUDGET=" in err
    assert "Traceback" not in err
    assert not no_window.exists()
    assert not (tmp_path / ".llm-proxy-log").exists()
    assert not (tmp_path / "cassettes").exists()


def test_serve_names_its_own_suite_in_the_command(
    no_window: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["serve", "--suite", "rag-enrich"]) == 2
    assert "llm-runner SUITE=rag-enrich RUN_BUDGET=" in capsys.readouterr().err


def test_serve_in_live_mode_with_no_window_exits_2_too(
    no_window: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_MODE", "live")
    assert cli.main(["serve"]) == 2
    assert "llm-runner SUITE=dev" in capsys.readouterr().err
    assert not no_window.exists()


def test_make_llm_proxy_runs_cli_serve_and_nothing_else() -> None:
    """Read the target, never run it: it would start a proxy."""
    makefile = (Path(__file__).resolve().parents[3] / "Makefile").read_text()
    recipe = makefile.split("\nllm-proxy:", 1)[1].split("\n\n", 1)[0]
    commands = [line.strip() for line in recipe.splitlines()[1:] if line.startswith("\t")]
    assert commands == ["uv run python -m northwind_llm_replay.cli serve"]


@pytest.fixture
def eval_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """The eval CLI with retrieval and the saved contexts faked, so only the spending client
    decides the outcome."""
    paths = {
        "candidate": tmp_path / "candidate.json",
        "last": tmp_path / "last.json",
        "baseline": tmp_path / "baseline.json",
    }
    monkeypatch.setattr(eval_cli, "CANDIDATE", paths["candidate"])
    monkeypatch.setattr(eval_cli, "LAST_RUN", paths["last"])
    monkeypatch.setattr(eval_cli, "BASELINE", paths["baseline"])
    monkeypatch.setattr(eval_cli, "load_items", lambda path: [])
    monkeypatch.setattr(eval_cli, "load_contexts", lambda path: {})
    monkeypatch.setattr(eval_cli, "_context_leaks", lambda contexts, items: [])
    monkeypatch.setattr(eval_cli, "_stale_ids", lambda *args: [])
    monkeypatch.setattr(eval_cli, "_connect", lambda: FakeConnection())
    monkeypatch.setattr(eval_cli, "FastEmbedEmbedder", lambda: FakeEmbedder("x"))
    monkeypatch.setattr(eval_cli, "_index_version", lambda conn: "idx")
    monkeypatch.setattr(eval_cli, "_live", lambda conn, embedder, items: {})
    return paths


class FakeConnection:
    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_eval_run_with_no_window_returns_2_spends_nothing_and_writes_no_candidate(
    no_window: Path,
    eval_rig: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(eval_cli, "_connect", lambda: FakeConnection())

    def no_ledger(self: object, *args: object, **kwargs: object) -> None:
        raise AssertionError("a ledger was built on the client side")

    monkeypatch.setattr("northwind_llm_replay.ledger.Ledger.__init__", no_ledger)

    def no_upstream(*args: object, **kwargs: object) -> None:
        raise AssertionError("a request left the process")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_upstream)
    assert eval_cli.main(["run", "--slice", "5"]) == 2
    captured = capsys.readouterr()
    assert "make -C ~/.northwind/runner llm-runner SUITE=eval-answers RUN_BUDGET=" in captured.err
    assert "Traceback" not in captured.err
    assert not eval_rig["candidate"].exists()
    assert not eval_rig["last"].exists()
    assert not eval_rig["baseline"].exists()
    assert not no_window.exists()


def test_eval_run_gives_the_same_command_as_the_proxy(
    no_window: Path,
    eval_rig: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(eval_cli, "_connect", lambda: FakeConnection())
    with pytest.raises(RunnerUnavailableError) as raised:
        proxy_config("record", eval_cli.SUITE, cassette_root=Path("cassettes"), log_path=None)
    assert eval_cli.main(["run", "--slice", "all"]) == 2
    assert str(raised.value) in capsys.readouterr().err


# -- the ledger is written only by the runner side (item 4) ----------------------------------


def test_a_record_run_works_with_an_unwritable_ledger_path_on_the_client_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    client_ledger = blocker / "ledger.jsonl"  # cannot be created, whatever the user id
    monkeypatch.setenv("LLM_LEDGER_PATH", str(client_ledger))
    lines: list[str] = []
    with ScriptedLLM(require_placeholder=False) as upstream:
        upstream.reply("hi", input_tokens=100, output_tokens=50)
        with runner_server(tmp_path, SUITE, lines, upstream.url) as (url, _, ledger):
            real_init = Ledger.__init__
            built: list[Path] = []

            def watch(self: Ledger, path: Path, *args: Any, **kwargs: Any) -> None:
                built.append(path)
                real_init(self, path, *args, **kwargs)

            monkeypatch.setattr(Ledger, "__init__", watch)
            config = proxy_config(
                "record", SUITE, cassette_root=tmp_path / "cassettes", log_path=None, url=url
            )
            assert config.ledger is None
            with RunningProxy(config) as proxy:
                reply = post(f"{proxy.url}/v1/messages", message(), {"x-api-key": PLACEHOLDER})
                assert reply.status_code == 200, reply.text
                assert proxy.proxy.cfg.ledger is None
            assert built == []
            assert not client_ledger.exists()
            totals = ledger.totals()
            assert totals.settled_calls == 1
            assert totals.spent_usd > 0
    assert len(list((tmp_path / "cassettes" / SUITE).glob("*.json"))) == 1


def test_the_client_side_never_reads_the_ledger_or_budget_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM_BUDGET_USD and LLM_RUN_BUDGET_USD on a sandboxed command change nothing: the caps come
    from the runner's terminal."""
    monkeypatch.setenv("LLM_BUDGET_USD", "1000")
    monkeypatch.setenv("LLM_RUN_BUDGET_USD", "1000")
    monkeypatch.setenv("LLM_LEDGER_PATH", str(tmp_path / "blocker" / "ledger.jsonl"))
    lines: list[str] = []
    with ScriptedLLM(require_placeholder=False) as upstream:
        with runner_server(tmp_path, SUITE, lines, upstream.url) as (url, _, ledger):
            ledger.run_budget_usd = 0.0
            config = proxy_config(
                "record", SUITE, cassette_root=tmp_path / "cassettes", log_path=None, url=url
            )
            with RunningProxy(config) as proxy:
                reply = post(f"{proxy.url}/v1/messages", message(), {"x-api-key": PLACEHOLDER})
            assert reply.status_code == 402
            assert upstream.requests == []
            assert ledger.run_budget_usd == 0.0
