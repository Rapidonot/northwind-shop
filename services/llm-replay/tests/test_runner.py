"""CC-27 F1/F3 (decision 0005): the key-holding runner, against a scripted upstream.

No network and no real key: the upstream is ScriptedLLM and FAKE_KEY stands in for the key.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import socket
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from northwind_common import llm, localenv
from northwind_common.llm import DEFAULT_MAX_OUTPUT_TOKENS, PLACEHOLDER_API_KEY
from northwind_llm_replay import runner
from northwind_llm_replay.ledger import Ledger
from northwind_llm_replay.proxy import RunningProxy
from northwind_llm_replay.runner import (
    ALLOWED_MODELS,
    RunnerError,
    RunnerGate,
    RunnerUnavailableError,
    Window,
    create_runner_app,
    load_runner_key,
    preflight,
    proxy_config,
    refuse_if_port_taken,
)
from northwind_llm_replay.scan import scan_text
from northwind_llm_replay.scripted import ScriptedLLM, ServerThread

pytestmark = pytest.mark.req("CC-27")

FAKE_KEY = "nwfake-" + "runnerkey" * 6  # fake; built at runtime
KEY_ON_DISK = "sk-ant-" + "api03-" + "runnerkey" * 6  # fake; built at runtime
SUITE = "t"
JSON_HEADERS = {"content-type": "application/json"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Rig:
    upstream: ScriptedLLM
    ledger: Ledger
    window: Window
    clock: Clock
    lines: list[str]
    url: str

    def claim(self, suite: str = SUITE) -> str:
        reply = post(f"{self.url}/_runner/claim", {"suite": suite})
        assert reply.status_code == 200, reply.text
        token: str = reply.json()["token"]
        return token

    def send(
        self, token: str, body: dict[str, Any] | None = None, path: str = "/v1/messages"
    ) -> httpx.Response:
        return post(f"{self.url}{path}", body or message(), {"x-api-key": token})


def post(url: str, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.post(url, json=body, headers=headers or {}, timeout=30, trust_env=False)


def message(model: str = "claude-haiku-5-5", max_tokens: int = 64, **extra: Any) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": "hello"}],
        **extra,
    }


def sse_with_deltas(*texts: str) -> str:
    def event(data: dict[str, Any]) -> str:
        return f"event: {data['type']}\ndata: {json.dumps(data)}\n\n"

    start = {"usage": {"input_tokens": 10, "output_tokens": 1}}
    parts = [event({"type": "message_start", "message": start})]
    for text in texts:
        delta = {"type": "text_delta", "text": text}
        parts.append(event({"type": "content_block_delta", "index": 0, "delta": delta}))
    parts.append(event({"type": "message_delta", "delta": {}, "usage": {"output_tokens": 5}}))
    parts.append(event({"type": "message_stop"}))
    return "".join(parts)


@pytest.fixture(autouse=True)
def no_cached_sessions() -> Iterator[None]:
    runner.SESSIONS.clear()
    yield
    runner.SESSIONS.clear()


@contextmanager
def open_rig(tmp_path: Path, key: str) -> Iterator[Rig]:
    clock = Clock()
    lines: list[str] = []
    with ScriptedLLM(require_placeholder=False) as upstream:
        ledger = Ledger(
            tmp_path / "ledger.jsonl",
            budget_usd=1.0,
            run_budget_usd=0.5,
            run_id="window",
            suite=SUITE,
        )
        window = Window(suite=SUITE, minutes=30, idle_minutes=10, clock=clock)
        app, _ = create_runner_app(
            window, key, ledger, tmp_path, upstream_url=upstream.url, out=lines.append
        )
        server = ServerThread(app)
        server.start()
        try:
            yield Rig(upstream, ledger, window, clock, lines, server.url)
        finally:
            server.stop()


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    with open_rig(tmp_path, FAKE_KEY) as opened:
        yield opened


# -- claim and auth (F3) ---------------------------------------------------------------------


def test_the_first_claim_gets_a_token_and_a_second_gets_409(rig: Rig) -> None:
    assert rig.claim()
    second = post(f"{rig.url}/_runner/claim", {"suite": SUITE})
    assert second.status_code == 409
    assert second.json()["isError"] is True
    assert "token" not in second.json()


def test_a_claim_for_another_suite_is_refused_and_leaves_the_window_open(rig: Rig) -> None:
    refused = post(f"{rig.url}/_runner/claim", {"suite": "other"})
    assert refused.status_code == 403
    assert rig.claim()


def test_a_claim_without_json_is_refused(rig: Rig) -> None:
    """A browser page can only send a simple POST, and must not burn the window."""
    refused = httpx.post(
        f"{rig.url}/_runner/claim",
        content=json.dumps({"suite": SUITE}),
        headers={"content-type": "text/plain"},
        trust_env=False,
    )
    assert refused.status_code == 400
    assert rig.claim()


def test_a_foreign_host_header_is_refused(rig: Rig) -> None:
    """DNS rebinding would otherwise make the claim's reply readable by a web page."""
    refused = post(f"{rig.url}/_runner/claim", {"suite": SUITE}, {"host": "evil.example"})
    assert refused.status_code == 403
    assert rig.claim()


def test_the_placeholder_no_longer_opens_the_runner(rig: Rig) -> None:
    rig.claim()
    assert rig.send(PLACEHOLDER_API_KEY).status_code == 401
    assert rig.upstream.requests == []


def test_a_request_before_any_claim_is_refused(rig: Rig) -> None:
    assert rig.send("anything").status_code == 401
    assert rig.upstream.requests == []


def test_a_wrong_token_is_refused(rig: Rig) -> None:
    token = rig.claim()
    assert rig.send(token + "x").status_code == 401
    assert rig.upstream.requests == []


def test_a_non_ascii_credential_is_a_refusal_not_a_crash(rig: Rig) -> None:
    rig.claim()
    reply = httpx.post(
        f"{rig.url}/v1/messages",
        json=message(),
        headers=[(b"x-api-key", b"caf\xe9")],
        trust_env=False,
    )
    assert reply.status_code == 401


def test_the_token_opens_the_runner_and_only_the_key_goes_upstream(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply("hi")
    reply = rig.send(token)
    assert reply.status_code == 200
    assert reply.json()["content"][0]["text"] == "hi"
    sent = rig.upstream.requests[0]["headers"]
    assert sent["x-api-key"] == FAKE_KEY
    assert token not in json.dumps(rig.upstream.requests)
    assert FAKE_KEY not in reply.text


def test_release_needs_the_token_and_closes_the_window(rig: Rig) -> None:
    token = rig.claim()
    refused = post(f"{rig.url}/_runner/release", {}, {"x-api-key": "wrong"})
    assert refused.status_code == 401
    assert not rig.window.closed.is_set()
    assert post(f"{rig.url}/_runner/release", {}, {"x-api-key": token}).status_code == 200
    assert rig.window.closed.is_set()
    assert rig.send(token).status_code == 401


# -- what the runner forwards (item 4) -------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/v1/messages/count_tokens",
        "/v1/messages/batches",
        "/v1/models",
        "/v1/files",
        "/_t/b64.dGVzdA/v1/messages",
        "/v1/messages?beta=false",
        "/v1/messages?beta=true&x=1",
    ],
)
def test_only_post_v1_messages_is_forwarded(rig: Rig, path: str) -> None:
    token = rig.claim()
    reply = rig.send(token, path=path)
    assert reply.status_code == 403
    assert reply.json()["errorCategory"] == "permission"
    assert rig.upstream.requests == []


def test_get_is_refused(rig: Rig) -> None:
    token = rig.claim()
    reply = httpx.get(f"{rig.url}/v1/messages", headers={"x-api-key": token}, trust_env=False)
    assert reply.status_code == 403
    assert rig.upstream.requests == []


def test_the_sdk_beta_query_is_forwarded(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply("hi")
    assert rig.send(token, path="/v1/messages?beta=true").status_code == 200


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5-extra", "", None])
def test_any_other_model_is_refused(rig: Rig, model: Any) -> None:
    token = rig.claim()
    reply = rig.send(token, message(model=model))
    assert reply.status_code == 400
    assert reply.json()["errorCategory"] == "validation"
    assert rig.upstream.requests == []
    assert rig.ledger.totals().spent_usd == 0


def test_the_allowed_models_are_the_factory_defaults() -> None:
    assert frozenset({llm.ASSISTANT_MODEL, llm.ROUTINE_MODEL, llm.JUDGE_MODEL}) == ALLOWED_MODELS
    assert frozenset({"claude-sonnet-5-5", "claude-haiku-5-5"}) == ALLOWED_MODELS


def test_max_tokens_at_the_factory_cap_is_forwarded(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply("hi")
    assert rig.send(token, message(max_tokens=DEFAULT_MAX_OUTPUT_TOKENS)).status_code == 200


@pytest.mark.parametrize("value", [DEFAULT_MAX_OUTPUT_TOKENS + 1, 128000, 0, -1, True, "64", None])
def test_max_tokens_outside_the_cap_is_refused(rig: Rig, value: Any) -> None:
    token = rig.claim()
    reply = rig.send(token, message(max_tokens=value))
    assert reply.status_code == 400
    assert rig.upstream.requests == []


def test_a_body_that_is_not_an_object_is_refused(rig: Rig) -> None:
    token = rig.claim()
    reply = post(f"{rig.url}/v1/messages", ["hello"], {"x-api-key": token})
    assert reply.status_code == 400
    assert rig.upstream.requests == []


def test_the_upstream_cannot_be_set_from_outside() -> None:
    """Only `create_runner_app`, which the tests call directly, takes one."""
    assert set(inspect.signature(runner.serve_window).parameters) == {
        "suite",
        "run_budget",
        "minutes",
        "idle_minutes",
        "total_budget",
    }
    upstream = inspect.signature(runner.create_runner_app).parameters["upstream_url"]
    assert upstream.default == "https://api.anthropic.com"


def test_the_runner_binds_loopback_only(tmp_path: Path) -> None:
    window = Window(suite=SUITE, minutes=1, idle_minutes=1)
    ledger = Ledger(tmp_path / "l", budget_usd=1, run_budget_usd=1, run_id="r", suite=SUITE)
    app, _ = create_runner_app(window, FAKE_KEY, ledger, tmp_path, out=lambda _: None)
    server = ServerThread(app)
    server.start()
    try:
        assert server.url.startswith("http://127.0.0.1:")
    finally:
        server.stop()


@pytest.mark.parametrize("bind_to", ["127.0.0.1", "0.0.0.0"])  # noqa: S104 - the case under test
def test_a_taken_port_refuses_to_start(bind_to: str) -> None:
    with socket.socket() as listener:
        listener.bind((bind_to, 0))
        listener.listen()
        with pytest.raises(RunnerError, match="already in use"):
            refuse_if_port_taken(listener.getsockname()[1])


def test_a_free_port_passes() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    refuse_if_port_taken(port)


# -- budget and charging ---------------------------------------------------------------------


def test_a_budget_refusal_reaches_the_client(rig: Rig) -> None:
    token = rig.claim()
    rig.ledger.run_budget_usd = 0.0
    refused = rig.send(token)
    assert refused.status_code == 402
    assert refused.json()["errorCategory"] == "business"
    assert rig.upstream.requests == []


def test_a_cut_stream_is_charged_at_worst_case(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply_cut_stream()
    rig.send(token, message(stream=True))
    totals = rig.ledger.totals()
    assert totals.settled_calls == 1
    assert totals.spent_usd > 0
    assert totals.outstanding_usd == 0


def test_a_streamed_reply_is_settled_at_actual_usage_and_keeps_its_type(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply("hi")
    reply = rig.send(token, message(stream=True))
    assert reply.headers["content-type"].startswith("text/event-stream")
    assert "message_stop" in reply.text
    assert rig.ledger.totals().settled_calls == 1


# -- key fragments (item 5) ------------------------------------------------------------------


def assert_blocked(reply: httpx.Response) -> None:
    assert reply.status_code == 502
    assert reply.json()["isError"] is True
    assert reply.json()["errorCategory"] == "permission"
    assert reply.json()["isRetryable"] is False
    assert FAKE_KEY[:16] not in reply.text


def test_a_request_holding_a_key_fragment_is_blocked_before_it_is_sent(rig: Rig) -> None:
    token = rig.claim()
    body = message()
    body["messages"][0]["content"] = f"look: {FAKE_KEY[3:25]}"
    assert_blocked(rig.send(token, body))
    assert rig.upstream.requests == []
    assert rig.ledger.totals().spent_usd == 0


def test_an_escaped_key_fragment_in_a_request_is_blocked(rig: Rig) -> None:
    token = rig.claim()
    escaped = "".join(f"\\u{ord(c):04x}" for c in FAKE_KEY[:20])
    raw = json.dumps(message()).replace('"hello"', f'"{escaped}"')
    reply = httpx.post(
        f"{rig.url}/v1/messages",
        content=raw,
        headers={"x-api-key": token, **JSON_HEADERS},
        trust_env=False,
    )
    assert_blocked(reply)
    assert rig.upstream.requests == []


def test_a_forwarded_header_holding_a_key_fragment_is_blocked(rig: Rig) -> None:
    token = rig.claim()
    reply = post(
        f"{rig.url}/v1/messages", message(), {"x-api-key": token, "user-agent": FAKE_KEY[:24]}
    )
    assert_blocked(reply)
    assert rig.upstream.requests == []


def test_a_json_response_holding_a_key_fragment_is_blocked_and_still_charged(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply(f"the key is {FAKE_KEY}")
    assert_blocked(rig.send(token))
    totals = rig.ledger.totals()
    assert totals.settled_calls == 1
    assert totals.outstanding_usd == 0
    assert totals.spent_usd > 0


def test_a_streamed_response_holding_a_key_fragment_is_blocked(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply(f"the key is {FAKE_KEY}")
    assert_blocked(rig.send(token, message(stream=True)))


def test_a_key_split_across_sse_deltas_is_blocked(rig: Rig) -> None:
    token = rig.claim()
    head, tail = FAKE_KEY[:10], FAKE_KEY[10:20]
    rig.upstream.reply_raw(200, sse_with_deltas(head, tail), "text/event-stream")
    assert_blocked(rig.send(token, message(stream=True)))


def test_an_upstream_error_body_holding_a_key_fragment_is_blocked(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply_raw(401, json.dumps({"error": {"message": f"bad key {FAKE_KEY}"}}))
    assert_blocked(rig.send(token))


def test_a_clean_reply_with_an_unrelated_key_shape_passes(rig: Rig) -> None:
    """Only this key's fragments are blocked, not every string that looks like a key."""
    token = rig.claim()
    rig.upstream.reply("a key looks like sk-" + "ant-" + "notourkey0000000")
    assert rig.send(token).status_code == 200


# -- the window (item 8) ---------------------------------------------------------------------


def test_the_time_limit_stops_admission_and_closes_the_window(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply("hi")
    rig.clock.now += 29 * 60
    assert rig.send(token).status_code == 200
    rig.clock.now += 2 * 60
    assert rig.send(token).status_code == 401
    rig.window.tick()
    assert rig.window.closed.is_set()
    assert rig.window.reason == "time limit reached"


def test_the_idle_limit_closes_an_unclaimed_window() -> None:
    clock = Clock()
    window = Window(suite=SUITE, minutes=30, idle_minutes=10, clock=clock)
    clock.now += 9 * 60
    window.tick()
    assert not window.closed.is_set()
    clock.now += 2 * 60
    window.tick()
    assert window.closed.is_set()
    assert window.reason == "idle"


def test_a_request_resets_the_idle_clock_and_in_flight_work_pauses_it() -> None:
    clock = Clock()
    window = Window(suite=SUITE, minutes=30, idle_minutes=10, clock=clock)
    window.begin()
    clock.now += 11 * 60
    window.tick()
    assert not window.closed.is_set()
    window.end()
    clock.now += 9 * 60
    window.tick()
    assert not window.closed.is_set()


def test_a_closed_window_cannot_be_claimed(rig: Rig) -> None:
    rig.window.close("released")
    assert post(f"{rig.url}/_runner/claim", {"suite": SUITE}).status_code == 409


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_the_run_budget_must_be_finite_and_above_zero(value: float) -> None:
    with pytest.raises(RunnerError, match="run budget"):
        runner.checked_run_budget(value)


def test_each_request_prints_one_line_without_content(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply("secret answer text", input_tokens=812, output_tokens=143)
    body = message()
    body["messages"][0]["content"] = "secret question text"
    rig.send(token, body)
    lines = [line for line in rig.lines if "claude-haiku-5-5" in line]
    assert len(lines) == 1
    line = lines[0]
    assert "in=812" in line
    assert "out=143" in line
    assert "$" in line
    assert "run $" in line
    assert "ledger $" in line
    joined = "\n".join(rig.lines)
    assert "secret" not in joined
    assert token not in joined
    assert FAKE_KEY[:16] not in joined


def test_refusals_and_blocks_print_too(rig: Rig) -> None:
    token = rig.claim()
    rig.send(token, message(model="claude-opus-5-5"))
    rig.upstream.reply(f"the key is {FAKE_KEY}")
    rig.send(token)
    joined = "\n".join(rig.lines)
    assert "REFUSED" in joined
    assert "BLOCKED" in joined
    assert FAKE_KEY[:16] not in joined


# -- the key file (item 1) -------------------------------------------------------------------


def key_file(tmp_path: Path, text: str = f"ANTHROPIC_API_KEY={KEY_ON_DISK}\n") -> Path:
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700, exist_ok=True)
    path = secrets / "key"
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_the_key_file_defaults_to_the_private_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_ENV_FILE", raising=False)
    assert localenv.env_file().parent == Path.home() / ".northwind" / "secrets"


def test_the_key_is_loaded_from_a_private_file(tmp_path: Path) -> None:
    assert load_runner_key(key_file(tmp_path)) == KEY_ON_DISK


def test_only_the_key_variable_is_taken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_BUDGET_USD", raising=False)
    path = key_file(tmp_path, f"LLM_BUDGET_USD=1000\nANTHROPIC_API_KEY={KEY_ON_DISK}\n")
    assert load_runner_key(path) == KEY_ON_DISK
    assert "LLM_BUDGET_USD" not in os.environ


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o666])
def test_a_key_file_others_can_read_is_refused(tmp_path: Path, mode: int) -> None:
    path = key_file(tmp_path)
    path.chmod(mode)
    with pytest.raises(RunnerError, match="0600"):
        load_runner_key(path)


def test_a_key_directory_others_can_enter_is_refused(tmp_path: Path) -> None:
    path = key_file(tmp_path)
    path.parent.chmod(0o755)
    with pytest.raises(RunnerError, match="0700"):
        load_runner_key(path)


def test_a_symlinked_key_file_is_refused(tmp_path: Path) -> None:
    real = key_file(tmp_path)
    link = real.parent / "link"
    link.symlink_to(real)
    with pytest.raises(RunnerError, match="symlink"):
        load_runner_key(link)


def test_a_missing_key_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "secrets").mkdir(mode=0o700)
    with pytest.raises(RunnerError, match="missing"):
        load_runner_key(tmp_path / "secrets" / "key")


def test_a_key_file_without_the_key_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RunnerError, match="ANTHROPIC_API_KEY"):
        load_runner_key(key_file(tmp_path, "OTHER=1\n"))


# -- preflight -------------------------------------------------------------------------------


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)  # noqa: S603, S607


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.email", "t@example.com")
    git(origin, "config", "user.name", "t")
    (origin / "README").write_text("x")
    git(origin, "add", "README")
    git(origin, "commit", "-q", "-m", "init")
    target = tmp_path / "runner"
    git(tmp_path, "clone", "-q", str(origin), str(target))
    git(target, "config", "user.email", "t@example.com")
    git(target, "config", "user.name", "t")
    return target.resolve()


def test_preflight_passes_on_a_clean_clone_at_origin_main(clone: Path) -> None:
    preflight(clone, clone)


def test_preflight_refuses_any_other_directory(clone: Path, tmp_path: Path) -> None:
    with pytest.raises(RunnerError, match="run it from"):
        preflight(clone, tmp_path / "elsewhere")


def test_preflight_refuses_a_linked_worktree(clone: Path, tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    git(clone, "worktree", "add", "-q", "--detach", str(linked))
    with pytest.raises(RunnerError, match="linked worktree"):
        preflight(linked.resolve(), linked.resolve())


def test_preflight_refuses_a_modified_file(clone: Path) -> None:
    (clone / "README").write_text("changed")
    with pytest.raises(RunnerError, match="not clean"):
        preflight(clone, clone)


def test_preflight_refuses_an_untracked_file(clone: Path) -> None:
    (clone / "conftest.py").write_text("")
    with pytest.raises(RunnerError, match="not clean"):
        preflight(clone, clone)


def test_preflight_refuses_a_head_that_is_not_origin_main(clone: Path) -> None:
    git(clone, "commit", "-q", "--allow-empty", "-m", "local")
    with pytest.raises(RunnerError, match="origin/main"):
        preflight(clone, clone)


# -- the sandboxed side: record through the runner -------------------------------------------


def config_for(rig: Rig, tmp_path: Path, mode: str = "record") -> Any:
    return proxy_config(
        mode,
        SUITE,
        cassette_root=tmp_path / "cassettes",
        log_path=None,
        url=rig.url,
    )


def in_process_post(proxy: RunningProxy, body: dict[str, Any]) -> httpx.Response:
    return post(f"{proxy.url}/v1/messages", body, {"x-api-key": PLACEHOLDER_API_KEY})


def test_record_writes_the_cassette_here_and_the_ledger_entry_comes_from_the_runner(
    rig: Rig, tmp_path: Path
) -> None:
    rig.upstream.reply("hi", input_tokens=100, output_tokens=50)
    with RunningProxy(config_for(rig, tmp_path)) as proxy:
        reply = in_process_post(proxy, message())
        assert reply.status_code == 200
        assert proxy.proxy.cfg.ledger is None
    cassettes = list((tmp_path / "cassettes" / SUITE).glob("*.json"))
    assert len(cassettes) == 1
    recorded = json.loads(cassettes[0].read_text())
    totals = rig.ledger.totals()
    assert totals.settled_calls == 1
    assert recorded["cost_usd"] == pytest.approx(totals.spent_usd)
    assert recorded["cost_usd"] > 0
    assert rig.upstream.requests[0]["headers"]["x-api-key"] == FAKE_KEY


def test_a_streamed_record_through_the_runner_writes_an_sse_cassette(
    rig: Rig, tmp_path: Path
) -> None:
    rig.upstream.reply("hi")
    with RunningProxy(config_for(rig, tmp_path)) as proxy:
        reply = in_process_post(proxy, message(stream=True))
        assert "message_stop" in reply.text
    (cassette,) = (tmp_path / "cassettes" / SUITE).glob("*.json")
    assert json.loads(cassette.read_text())["kind"] == "sse"


def test_a_second_run_replays_from_the_cassette_without_going_upstream(
    rig: Rig, tmp_path: Path
) -> None:
    rig.upstream.reply("hi")
    with RunningProxy(config_for(rig, tmp_path)) as proxy:
        in_process_post(proxy, message())
        again = in_process_post(proxy, message())
    assert again.status_code == 200
    assert len(rig.upstream.requests) == 1


def test_a_runner_refusal_is_not_recorded(rig: Rig, tmp_path: Path) -> None:
    with RunningProxy(config_for(rig, tmp_path)) as proxy:
        reply = in_process_post(proxy, message(model="claude-opus-5-5"))
    assert reply.status_code == 400
    assert not list(tmp_path.glob("cassettes/**/*.json"))


def test_one_process_claims_once_for_all_its_proxies(rig: Rig, tmp_path: Path) -> None:
    """pytest builds a proxy per test, so the claim is cached per process."""
    rig.upstream.reply("one")
    rig.upstream.reply("two")
    with RunningProxy(config_for(rig, tmp_path)) as first:
        assert in_process_post(first, message(metadata={"user_id": "a"})).status_code == 200
    with RunningProxy(config_for(rig, tmp_path)) as second:
        assert in_process_post(second, message(metadata={"user_id": "b"})).status_code == 200


def test_the_token_stays_out_of_the_environment_and_the_cassette(rig: Rig, tmp_path: Path) -> None:
    rig.upstream.reply("hi")
    config = config_for(rig, tmp_path)
    token = runner.SESSIONS[(rig.url, SUITE)].token
    with RunningProxy(config) as proxy:
        in_process_post(proxy, message())
    assert token not in str(dict(os.environ))
    (cassette,) = (tmp_path / "cassettes" / SUITE).glob("*.json")
    assert token not in cassette.read_text()


def test_a_second_process_gets_the_exact_reason(rig: Rig, tmp_path: Path) -> None:
    rig.claim()
    with pytest.raises(RunnerUnavailableError, match="already claimed"):
        config_for(rig, tmp_path)


def test_replay_needs_no_runner(tmp_path: Path) -> None:
    config = proxy_config(
        "replay", SUITE, cassette_root=tmp_path, log_path=None, url="http://127.0.0.1:1"
    )
    assert config.mode == "replay"
    assert config.ledger is None
    assert not config.via_runner


@pytest.mark.parametrize("mode", ["record", "live"])
def test_no_window_means_no_spend_and_the_key_is_never_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """RAG-12 finding 9: LLM_MODE=record with a readable key file and no runner."""
    monkeypatch.setenv("LLM_ENV_FILE", str(key_file(tmp_path)))

    def no_key_load(*_: object) -> str:
        raise AssertionError("the key was loaded outside the runner")

    monkeypatch.setattr("northwind_llm_replay.proxy.load_api_key", no_key_load)
    monkeypatch.setattr(runner, "load_runner_key", no_key_load)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(RunnerUnavailableError, match="llm-runner SUITE=t RUN_BUDGET="):
        proxy_config(
            mode,
            SUITE,
            cassette_root=tmp_path,
            log_path=None,
            url=f"http://127.0.0.1:{port}",
        )


# -- security review 2026-11-04 --------------------------------------------------------------


def test_a_request_fragment_hit_closes_the_window_and_says_to_rotate(rig: Rig) -> None:
    """One bit per window: a repeatable hit would let guesses recover the key (finding 1)."""
    token = rig.claim()
    body = message()
    body["messages"][0]["content"] = FAKE_KEY[:20]
    assert_blocked(rig.send(token, body))
    assert rig.window.closed.is_set()
    assert "ROTATE THE KEY" in "\n".join(rig.lines)
    assert rig.send(token).status_code == 401


def test_a_response_fragment_hit_closes_the_window(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply(f"the key is {FAKE_KEY}")
    assert_blocked(rig.send(token))
    assert rig.window.closed.is_set()


@pytest.mark.parametrize(
    "free_refusal",
    [{"inference_geo": "x"}, {"model": "claude-opus-5-5"}, {"max_tokens": 999999}],
    ids=["geo", "model", "max-tokens"],
)
def test_a_free_refusal_says_nothing_about_a_guess(rig: Rig, free_refusal: dict[str, Any]) -> None:
    """Hit and miss must look the same whenever the request costs nothing (finding 1)."""
    token = rig.claim()
    hit = message() | free_refusal
    hit["messages"] = [{"role": "user", "content": FAKE_KEY[:20]}]
    miss = message() | free_refusal
    first, second = rig.send(token, hit), rig.send(token, miss)
    assert first.status_code == second.status_code != 502
    assert first.json()["message"] == second.json()["message"]
    assert not rig.window.closed.is_set()


def test_a_budget_refusal_says_nothing_about_a_guess(rig: Rig) -> None:
    token = rig.claim()
    rig.ledger.run_budget_usd = 0.0
    hit = message()
    hit["messages"][0]["content"] = FAKE_KEY[:20]
    assert rig.send(token, hit).status_code == 402
    assert not rig.window.closed.is_set()


def test_repeated_refusals_close_the_window(rig: Rig) -> None:
    token = rig.claim()
    for _ in range(runner.MAX_REFUSALS):
        assert rig.send(token, message(model="claude-opus-5-5")).status_code == 400
    assert rig.window.closed.is_set()
    assert rig.window.reason == f"{runner.MAX_REFUSALS} refusals"


def test_a_call_that_spends_the_run_budget_closes_the_window(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply("hi", input_tokens=2_000_000, output_tokens=0)
    assert rig.send(token, message()).status_code == 200
    assert rig.window.closed.is_set()
    assert rig.window.reason == "the run budget is spent"


@pytest.mark.parametrize(
    "field",
    ["mcp_servers", "container", "service_tier", "speed", "inference_geo_x", "tool_choice"],
)
def test_a_field_outside_the_recorded_ones_is_refused(rig: Rig, field: str) -> None:
    token = rig.claim()
    reply = rig.send(token, message() | {field: "x"})
    assert reply.status_code == 400
    assert rig.upstream.requests == []


def test_every_recorded_request_still_passes_the_gate() -> None:
    """The lists are measured from the cassettes, so a recording must never be refused."""
    gate = runner.RunnerGate(FAKE_KEY, None, print, print)  # type: ignore[arg-type]
    root = Path(__file__).resolve().parents[3] / "cassettes"
    files = sorted(root.glob("**/*.json"))
    assert files
    for path in files:
        match = json.loads(path.read_text())["match"]
        raw = json.dumps(match["body"]).encode()
        name, _, query = match["path"].partition("?")
        headers = {k.lower(): v for k, v in (match.get("headers") or {}).items()}
        refusal = gate.refuse(match["method"], name, query, raw, match["body"], headers)
        assert refusal is None, (path.name, refusal and refusal[0])


@pytest.mark.parametrize(
    "tool",
    [
        {"type": "web_search_20250305", "name": "web_search"},
        {"type": "web_fetch_20250910", "name": "web_fetch"},
        {"type": "code_execution_20250825", "name": "code_execution"},
        {"type": "mcp_toolset", "mcp_server_name": "x"},
        "not an object",
    ],
)
def test_a_server_tool_is_refused(rig: Rig, tool: Any) -> None:
    token = rig.claim()
    assert rig.send(token, message(tools=[tool])).status_code == 400
    assert rig.upstream.requests == []


def test_a_client_tool_is_forwarded(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply("hi")
    tool = {"name": "search", "input_schema": {"type": "object"}}
    assert rig.send(token, message(tools=[tool])).status_code == 200


@pytest.mark.parametrize(
    "block",
    [
        {"type": "document", "source": {"type": "url", "url": "https://example.com/big.pdf"}},
        {"type": "document", "source": {"type": "base64", "data": "JVBERi0="}},
        {"type": "image", "source": {"type": "base64", "data": "iVBORw0K"}},
        {"type": "text", "text": "x", "citations": []},
        {"type": "image", "source": {"type": "url", "url": "https://example.com/big.png"}},
        {"type": "document", "source": {"type": "file", "file_id": "f"}},
        {"type": "server_tool_use", "id": "x", "name": "web_search", "input": {}},
        {
            "type": "tool_result",
            "tool_use_id": "t",
            "content": [{"type": "image", "source": {"type": "url", "url": "https://e.com/x"}}],
        },
    ],
)
def test_content_billed_outside_the_reservation_is_refused(rig: Rig, block: dict[str, Any]) -> None:
    token = rig.claim()
    body = message()
    body["messages"][0]["content"] = [block]
    assert rig.send(token, body).status_code == 400
    assert rig.upstream.requests == []


def test_a_url_source_in_the_system_prompt_is_refused(rig: Rig) -> None:
    token = rig.claim()
    source = {"type": "url", "url": "https://example.com/x"}
    reply = rig.send(token, message(system=[{"type": "document", "source": source}]))
    assert reply.status_code == 400


def test_a_tool_use_input_may_hold_any_shape(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply("hi")
    body = message()
    body["messages"] = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t", "name": "n", "input": {"type": "url", "x": 1}}
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}],
        },
    ]
    assert rig.send(token, body).status_code == 200


def test_an_unknown_beta_is_refused(rig: Rig) -> None:
    token = rig.claim()
    headers = {"x-api-key": token, "anthropic-beta": "mcp-client-2025-04-04"}
    assert post(f"{rig.url}/v1/messages", message(), headers).status_code == 400
    assert rig.upstream.requests == []


def test_the_recorded_betas_are_forwarded(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply("hi")
    betas = ", ".join(sorted(runner.ALLOWED_BETAS))
    headers = {"x-api-key": token, "anthropic-beta": betas}
    assert post(f"{rig.url}/v1/messages", message(), headers).status_code == 200


def test_duplicate_keys_are_refused(rig: Rig) -> None:
    """The gate reads the last, and upstream might read the first."""
    token = rig.claim()
    raw = json.dumps(message()).replace(
        '"model": "claude-haiku-5-5"', '"model": "claude-opus-5-5", "model": "claude-haiku-5-5"'
    )
    reply = httpx.post(
        f"{rig.url}/v1/messages",
        content=raw,
        headers={"x-api-key": token, **JSON_HEADERS},
        trust_env=False,
    )
    assert reply.status_code == 400
    assert rig.upstream.requests == []


def test_a_non_ascii_forwarded_header_is_refused_before_any_reservation(rig: Rig) -> None:
    token = rig.claim()
    reply = httpx.post(
        f"{rig.url}/v1/messages",
        json=message(),
        headers=[(b"x-api-key", token.encode()), (b"user-agent", b"\xff")],
        trust_env=False,
    )
    assert reply.status_code == 400
    assert rig.ledger.totals().outstanding_usd == 0


def test_a_body_over_the_cap_is_refused_unread(rig: Rig) -> None:
    token = rig.claim()
    body = message()
    body["messages"][0]["content"] = "x" * (runner.MAX_BODY_BYTES + 1)
    assert rig.send(token, body).status_code == 413
    assert rig.upstream.requests == []


def test_a_body_with_no_declared_size_is_refused(rig: Rig) -> None:
    token = rig.claim()

    def chunks() -> Iterator[bytes]:
        yield json.dumps(message()).encode()

    reply = httpx.post(
        f"{rig.url}/v1/messages",
        content=chunks(),
        headers={"x-api-key": token, **JSON_HEADERS},
        trust_env=False,
    )
    assert reply.status_code == 413


def test_a_caller_without_the_token_does_not_move_the_idle_clock(rig: Rig) -> None:
    rig.claim()
    rig.clock.now += 9 * 60
    post(f"{rig.url}/v1/messages", message(), {"x-api-key": "wrong"})
    rig.clock.now += 2 * 60
    rig.window.tick()
    assert rig.window.reason == "idle"


def test_too_many_requests_in_flight_are_turned_away(rig: Rig) -> None:
    token = rig.claim()
    for _ in range(runner.MAX_IN_FLIGHT):
        assert rig.window.begin()
    reply = rig.send(token)
    assert reply.status_code == 429
    assert reply.json()["isRetryable"] is True
    assert rig.upstream.requests == []


def test_a_malformed_test_prefix_is_a_structured_error(rig: Rig) -> None:
    token = rig.claim()
    reply = rig.send(token, path="/_t/b64.!")
    assert reply.status_code in (400, 403)
    assert reply.json()["isError"] is True


def test_a_wrong_suite_claim_does_not_say_which_suite_is_open(rig: Rig) -> None:
    refused = post(f"{rig.url}/_runner/claim", {"suite": "other"})
    assert repr(SUITE) not in refused.text


# -- security review 2026-11-04, second pass -------------------------------------------------


def test_a_header_that_breaks_json_parsing_cannot_make_a_miss_free(rig: Rig) -> None:
    """An integer over 4300 digits raises plain ValueError, which used to skip the upstream call."""
    token = rig.claim()
    rig.upstream.reply("hi")
    reply = post(
        f"{rig.url}/v1/messages", message(), {"x-api-key": token, "user-agent": "1" * 5000}
    )
    assert reply.status_code == 200
    assert len(rig.upstream.requests) == 1
    assert rig.ledger.totals().outstanding_usd == 0


def test_a_fragment_beside_unparsable_text_is_still_found(rig: Rig) -> None:
    token = rig.claim()
    reply = post(
        f"{rig.url}/v1/messages",
        message(),
        {"x-api-key": token, "user-agent": "1" * 5000 + FAKE_KEY[:20]},
    )
    assert_blocked(reply)


def test_a_body_with_an_oversized_integer_is_refused_and_counted(rig: Rig) -> None:
    token = rig.claim()
    raw = json.dumps(message()).replace('"hello"', "1" * 5000)
    reply = httpx.post(
        f"{rig.url}/v1/messages",
        content=raw,
        headers={"x-api-key": token, **JSON_HEADERS},
        trust_env=False,
    )
    assert reply.status_code == 400
    assert "REFUSED refused-unreadable" in "\n".join(rig.lines)


def test_repeated_400s_from_upstream_close_the_window(rig: Rig) -> None:
    """A body the gate passes and upstream rejects is a guess that bills nothing real."""
    token = rig.claim()
    for _ in range(runner.MAX_REFUSALS):
        rig.upstream.reply_raw(400, json.dumps({"error": {"message": "bad"}}))
        rig.send(token)
    assert rig.window.closed.is_set()


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_a_body_in_another_encoding_is_refused(rig: Rig, encoding: str) -> None:
    token = rig.claim()
    reply = httpx.post(
        f"{rig.url}/v1/messages",
        content=json.dumps(message()).encode(encoding),
        headers={"x-api-key": token, **JSON_HEADERS},
        trust_env=False,
    )
    assert reply.status_code == 400
    assert rig.upstream.requests == []


@pytest.mark.parametrize(
    "change",
    [
        {"output_config": {"task_budget": 1}},
        {"metadata": {"tier": "x"}},
        {"thinking": {"type": "enabled", "budget_tokens": 100}},
        {"thinking": {"type": "adaptive", "budget_tokens": 100}},
        {"context_management": {"edits": [{"type": "compact_20260112"}]}},
        {"context_management": {"edits": [], "x": 1}},
    ],
)
def test_a_nested_setting_outside_the_recorded_ones_is_refused(
    rig: Rig, change: dict[str, Any]
) -> None:
    token = rig.claim()
    assert rig.send(token, message() | change).status_code == 400
    assert rig.upstream.requests == []


@pytest.mark.parametrize(
    "extra", [{"speed": "fast"}, {"service_tier": "priority"}, {"output_config": {"speed": 1}}]
)
def test_a_message_key_outside_the_recorded_ones_is_refused(
    rig: Rig, extra: dict[str, Any]
) -> None:
    token = rig.claim()
    body = message()
    body["messages"][0] |= extra
    assert rig.send(token, body).status_code == 400
    assert rig.upstream.requests == []


def test_a_large_claim_is_refused_unread(rig: Rig) -> None:
    reply = post(f"{rig.url}/_runner/claim", {"suite": SUITE, "x": "y" * 2000})
    assert reply.status_code == 413
    assert rig.claim()


def test_an_unreadable_claim_is_a_structured_error(rig: Rig) -> None:
    reply = httpx.post(
        f"{rig.url}/_runner/claim",
        content=b"\xff\xfe",
        headers=JSON_HEADERS,
        trust_env=False,
    )
    assert reply.status_code == 400
    assert reply.json()["isError"] is True


def test_callers_without_the_token_cannot_flood_the_terminal(rig: Rig) -> None:
    rig.claim()
    before = len(rig.lines)
    for _ in range(runner.MAX_NOISE_LINES + 5):
        post(f"{rig.url}/v1/messages", message(), {"x-api-key": "wrong"})
    assert len(rig.lines) - before == runner.MAX_NOISE_LINES + 1


@pytest.mark.parametrize(
    "body",
    [
        {**message(), "model": ["claude-haiku-5-5"]},
        message(thinking={"type": ["adaptive"]}),
        {**message(), "messages": [{"role": ["user"], "content": "hi"}]},
        {**message(), "messages": [{"role": "user", "content": [{"type": ["text"]}]}]},
    ],
    ids=["model", "thinking", "role", "block"],
)
def test_a_list_where_a_name_belongs_is_a_structured_refusal(
    rig: Rig, body: dict[str, Any]
) -> None:
    token = rig.claim()
    reply = rig.send(token, body)
    assert reply.status_code == 400
    assert reply.json()["isError"] is True
    assert rig.upstream.requests == []


# -- the key's public prefix -----------------------------------------------------------------

PREFIX = "sk-ant-" + "api03-"
SHAPED_KEY = PREFIX + "Zq7xL9mV2tR8kW4pH1nY6cB3" * 3  # fake; built at runtime
SECRET = SHAPED_KEY[len(PREFIX) :]


@pytest.fixture
def shaped(tmp_path: Path) -> Iterator[Rig]:
    with open_rig(tmp_path, SHAPED_KEY) as opened:
        yield opened


def with_text(text: str) -> dict[str, Any]:
    body = message()
    body["messages"][0]["content"] = text
    return body


@pytest.mark.parametrize("guess", ["Zq7", "aaa"], ids=["right-guess", "wrong-guess"])
def test_the_public_prefix_plus_three_guesses_says_nothing(shaped: Rig, guess: str) -> None:
    """16 characters with 3 unknown could be extended one at a time. A right guess and a wrong one
    must look the same, so neither is blocked and neither closes the window."""
    token = shaped.claim()
    shaped.upstream.reply("hi")
    reply = shaped.send(token, with_text(PREFIX + guess))
    assert reply.status_code == 200
    assert len(shaped.upstream.requests) == 1
    assert not shaped.window.closed.is_set()


@pytest.mark.parametrize("known", range(3, 16))
def test_no_fragment_overlaps_the_prefix(shaped: Rig, known: int) -> None:
    """Every overlap a 16-character window can have with the 13-character prefix."""
    token = shaped.claim()
    shaped.upstream.reply("hi")
    guess = SHAPED_KEY[len(PREFIX) + known - 16 : len(PREFIX) + known]
    assert len(guess) == 16
    assert shaped.send(token, with_text(guess)).status_code == 200
    assert not shaped.window.closed.is_set()


@pytest.mark.parametrize(
    "run",
    [SECRET[:16], SECRET[20:36], SECRET[-16:]],
    ids=["first", "middle", "last"],
)
def test_a_16_character_run_from_the_secret_part_is_blocked(shaped: Rig, run: str) -> None:
    token = shaped.claim()
    reply = shaped.send(token, with_text(f"look: {run}"))
    assert reply.status_code == 502
    assert run not in reply.text
    assert shaped.upstream.requests == []
    assert shaped.window.closed.is_set()
    assert "ROTATE THE KEY" in "\n".join(shaped.lines)


def test_15_characters_of_the_secret_part_pass(shaped: Rig) -> None:
    token = shaped.claim()
    shaped.upstream.reply("hi")
    assert shaped.send(token, with_text(SECRET[:15])).status_code == 200


def test_a_miss_is_still_charged(shaped: Rig) -> None:
    token = shaped.claim()
    shaped.upstream.reply("hi")
    shaped.send(token, with_text(PREFIX + "aaa"))
    assert shaped.ledger.totals().spent_usd > 0


def test_the_whole_key_in_a_response_is_blocked_and_closes_the_window(shaped: Rig) -> None:
    token = shaped.claim()
    shaped.upstream.reply(f"the key is {SHAPED_KEY}")
    assert shaped.send(token).status_code == 502
    assert shaped.window.closed.is_set()


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        ("sk-ant-api03-abc", "abc"),
        ("sk-ant-admin01-abc", "abc"),
        ("sk-ant-oat01-abc", "abc"),
        ("nwfake-abc", "nwfake-abc"),
    ],
)
def test_secret_part_drops_only_a_known_prefix(key: str, secret: str) -> None:
    assert runner.secret_part(key) == secret


def test_a_key_too_short_to_scan_for_is_refused(tmp_path: Path) -> None:
    path = key_file(tmp_path, f"ANTHROPIC_API_KEY={PREFIX}short\n")
    with pytest.raises(RunnerError, match="too short"):
        load_runner_key(path)


def test_the_offline_scan_still_matches_the_prefix(tmp_path: Path) -> None:
    """Only the runner narrows its fragments: a file is not an oracle that answers per request."""
    findings = scan_text(PREFIX + "Zq7", secret=SHAPED_KEY)
    assert "api-key-fragment" in [f.kind for f in findings]


# -- first byte (#17) ------------------------------------------------------------------------


def test_each_call_logs_its_first_upstream_byte(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply("hi")
    rig.send(token)
    (line,) = [line for line in rig.lines if "claude-haiku-5-5" in line]
    assert re.search(r"first_byte=\d+ms", line)
    settled = [
        json.loads(entry)
        for entry in rig.ledger.path.read_text().splitlines()
        if '"settle"' in entry
    ]
    assert isinstance(settled[0]["first_byte_ms"], int)


def test_a_streamed_call_logs_its_first_upstream_byte(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply("hi")
    rig.send(token, message(stream=True))
    assert any(re.search(r"first_byte=\d+ms", line) for line in rig.lines)


def test_a_call_with_no_body_has_no_first_byte(rig: Rig) -> None:
    token = rig.claim()
    rig.upstream.reply_raw(529, "")
    rig.send(token)
    assert any("first_byte=?ms" in line for line in rig.lines)
    assert "first_byte_ms" not in rig.ledger.path.read_text()


# -- security review 2026-11-04, third pass --------------------------------------------------


def test_a_deeply_nested_header_strands_no_reservation(rig: Rig) -> None:
    """Parses and dumps, then the walk for text overflows: it must not skip settlement."""
    token = rig.claim()
    rig.upstream.reply("hi")
    nested = "[" * 2000 + "]" * 2000
    post(f"{rig.url}/v1/messages", message(), {"x-api-key": token, "user-agent": nested})
    assert rig.ledger.totals().outstanding_usd == 0


def test_a_request_scan_that_raises_releases_the_reservation_and_is_counted(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: object) -> bool:
        raise RecursionError

    monkeypatch.setattr(RunnerGate, "request_leaks", boom)
    token = rig.claim()
    reply = rig.send(token)
    assert reply.status_code == 400
    assert rig.upstream.requests == []
    assert rig.ledger.totals().outstanding_usd == 0
    assert "REFUSED refused-unreadable" in "\n".join(rig.lines)


@pytest.mark.parametrize(
    "key",
    [
        "sk-ant-API03-" + "x" * 40,
        "sk-ant-api04_" + "x" * 40,
        "gateway-" + "x" * 40,
    ],
)
def test_a_key_with_no_known_prefix_is_refused(tmp_path: Path, key: str) -> None:
    """Its prefix would stay in the fragments, and the guessing search would return."""
    path = key_file(tmp_path, f"ANTHROPIC_API_KEY={key}\n")
    with pytest.raises(RunnerError, match="known key prefix"):
        load_runner_key(path)
