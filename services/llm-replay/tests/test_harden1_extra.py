"""HARDEN-1 gap tests: beta header variants, stop_reason parsing, live-mode refusals, log shapes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from northwind_common.llm import PLACEHOLDER_API_KEY
from northwind_llm_replay.ledger import Ledger
from northwind_llm_replay.proxy import (
    UNPARSED_BODY,
    ProxyConfig,
    RunningProxy,
    fallback_betas,
    stop_reason,
)
from northwind_llm_replay.scripted import ScriptedLLM

FAKE_REAL_KEY = "nwfake-" + "realkey" * 6  # fake; built at runtime
OK_HEADERS = {"x-api-key": PLACEHOLDER_API_KEY, "anthropic-version": "2023-06-01"}
MODEL = "claude-haiku-5-5"


def body(text: str = "hello", **extra: Any) -> dict[str, Any]:
    return {
        "model": MODEL,
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

    def proxy(self, mode: str) -> RunningProxy:
        return RunningProxy(
            ProxyConfig(
                mode=mode,
                suite="t",
                cassette_root=self.tmp / "cassettes",
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
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def sse(*events: tuple[str, dict[str, Any]]) -> str:
    return "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events)


def delta(reason: str | None) -> tuple[str, dict[str, Any]]:
    return ("message_delta", {"type": "message_delta", "delta": {"stop_reason": reason}})


STOP = ("message_stop", {"type": "message_stop"})
REFUSAL_JSON = json.dumps(
    {
        "id": "msg_x",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [],
        "stop_reason": "refusal",
        "stop_sequence": None,
        "usage": {"input_tokens": 4, "output_tokens": 5},
    }
)


# -- fallback_betas: header variants ------------------------------------------------------------


@pytest.mark.req("HARDEN-1")
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (" Fallback-Credit-2026-06-01 ", ["Fallback-Credit-2026-06-01"]),
        (
            "claude-code-20250219, SERVER-SIDE-FALLBACK-2026-06-01 ,x",
            ["SERVER-SIDE-FALLBACK-2026-06-01"],
        ),
        (
            "a,fallback-credit-1,b,server-side-fallback-2",
            ["fallback-credit-1", "server-side-fallback-2"],
        ),
        ("claude-code-20250219,interleaved-thinking-2025-05-14", []),
        ("", []),
        ("not-a-fallback-credit-1", []),  # a prefix, not a substring
    ],
)
def test_fallback_betas_ignores_case_and_whitespace(header: str, expected: list[str]) -> None:
    assert fallback_betas({"anthropic-beta": header}) == expected


@pytest.mark.req("HARDEN-1")
def test_fallback_betas_without_the_header_is_empty() -> None:
    assert fallback_betas({}) == []


@pytest.mark.req("HARDEN-1")
@pytest.mark.parametrize("mode", ["record", "replay", "live"])
def test_odd_case_and_spacing_fallback_beta_is_refused_locally(rig: Rig, mode: str) -> None:
    rig.upstream.reply("must not be reached")
    headers = {
        **OK_HEADERS,
        "anthropic-beta": "claude-code-20250219, Fallback-Credit-2026-06-01 ,z",
    }
    with rig.proxy(mode) as p:
        r = httpx.post(p.url + "/v1/messages", json=body(), headers=headers, timeout=30)
        state = p.proxy.state
    assert r.status_code == 400
    assert rig.upstream.requests == []
    assert [c["outcome"] for c in state.calls] == ["refused-fallback"]
    assert [a["betas"] for a in state.fallback_attempts] == [["Fallback-Credit-2026-06-01"]]


@pytest.mark.req("HARDEN-1")
@pytest.mark.parametrize("mode", ["record", "replay", "live"])
def test_a_fallback_beta_on_count_tokens_is_refused_locally(rig: Rig, mode: str) -> None:
    rig.upstream.reply("must not be reached")
    headers = {**OK_HEADERS, "anthropic-beta": "server-side-fallback-2026-06-01"}
    with rig.proxy(mode) as p:
        r = httpx.post(
            p.url + "/v1/messages/count_tokens",
            json={"model": MODEL, "messages": body()["messages"]},
            headers=headers,
            timeout=30,
        )
        state = p.proxy.state
    assert r.status_code == 400
    assert r.json()["errorCategory"] == "validation"
    assert rig.upstream.requests == []
    assert [c["outcome"] for c in state.calls] == ["refused-fallback"]
    assert state.misses == []


@pytest.mark.req("HARDEN-1")
def test_a_refused_fallback_reserves_nothing_in_the_ledger(rig: Rig) -> None:
    rig.upstream.reply("must not be reached")
    headers = {**OK_HEADERS, "anthropic-beta": "fallback-credit-2026-06-01"}
    with rig.proxy("live") as p:
        httpx.post(p.url + "/v1/messages", json=body(), headers=headers, timeout=30)
    path = rig.tmp / "ledger.jsonl"
    assert not path.exists() or path.read_text(encoding="utf-8").strip() == ""
    totals = rig.ledger.totals()
    assert totals.settled_calls == 0


# -- stop_reason --------------------------------------------------------------------------------


@pytest.mark.req("HARDEN-1")
def test_stop_reason_sse_refusal_followed_by_other_events() -> None:
    text = sse(
        ("message_start", {"type": "message_start", "message": {}}),
        delta("refusal"),
        STOP,
        ("ping", {"type": "ping"}),
    )
    assert stop_reason(text, True) == "refusal"


@pytest.mark.req("HARDEN-1")
@pytest.mark.parametrize("reason", ["end_turn", "max_tokens", "tool_use", "stop_sequence"])
def test_stop_reason_sse_non_refusal_stops_are_not_refusals(reason: str) -> None:
    assert stop_reason(sse(delta(reason), STOP), True) == reason


@pytest.mark.req("HARDEN-1")
def test_stop_reason_sse_ignores_malformed_data_lines() -> None:
    text = (
        "event: x\ndata: {not json\n\n"
        + sse(delta("refusal"))
        + "data: [DONE]\n\ndata: \n\ndata: [1, 2]\n\n"
    )
    assert stop_reason(text, True) == "refusal"


@pytest.mark.req("HARDEN-1")
def test_stop_reason_sse_without_a_delta_or_with_null_reason_is_none() -> None:
    assert stop_reason("", True) is None
    assert stop_reason(sse(STOP), True) is None
    assert stop_reason(sse(delta(None)), True) is None
    assert stop_reason("event: x\ndata: {broken\n\n", True) is None


@pytest.mark.req("HARDEN-1")
def test_stop_reason_json_shapes() -> None:
    assert stop_reason(REFUSAL_JSON, False) == "refusal"
    assert stop_reason(json.dumps({"stop_reason": "end_turn"}), False) == "end_turn"
    assert stop_reason(json.dumps({"stop_reason": None}), False) is None
    assert stop_reason(json.dumps({"stop_reason": 5}), False) is None
    assert stop_reason(json.dumps([{"stop_reason": "refusal"}]), False) is None
    assert stop_reason("not json", False) is None
    assert stop_reason("", False) is None


# -- refusal counting in live mode and state.calls ----------------------------------------------


@pytest.mark.req("HARDEN-1")
@pytest.mark.parametrize("stream", [True, False])
def test_refusals_are_counted_in_live_mode_too(rig: Rig, stream: bool) -> None:
    if stream:
        text = sse(
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "model": MODEL,
                        "id": "m",
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "usage": {"input_tokens": 4, "output_tokens": 1},
                    },
                },
            ),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "refusal"},
                    "usage": {"input_tokens": 4, "output_tokens": 5},
                },
            ),
            STOP,
        )
        rig.upstream.reply_raw(200, text, "text/event-stream")
    else:
        text = REFUSAL_JSON
        rig.upstream.reply_raw(200, text)
    rig.upstream.reply("fine")
    with rig.proxy("live") as p:
        first = httpx.post(
            p.url + "/v1/messages", json=body("a", stream=stream), headers=OK_HEADERS, timeout=30
        )
        httpx.post(p.url + "/v1/messages", json=body("b"), headers=OK_HEADERS, timeout=30)
        state = p.proxy.state
    assert first.text == text  # untouched
    assert len(state.refusals) == 1
    assert state.refusals[0]["mode"] == "live"
    assert [e.get("refusal") for e in log_lines(rig.log)] == [True, None]


@pytest.mark.req("HARDEN-1")
def test_state_calls_never_carry_the_body(rig: Rig) -> None:
    rig.upstream.reply("ok")
    headers_bad = {"x-api-key": "wrong"}
    with rig.proxy("record") as p:
        httpx.post(p.url + "/v1/messages", json=body("one"), headers=OK_HEADERS, timeout=30)
        httpx.post(p.url + "/v1/messages", json=body("two"), headers=headers_bad, timeout=30)
        httpx.post(
            p.url + "/v1/messages",
            json=body("three"),
            headers={**OK_HEADERS, "anthropic-beta": "fallback-credit-2026-06-01"},
            timeout=30,
        )
        state = p.proxy.state
    assert len(state.calls) == 3
    assert all("body" not in c for c in state.calls)
    assert all("body" in e for e in log_lines(rig.log))  # the log keeps the scrubbed body


# -- log shape for non-object bodies ------------------------------------------------------------


@pytest.mark.req("HARDEN-1")
@pytest.mark.parametrize("payload", [[1, 2, 3], 42, "just a string", True])
def test_a_json_body_that_is_not_an_object_is_logged_parsed_not_raw_or_unparsed(
    rig: Rig, payload: Any
) -> None:
    with rig.proxy("record") as p:
        httpx.post(p.url + "/v1/messages", json=payload, headers=OK_HEADERS, timeout=30)
    [entry] = log_lines(rig.log)
    assert entry["body"] == payload
    assert entry["body"] != UNPARSED_BODY
    assert entry["model"] is None
    assert entry["stream"] is False


@pytest.mark.req("HARDEN-1")
def test_an_empty_body_logs_none_and_a_garbled_one_the_placeholder(rig: Rig) -> None:
    with rig.proxy("record") as p:
        httpx.post(p.url + "/v1/messages", content=b"", headers=OK_HEADERS, timeout=30)
        httpx.post(p.url + "/v1/messages", content=b"{oops", headers=OK_HEADERS, timeout=30)
    assert [e["body"] for e in log_lines(rig.log)] == [None, UNPARSED_BODY]
