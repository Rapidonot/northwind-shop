"""HARDEN-1: a classifier refusal ends deterministically, and the proxy log scrubs the CLI's ID.

M1. After a refusal the bundled CLI retries with the `fallback-credit` beta, which the runner
refuses (decision 0005): that used up runner refusals and made replay miss (RAG-11, RT-016). The
CLI env now keeps the beta off (`northwind_common.llm.child_env`), and the sandbox proxy refuses
any fallback beta itself, never forwarding it, and fails the test. No network: ScriptedLLM stands
in for the runner.

M2. `.llm-proxy-log/` holds the request body. Cassettes scrub `metadata.user_id`; the log now
does too, and logs a placeholder for a body it cannot parse.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import httpx2 as httpx
import pytest

from northwind_common.llm import (
    PLACEHOLDER_API_KEY,
    agent_options,
    agent_runtime,
    bundled_cli,
    write_launcher,
)
from northwind_llm_replay import proxy as proxy_module
from northwind_llm_replay import runner
from northwind_llm_replay.ledger import Ledger
from northwind_llm_replay.proxy import ProxyConfig, RunningProxy
from northwind_llm_replay.scan import scan_paths
from northwind_llm_replay.scripted import ScriptedLLM

pytest_plugins = ["pytester"]

FAKE_REAL_KEY = "nwfake-" + "realkey" * 6  # fake; built at runtime
TEST_PAN = "4111" + "1111" * 3  # Luhn-valid sandbox test number, built at runtime
OK_HEADERS = {"x-api-key": PLACEHOLDER_API_KEY, "anthropic-version": "2023-06-01"}
BASE_BETAS = "claude-code-20250219,interleaved-thinking-2025-05-14"
FALLBACK_BETAS = ["fallback-credit-2026-06-01", "server-side-fallback-2026-06-01"]
# The two families the CLI strips and retries; the proxy's own constant must equal this.
PREFIXES = ("fallback-credit-", "server-side-fallback-")
MODEL = "claude-sonnet-5-5"


def body(text: str = "hello", **extra: Any) -> dict[str, Any]:
    return {
        "model": "claude-haiku-5-5",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": text}],
        **extra,
    }


@dataclass
class Rig:
    upstream: ScriptedLLM
    tmp: Path
    ledger: Ledger

    @property
    def log(self) -> Path:
        return self.tmp / "proxy-log" / "t.jsonl"

    def proxy(self, mode: str, root: Path | None = None) -> RunningProxy:
        return RunningProxy(
            ProxyConfig(
                mode=mode,
                suite="t",
                cassette_root=(root or self.tmp) / "cassettes",
                ledger=self.ledger,
                log_path=self.log,
                upstream_url=self.upstream.url,
                key_loader=lambda: FAKE_REAL_KEY,
                secret_for_scan=FAKE_REAL_KEY,
            )
        )


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    with ScriptedLLM(require_placeholder=False) as upstream:
        ledger = Ledger(
            tmp_path / "ledger.jsonl", budget_usd=1.0, run_budget_usd=None, run_id="r", suite="t"
        )
        yield Rig(upstream, tmp_path, ledger)


def log_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# -- M1: the tripwire ---------------------------------------------------------------------------


@pytest.mark.req("HARDEN-1")
@pytest.mark.parametrize("mode", ["record", "replay", "live"])
@pytest.mark.parametrize("beta", FALLBACK_BETAS)
def test_proxy_never_forwards_a_fallback_beta(rig: Rig, mode: str, beta: str) -> None:
    rig.upstream.reply("must not be reached")
    headers = {**OK_HEADERS, "anthropic-beta": f"{BASE_BETAS},{beta}"}
    with rig.proxy(mode) as p:
        r = httpx.post(p.url + "/v1/messages", json=body(), headers=headers, timeout=30)
        state = p.proxy.state
    assert r.status_code == 400
    err = r.json()
    assert (err["isError"], err["errorCategory"], err["isRetryable"]) == (True, "validation", False)
    assert rig.upstream.requests == []  # never forwarded, so it costs no runner refusal
    assert [c["outcome"] for c in state.calls] == ["refused-fallback"]
    assert [a["betas"] for a in state.fallback_attempts] == [[beta]]
    assert state.misses == []  # reported as its own failure, never as a miss to re-record
    assert not list((rig.tmp / "cassettes").rglob("*.json"))
    assert rig.ledger.totals().settled_calls == 0
    if mode != "replay":
        assert [e["outcome"] for e in log_lines(rig.log)] == ["refused-fallback"]


@pytest.mark.req("HARDEN-1")
def test_the_prefixes_are_exactly_the_two_fallback_families() -> None:
    assert getattr(proxy_module, "FALLBACK_BETA_PREFIXES", None) == PREFIXES


@pytest.mark.req("HARDEN-1")
def test_ordinary_betas_still_pass_the_tripwire(rig: Rig) -> None:
    rig.upstream.reply("fine")
    headers = {**OK_HEADERS, "anthropic-beta": BASE_BETAS}
    with rig.proxy("record") as p:
        r = httpx.post(p.url + "/v1/messages", json=body(), headers=headers, timeout=30)
        state = p.proxy.state
    assert r.status_code == 200
    assert state.fallback_attempts == []
    assert len(rig.upstream.requests) == 1


@pytest.mark.req("HARDEN-1")
def test_runner_allow_list_and_refusal_cap_are_unchanged() -> None:
    """The fix never loosens the runner: no fallback beta allowed, the refusal cap still 5."""
    assert not any(b.startswith(PREFIXES) for b in runner.ALLOWED_BETAS)
    assert runner.MAX_REFUSALS == 5


PLUGIN_TEST = """
import pytest
from northwind_common.llm import anthropic_client

@pytest.mark.llm_suite("alpha")
def test_fallback(llm_proxy):
    client = anthropic_client(
        max_retries=0, default_headers={"anthropic-beta": "fallback-credit-2026-06-01"}
    )
    try:
        client.messages.create(model="claude-haiku-5-5", max_tokens=8,
                               messages=[{"role": "user", "content": "x"}])
    except Exception:
        pass
"""


@pytest.mark.req("HARDEN-1")
def test_plugin_fails_a_test_that_sent_a_fallback_beta(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_MODE", raising=False)
    pytester.makepyfile(PLUGIN_TEST)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=1)  # the failure is at teardown
    result.stdout.fnmatch_lines(["*fallback beta*HARDEN-1*do not re-record*"])
    assert "LLM cassette miss" not in result.stdout.str()


# -- M1: the real CLI against a scripted refusal -----------------------------------------------


def refusal_sse(model: str) -> str:
    """The shape of RT-016's recorded refusal (cassette 684f8429…, `d7b9216^`), trimmed."""

    def ev(name: str, data: dict[str, Any]) -> str:
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    start = {
        "type": "message_start",
        "message": {
            "model": model,
            "id": "msg_scripted_refusal",
            "type": "message",
            "role": "assistant",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "stop_details": None,
            "usage": {"input_tokens": 4, "output_tokens": 1},
        },
    }
    delta = {
        "type": "message_delta",
        "delta": {
            "stop_reason": "refusal",
            "stop_sequence": None,
            "stop_details": {
                "type": "refusal",
                "category": "cyber",
                "explanation": "Cyber safeguards flagged this message.",
            },
        },
        "usage": {"input_tokens": 4, "output_tokens": 5},
    }
    return (
        ev("message_start", start)
        + ev("message_delta", delta)
        + ev("message_stop", {"type": "message_stop"})
    )


def script_refusal(upstream: ScriptedLLM) -> None:
    upstream.reply_raw(200, refusal_sse(MODEL), "text/event-stream")
    for _ in range(6):  # more than any retry path needs
        upstream.reply_stream("after the refusal", model=MODEL)


def run_turn(url: str, root: Path, *, flag_dropped: bool = False) -> list[str]:
    rt = agent_runtime(base_url=url, root=root)
    if flag_dropped:  # as if a CLI upgrade ignored it; extra_env refuses this key on purpose
        dropped = {k: v for k, v in rt.env.items() if k != "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"}
        write_launcher(rt.root, dropped, bundled_cli())
    opts = agent_options(
        system_prompt="You are a test.", model=MODEL, runtime=rt, tools=[], max_turns=1
    )

    async def go() -> list[str]:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query

        seen: list[str] = []
        async for msg in query(prompt="hello", options=opts):
            if isinstance(msg, AssistantMessage):
                seen.append(
                    "text:" + "".join(b.text for b in msg.content if isinstance(b, TextBlock))
                )
            elif isinstance(msg, ResultMessage):
                seen.append(f"result:{msg.subtype}")
        return seen

    try:
        return anyio.run(go)
    except Exception as e:  # the CLI's error result raises; its type is the outcome
        return [f"error:{type(e).__name__}"]


def upstream_betas(upstream: ScriptedLLM) -> list[list[str]]:
    return [
        [b.strip() for b in r["headers"].get("anthropic-beta", "").split(",") if b.strip()]
        for r in upstream.requests
    ]


def no_fallback_beta(betas: list[list[str]]) -> bool:
    return not any(b.startswith(PREFIXES) for each in betas for b in each)


def runner_refusals(rig: Rig) -> list[str]:
    """What the runner's own gate (unchanged) would refuse among the requests that reached the
    upstream, the runner's stand-in: each refusal there would count toward MAX_REFUSALS."""
    gate = runner.RunnerGate(FAKE_REAL_KEY + "-x" * 20, rig.ledger, print, print)
    refused = []
    for r in rig.upstream.requests:
        raw = json.dumps(r["body"]).encode()
        verdict = gate.refuse("POST", r["path"], r["query"], raw, r["body"], r["headers"])
        if verdict is not None:
            refused.append(verdict[0])
    return refused


@pytest.mark.req("HARDEN-1")
def test_refusal_ends_as_two_recorded_requests_that_replay_with_no_miss(rig: Rig) -> None:
    script_refusal(rig.upstream)
    with rig.proxy("record") as p:
        recorded = run_turn(p.url, rig.tmp / "rt-record")
        rec_state = p.proxy.state
    assert len(rig.upstream.requests) == 2  # the refusal, then the CLI's plain retry
    assert no_fallback_beta(upstream_betas(rig.upstream))
    assert rec_state.fallback_attempts == []
    assert runner_refusals(rig) == []  # the runner would have used none of its refusals
    assert [c["outcome"] for c in rec_state.calls] == ["recorded", "recorded"]
    assert len(rec_state.refusals) == 1  # the proxy saw the one refusal
    assert len(list((rig.tmp / "cassettes" / "t").glob("*.json"))) == 2
    hashes = [c["hash"] for c in rec_state.calls]
    for n in range(2):  # the same result on every replay
        with rig.proxy("replay") as p:
            replayed = run_turn(p.url, rig.tmp / f"rt-replay-{n}")
            state = p.proxy.state
        assert state.misses == []
        assert state.fallback_attempts == []
        assert [c["outcome"] for c in state.calls] == ["hit", "hit"]
        assert [c["hash"] for c in state.calls] == hashes
        assert len(state.refusals) == 1
        assert replayed == recorded
    assert len(rig.upstream.requests) == 2  # replay never went upstream


DOUBLE_REFUSAL_REQUESTS = 2  # measured on the bundled CLI 0.2.178; a change here is a reviewed one


@pytest.mark.req("HARDEN-1")
def test_a_double_refusal_ends_after_a_pinned_number_of_requests(rig: Rig) -> None:
    """The retry is refused too: the CLI stops with its own error result, no fallback beta, no loop.
    A third refusal is scripted and never asked for."""
    for _ in range(3):
        rig.upstream.reply_raw(200, refusal_sse(MODEL), "text/event-stream")
    rig.upstream.reply_stream("must not be reached", model=MODEL)
    results = []
    for n, mode in enumerate(["record", "replay", "replay"]):
        with rig.proxy(mode) as p:
            results.append(run_turn(p.url, rig.tmp / f"rt-{n}"))
            state = p.proxy.state
        assert len(state.calls) == DOUBLE_REFUSAL_REQUESTS
        assert len(state.refusals) == DOUBLE_REFUSAL_REQUESTS
        assert state.fallback_attempts == []
        assert state.misses == []
    assert len(rig.upstream.requests) == DOUBLE_REFUSAL_REQUESTS
    assert no_fallback_beta(upstream_betas(rig.upstream))
    assert runner_refusals(rig) == []
    assert results[0] == results[1] == results[2]
    assert results[0] and results[0][-1].startswith("error:")  # the CLI's error result, every run


def refusal_json(model: str) -> str:
    return json.dumps(
        {
            "id": "msg_scripted_refusal_json",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": "refusal",
            "stop_sequence": None,
            "usage": {"input_tokens": 4, "output_tokens": 5},
        }
    )


@pytest.mark.req("HARDEN-1")
@pytest.mark.parametrize("stream", [True, False])
def test_the_proxy_counts_refusals_it_sees_and_changes_nothing_else(rig: Rig, stream: bool) -> None:
    if stream:
        rig.upstream.reply_raw(200, refusal_sse(MODEL), "text/event-stream")
    else:
        rig.upstream.reply_raw(200, refusal_json(MODEL))
    rig.upstream.reply("an ordinary answer")
    refused_body = body("refuse me", stream=stream)
    with rig.proxy("record") as p:
        first = httpx.post(
            p.url + "/v1/messages", json=refused_body, headers=OK_HEADERS, timeout=30
        )
        second = httpx.post(
            p.url + "/v1/messages", json=body("fine"), headers=OK_HEADERS, timeout=30
        )
        state = p.proxy.state
    assert first.status_code == 200
    assert first.text == (refusal_sse(MODEL) if stream else refusal_json(MODEL))  # untouched
    assert second.status_code == 200
    assert [c["outcome"] for c in state.calls] == ["recorded", "recorded"]
    assert [r["hash"] for r in state.refusals] == [state.calls[0]["hash"]]
    entries = log_lines(rig.log)
    assert [e.get("refusal") for e in entries] == [True, None]
    assert len(list((rig.tmp / "cassettes" / "t").glob("*.json"))) == 2
    with rig.proxy("replay") as p:
        again = httpx.post(
            p.url + "/v1/messages", json=refused_body, headers=OK_HEADERS, timeout=30
        )
        replay_state = p.proxy.state
    assert again.content == first.content
    assert [c["outcome"] for c in replay_state.calls] == ["hit"]
    assert len(replay_state.refusals) == 1


@pytest.mark.req("HARDEN-1")
def test_with_the_flag_dropped_the_tripwire_fails_closed_the_same_way_every_run(
    rig: Rig,
) -> None:
    """Addition 1: as if a CLI upgrade ignored the flag. The CLI sends the beta, the proxy refuses
    it locally, the CLI strips it and retries. Every run fails on `fallback_attempts`, no fallback
    beta reaches the upstream (the runner's stand-in), and the sequence is identical each time."""
    script_refusal(rig.upstream)
    runs: list[list[tuple[str, bool]]] = []
    for n, mode in enumerate(["record", "record", "replay"]):
        with rig.proxy(mode) as p:
            run_turn(p.url, rig.tmp / f"rt-{n}", flag_dropped=True)
            state = p.proxy.state
        assert state.fallback_attempts, f"run {n} ({mode}) sent no fallback beta"
        assert state.misses == []
        runs.append([(c["hash"], c["outcome"] == "refused-fallback") for c in state.calls])
    assert runs[0] == runs[1] == runs[2]
    assert no_fallback_beta(upstream_betas(rig.upstream))


# -- M2: the proxy log -------------------------------------------------------------------------


def device_user_id() -> str:
    """The CLI's `metadata.user_id`: a JSON string with a hex device ID. This one holds a
    Luhn-valid 16-digit run, as 36 log lines did at #21 (BE-05)."""
    device = "ab" + TEST_PAN + "cdef" * 11 + "ab"
    return json.dumps({"device_id": device, "account_uuid": "", "session_id": "0f0e-synthetic"})


@pytest.mark.req("HARDEN-1")
def test_proxy_log_scrubs_metadata_user_id_and_the_scan_passes(rig: Rig) -> None:
    rig.upstream.reply("ok")
    user_id = device_user_id()
    sent = body(metadata={"user_id": user_id})
    with rig.proxy("record") as p:
        assert (
            httpx.post(
                p.url + "/v1/messages", json=sent, headers=OK_HEADERS, timeout=30
            ).status_code
            == 200
        )
    text = rig.log.read_text(encoding="utf-8")
    assert TEST_PAN not in text
    assert user_id not in text
    [entry] = log_lines(rig.log)
    assert entry["body"]["metadata"]["user_id"] == "<scrubbed:metadata.user_id>"
    assert {k: v for k, v in entry["body"].items() if k != "metadata"} == {
        k: v for k, v in sent.items() if k != "metadata"
    }
    assert scan_paths([rig.log.parent]) == []


@pytest.mark.req("HARDEN-1")
def test_the_scanner_still_fails_the_unscrubbed_line(tmp_path: Path) -> None:
    """The fix is the scrub, not a looser scanner: the raw line still fails."""
    raw = tmp_path / "log" / "t.jsonl"
    raw.parent.mkdir()
    raw.write_text(json.dumps({"body": body(metadata={"user_id": device_user_id()})}) + "\n")
    assert [f.kind for f in scan_paths([raw.parent])] == ["luhn-valid-pan"]


@pytest.mark.req("HARDEN-1")
def test_an_unparsed_body_is_logged_as_a_placeholder_never_raw(rig: Rig) -> None:
    raw = (
        '{"metadata": {"user_id": "' + device_user_id().replace('"', "'") + '"}, broken'
    ).encode()
    with rig.proxy("record") as p:
        httpx.post(
            p.url + "/v1/messages",
            content=raw,
            headers={**OK_HEADERS, "content-type": "application/json"},
            timeout=30,
        )
    text = rig.log.read_text(encoding="utf-8")
    assert TEST_PAN not in text
    assert [e["body"] for e in log_lines(rig.log)] == [
        getattr(proxy_module, "UNPARSED_BODY", "<missing>")
    ]
    assert scan_paths([rig.log.parent]) == []


@pytest.mark.req("HARDEN-1")
def test_a_scrub_that_raises_never_logs_the_raw_body(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Addition 3: fail closed. If scrubbing fails, the raw body is never written."""

    def broken(_body: Any, *_a: Any, **_k: Any) -> Any:
        raise RuntimeError("scrub failed")

    monkeypatch.setattr(proxy_module, "scrub", broken)
    rig.upstream.reply("must not be reached")
    sent = body(metadata={"user_id": device_user_id()})
    with rig.proxy("record") as p:
        r = httpx.post(p.url + "/v1/messages", json=sent, headers=OK_HEADERS, timeout=30)
        state = p.proxy.state
    # Advisor row review 1: the user's addition 3 asks for a placeholder in the log, not silence.
    assert r.status_code == 400
    err = r.json()
    assert (err["isError"], err["errorCategory"], err["isRetryable"]) == (True, "validation", False)
    assert rig.upstream.requests == []
    assert [c["outcome"] for c in state.calls] == ["refused-scrub"]
    [entry] = log_lines(rig.log)
    assert entry["outcome"] == "refused-scrub"
    assert entry["body"] == getattr(proxy_module, "UNSCRUBBED_BODY", "<missing>")
    assert TEST_PAN not in rig.log.read_text(encoding="utf-8")
    assert scan_paths([rig.log.parent]) == []


@pytest.mark.req("HARDEN-1")
@pytest.mark.parametrize("outcome", ["refused-beta", "live"])
def test_the_runner_reports_nothing_from_the_body(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Addition 3: the runner's terminal line is status and numbers only, and it writes no file."""
    printed: list[str] = []
    gate = runner.RunnerGate(FAKE_REAL_KEY + "-x" * 20, rig.ledger, printed.append, printed.append)
    monkeypatch.chdir(rig.tmp)
    before = sorted(rig.tmp.rglob("*"))
    entry = {
        "outcome": outcome,
        "status": 200,
        "model": "claude-haiku-5-5",
        "cost_usd": 0.0,
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "body": body(metadata={"user_id": device_user_id()}),
    }
    gate.report(entry)
    assert printed
    assert not any(TEST_PAN in line or "user_id" in line or "hello" in line for line in printed)
    new = sorted(set(rig.tmp.rglob("*")) - set(before))
    assert [p.name for p in new] in ([], ["ledger.lock"])  # the ledger's own lock, for totals()
    assert not any(TEST_PAN in p.read_text(errors="replace") for p in new if p.is_file())


@pytest.mark.req("HARDEN-1")
@pytest.mark.parametrize("delta", ['"refusal"', '["refusal"]', "7"])
def test_counting_never_raises_on_an_odd_delta_and_the_stream_is_still_recorded(
    rig: Rig, delta: str
) -> None:
    """Security review L1: a `message_delta` whose delta is not an object must not cost the
    recording (counting is observation only)."""
    sse = (
        'event: message_start\ndata: {"type": "message_start", "message": {"model": "m", '
        '"id": "x", "type": "message", "role": "assistant", "content": [], '
        '"usage": {"input_tokens": 1, "output_tokens": 1}}}\n\n'
        f'event: message_delta\ndata: {{"type": "message_delta", "delta": {delta}, '
        '"usage": {"input_tokens": 1, "output_tokens": 2}}\n\n'
        'event: message_stop\ndata: {"type": "message_stop"}\n\n'
    )
    assert proxy_module.stop_reason(sse, True) is None
    rig.upstream.reply_raw(200, sse, "text/event-stream")
    with rig.proxy("record") as p:
        r = httpx.post(
            p.url + "/v1/messages", json=body(stream=True), headers=OK_HEADERS, timeout=30
        )
        state = p.proxy.state
    assert r.status_code == 200
    assert r.text == sse
    assert [c["outcome"] for c in state.calls] == ["recorded"]
    assert state.refusals == []
