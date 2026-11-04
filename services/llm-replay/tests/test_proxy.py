"""The record/replay proxy against a scripted upstream (FOUND-2; NFR-16; changes [3], [5], [9]).

No network: the "upstream" is ScriptedLLM. FAKE_REAL_KEY stands in for the real key the proxy
would load from the key file.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from northwind_common.llm import PLACEHOLDER_API_KEY, anthropic_client
from northwind_llm_replay.ledger import Ledger
from northwind_llm_replay.proxy import ProxyConfig, RunningProxy, encode_test_id
from northwind_llm_replay.scripted import ScriptedLLM

FAKE_REAL_KEY = "nwfake-" + "realkey" * 6  # fake; built at runtime
PAN = "4111" + "1111" * 3
OK_HEADERS = {"x-api-key": PLACEHOLDER_API_KEY, "anthropic-version": "2023-06-01"}


def req_body(
    text: str = "hello", *, stream: bool = False, model: str = "claude-haiku-5-5", **extra: Any
) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": text}],
        "stream": stream,
        **extra,
    }


@dataclass
class Rig:
    upstream: ScriptedLLM
    tmp: Path
    ledger: Ledger
    key_loads: list[int]

    def proxy(self, mode: str, **kw: Any) -> RunningProxy:
        def loader() -> str:
            self.key_loads.append(1)
            return FAKE_REAL_KEY

        cfg = ProxyConfig(
            mode=mode,
            suite="t",
            cassette_root=self.tmp / "cassettes",
            ledger=self.ledger,
            log_path=self.tmp / "proxy-log" / "t.jsonl",
            upstream_url=self.upstream.url,
            key_loader=loader,
            secret_for_scan=FAKE_REAL_KEY,
            **kw,
        )
        return RunningProxy(cfg)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    with ScriptedLLM(require_placeholder=False) as upstream:
        ledger = Ledger(
            tmp_path / "ledger.jsonl", budget_usd=1.0, run_budget_usd=None, run_id="r", suite="t"
        )
        yield Rig(upstream, tmp_path, ledger, [])


def post(
    url: str, body: Any, headers: dict[str, str] = OK_HEADERS, path: str = "/v1/messages"
) -> httpx.Response:
    return httpx.post(url + path, json=body, headers=headers, timeout=30)


@pytest.mark.req("FOUND-2")
@pytest.mark.parametrize("stream", [False, True])
def test_record_then_replay_is_byte_identical(rig: Rig, stream: bool) -> None:
    rig.upstream.reply("recorded answer", input_tokens=100, output_tokens=7)
    with rig.proxy("record") as p:
        recorded = post(p.url, req_body(stream=stream))
    assert recorded.status_code == 200
    assert len(list((rig.tmp / "cassettes" / "t").glob("*.json"))) == 1
    with rig.proxy("replay") as p:
        replayed = post(p.url, req_body(stream=stream))
    assert replayed.status_code == 200
    assert replayed.content == recorded.content
    assert len(rig.upstream.requests) == 1  # replay never went upstream
    assert ("text/event-stream" in replayed.headers["content-type"]) is stream


@pytest.mark.req("FOUND-2")
def test_record_mode_replays_known_requests_for_free(rig: Rig) -> None:
    rig.upstream.reply("once")
    with rig.proxy("record") as p:
        post(p.url, req_body())
        post(p.url, req_body())
    assert len(rig.upstream.requests) == 1
    assert rig.ledger.totals().settled_calls == 1


@pytest.mark.req("FOUND-2")
def test_miss_is_a_structured_error_with_a_diff_naming_the_field(rig: Rig) -> None:
    rig.upstream.reply("x")
    with rig.proxy("record") as p:
        post(p.url, req_body("What sizes does P0001 come in?"))
    with rig.proxy("replay") as p:
        r = post(p.url, req_body("What colours does P0001 come in?"))
        misses = p.proxy.state.misses
    assert r.status_code == 400
    err = r.json()
    assert err["isError"] is True
    assert err["errorCategory"] == "validation"
    assert err["isRetryable"] is False
    diff_lines = err["diff"].splitlines()
    assert any(ln.startswith("-") and "What sizes does P0001" in ln for ln in diff_lines)
    assert any(ln.startswith("+") and "What colours does P0001" in ln for ln in diff_lines)
    assert len(misses) == 1
    assert rig.key_loads == [1]  # only the record run loaded the key


@pytest.mark.req("FOUND-2")
def test_sequence_matching_serves_by_test_and_order_and_marks_stale(rig: Rig) -> None:
    rig.upstream.reply("first").reply("second")
    tid = "tests/test_x.py::test_flow"
    with rig.proxy("record") as p:
        base = f"{p.url}/_t/{encode_test_id(tid)}"
        post(base, req_body("a"))
        post(base, req_body("b"))
    with rig.proxy("replay", match="sequence") as p:
        base = f"{p.url}/_t/{encode_test_id(tid)}"
        r1 = post(base, req_body("a, edited prompt"))
        r2 = post(base, req_body("b, edited prompt"))
        state = p.proxy.state
    assert r1.json()["content"][0]["text"] == "first"
    assert r2.json()["content"][0]["text"] == "second"
    assert state.stale == [f"{tid}#0", f"{tid}#1"]
    assert state.misses == []


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_budget_breach_is_refused_with_zero_upstream_calls(rig: Rig) -> None:
    rig.ledger.settle(
        rig.ledger.reserve(model="claude-haiku-5-5", worst_case_usd=0.99999, request_hash="h"), None
    )
    rig.upstream.reply("never served")
    with rig.proxy("live") as p:
        r = post(p.url, req_body())
    assert r.status_code == 402
    assert r.json()["errorCategory"] == "business"
    assert r.json()["isRetryable"] is False
    assert rig.upstream.requests == []


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_stream_without_final_usage_is_charged_worst_case_and_not_recorded(rig: Rig) -> None:
    rig.upstream.reply_cut_stream()
    with rig.proxy("record") as p:
        post(p.url, req_body(stream=True))
    t = rig.ledger.totals()
    assert t.settled_calls == 1
    assert t.outstanding_usd == 0
    body_bytes = len(json.dumps(req_body(stream=True)).encode())
    from northwind_llm_replay.pricing import worst_case_usd

    assert t.spent_usd == pytest.approx(
        worst_case_usd("claude-haiku-5-5", body_bytes, 64), rel=0.05
    )
    assert not list((rig.tmp / "cassettes").rglob("*.json"))


@pytest.mark.req("FOUND-2")
def test_upstream_error_response_is_not_billed_or_recorded(rig: Rig) -> None:
    rig.upstream.reply_raw(
        529, json.dumps({"type": "error", "error": {"type": "overloaded_error"}})
    )
    with rig.proxy("record") as p:
        r = post(p.url, req_body())
    assert r.status_code == 529
    assert rig.ledger.totals().spent_usd == 0
    assert not list((rig.tmp / "cassettes").rglob("*.json"))


@pytest.mark.req("FOUND-2")
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"x-api-key": "wrong"},
        {"x-api-key": "sk-ant-" + "notreallyakey000000"},
        {"authorization": "Bearer " + "oauth-token-from-a-subscription"},
        {"x-api-key": PLACEHOLDER_API_KEY, "authorization": f"Bearer {PLACEHOLDER_API_KEY}"},
    ],
    ids=["none", "wrong", "real-looking-key", "oauth-bearer", "two-credentials"],
)
def test_only_the_placeholder_as_single_credential_is_accepted(
    rig: Rig, headers: dict[str, str]
) -> None:
    """Hard pass/fail (user change [5]): anything but the placeholder never reaches upstream."""
    rig.upstream.reply("x")
    with rig.proxy("live") as p:
        r = post(p.url, req_body(), headers={"anthropic-version": "2023-06-01", **headers})
    assert r.status_code == 401
    assert r.json()["errorCategory"] == "permission"
    assert rig.upstream.requests == []
    assert rig.key_loads == []


@pytest.mark.req("FOUND-2")
def test_placeholder_bearer_from_anthropic_sdk_is_accepted(rig: Rig) -> None:
    rig.upstream.reply("from the sdk")
    with rig.proxy("live") as p:
        msg = anthropic_client(base_url=p.url, max_retries=0).messages.create(
            **{k: v for k, v in req_body().items() if k != "stream"}
        )
    assert msg.content[0].text == "from the sdk"


@pytest.mark.req("FOUND-2")
def test_real_key_goes_only_upstream_never_into_cassette_log_or_ledger(rig: Rig) -> None:
    rig.upstream.reply("x").reply("y")
    with rig.proxy("record") as p:
        post(p.url, req_body("one"))
        post(p.url, req_body("two", stream=True))
    up = rig.upstream.requests
    assert [r["headers"]["x-api-key"] for r in up] == [FAKE_REAL_KEY, FAKE_REAL_KEY]
    assert all("authorization" not in r["headers"] for r in up)
    for f in [
        *(rig.tmp / "cassettes").rglob("*.json"),
        rig.tmp / "proxy-log" / "t.jsonl",
        rig.tmp / "ledger.jsonl",
    ]:
        text = f.read_text()
        assert FAKE_REAL_KEY not in text, f
        assert FAKE_REAL_KEY[:16] not in text, f
        assert PLACEHOLDER_API_KEY not in text, f  # credential values are never logged


@pytest.mark.req("FOUND-2")
def test_dirty_cassette_is_refused_by_the_scanner(rig: Rig) -> None:
    rig.upstream.reply(f"your card {PAN} is saved")
    with rig.proxy("record") as p:
        r = post(p.url, req_body())
        failures = p.proxy.state.scan_failures
    assert r.status_code == 200  # the caller got the answer, but nothing was written
    assert failures and "luhn-valid-pan" in failures[0]
    assert not list((rig.tmp / "cassettes").rglob("*.json"))


@pytest.mark.req("FOUND-2")
@pytest.mark.parametrize(
    ("path", "body", "status"),
    [
        ("/v1/messages/batches", req_body(), 403),
        ("/v1/models", None, 403),
        ("/v1/messages", req_body(model="claude-unknown-9"), 400),
        ("/v1/messages", req_body(inference_geo="us"), 400),
        ("/v1/messages", {"model": "claude-haiku-5-5", "messages": []}, 400),
    ],
    ids=["batches", "models", "unpriced-model", "inference-geo", "no-max-tokens"],
)
def test_requests_the_ledger_cannot_price_are_refused(
    rig: Rig, path: str, body: Any, status: int
) -> None:
    with rig.proxy("live") as p:
        r = post(p.url, body, path=path)
    assert r.status_code == status
    assert r.json()["isError"] is True
    assert rig.upstream.requests == []
    assert rig.ledger.totals().spent_usd == 0


@pytest.mark.req("FOUND-2")
def test_replay_mode_never_loads_the_key_or_calls_upstream(rig: Rig) -> None:
    with rig.proxy("replay") as p:
        post(p.url, req_body())
    assert rig.key_loads == []
    assert rig.upstream.requests == []
