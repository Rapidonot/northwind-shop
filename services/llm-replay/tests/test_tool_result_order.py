"""Tool results in `tool_use` order (FOUND-2; row #16, decision 0001's amendment). Failing first.

The Claude CLI runs read-only tools in parallel and appends their `tool_result` blocks in
completion order (docs/reviews/2026-11-23-16-tool-order-probe.txt), so one model turn can produce
request bodies that differ only in the order of those blocks, and a replay misses on timing. The
proxy puts each user message's results that answer a `tool_use` of the preceding assistant message
into that message's `tool_use` order, straight after it parses the body (`Proxy.handle`), so scrub,
hash, log, the runner gate, the ledger reservation and the upstream send all see the same bytes.
When nothing changes, the raw bytes go on untouched. Spec: docs/reviews/2026-11-17-16-plan.md,
"Addendum 8, update 1" and "update 2".

No spend, no network beyond loopback. The upstream is a tiny capturing server (it keeps the raw
bytes, which `ScriptedLLM` does not) or `ScriptedLLM` where parsed bodies are enough.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from northwind_common.llm import PLACEHOLDER_API_KEY
from northwind_llm_replay import runner
from northwind_llm_replay.cassette import Cassette, CassetteStore
from northwind_llm_replay.ledger import Ledger
from northwind_llm_replay.pricing import worst_case_usd
from northwind_llm_replay.proxy import ProxyConfig, RunningProxy, encode_test_id
from northwind_llm_replay.runner import Window, create_runner_app
from northwind_llm_replay.scripted import (
    ScriptedLLM,
    ServerThread,
    blocks_sse,
    message_json,
)
from northwind_llm_replay.scrub import SCRUB_RULES, ScrubRule, match_key, request_hash, scrub

pytestmark = pytest.mark.req("FOUND-2")

FAKE_REAL_KEY = "nwfake-" + "realkey" * 6  # fake; built at runtime
PAN = "4111" + "1111" * 3  # a sandbox test card, built at runtime
OK_HEADERS = {
    "x-api-key": PLACEHOLDER_API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}
REPO = Path(__file__).resolve().parents[3]
MODEL = "claude-haiku-5-5"


# -- helpers ---------------------------------------------------------------------------------


def ordered_tool_results(body: Any) -> Any:
    """`northwind_llm_replay.tool_order.ordered_tool_results`, imported late so that a missing
    module fails the tests that need it, not the whole file."""
    module = importlib.import_module("northwind_llm_replay.tool_order")
    return module.ordered_tool_results(body)


def tool_use(tool_id: str) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": "get_price",
        "input": {"variant_id": tool_id},
    }


def result(tool_id: str, text: str | None = None) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "content": text if text is not None else f"result for {tool_id}",
    }


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def conversation(
    results: Sequence[str] = ("a", "b"),
    *,
    uses: Sequence[str] = ("a", "b"),
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """User asks, assistant calls `uses` in one turn, user message carries `results` in order."""
    return {
        "model": MODEL,
        "max_tokens": 64,
        "stream": False,
        "metadata": {"user_id": json.dumps({"device_id": "dev-1", "session_id": "s-1"})},
        "messages": [
            {"role": "user", "content": [text_block("What do they cost? café")]},
            {"role": "assistant", "content": [text_block("Checking."), *map(tool_use, uses)]},
            {"role": "user", "content": [result(i, (extra or {}).get(i)) for i in results]},
        ],
    }


def result_ids(body: Mapping[str, Any], message: int = -1) -> list[str]:
    content = body["messages"][message]["content"]
    return [b["tool_use_id"] for b in content if b.get("type") == "tool_result"]


def odd_bytes(body: Any) -> bytes:
    """Serialisation no re-serialiser would reproduce: indented, raw non-ASCII, trailing space.
    The padding also makes `len(raw)` differ a lot from any re-serialisation."""
    return json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8") + b"\n" + b" " * 4000


class RawUpstream:
    """A stand-in for the Messages API that keeps the exact bytes it was sent."""

    def __init__(self) -> None:
        self.raw: list[bytes] = []
        self.paths: list[str] = []
        self._lock = threading.Lock()
        app = Starlette(routes=[Route("/{path:path}", self._handle, methods=["GET", "POST"])])
        self._server = ServerThread(app)

    @property
    def url(self) -> str:
        return self._server.url

    def __enter__(self) -> RawUpstream:
        self._server.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.stop()

    async def _handle(self, request: Request) -> Response:
        data = await request.body()
        with self._lock:
            self.raw.append(data)
            self.paths.append(request.url.path)
            n = len(self.raw)
        usage = {"input_tokens": 10, "output_tokens": 5}
        return JSONResponse(message_json(MODEL, "ok", usage, f"msg_raw{n:03d}"))


class SpyGate:
    """Implements `proxy.Gate` and records what the proxy hands the runner's hooks."""

    def __init__(self) -> None:
        self.refused: list[tuple[bytes, Any]] = []
        self.leak_checks: list[bytes] = []
        self.reports: list[dict[str, Any]] = []

    def refuse(
        self,
        method: str,
        raw_path: str,
        query: str,
        raw: bytes,
        body: Any,
        headers: dict[str, str],
    ) -> tuple[str, Response] | None:
        self.refused.append((raw, body))
        return None

    def leaks(self, text: str) -> bool:
        return False

    def request_leaks(self, raw: bytes, headers: dict[str, str]) -> bool:
        self.leak_checks.append(raw)
        return False

    def blocked(self) -> Response:
        return JSONResponse({"blocked": True}, status_code=403)

    def report(self, entry: dict[str, Any]) -> None:
        self.reports.append(entry)


@dataclass
class Rig:
    upstream: RawUpstream
    tmp: Path
    ledger: Ledger

    def proxy(self, mode: str, **kw: Any) -> RunningProxy:
        cfg = ProxyConfig(
            mode=mode,
            suite="t",
            cassette_root=self.tmp / "cassettes",
            ledger=self.ledger,
            log_path=self.tmp / "log" / "t.jsonl",
            upstream_url=self.upstream.url,
            key_loader=lambda: FAKE_REAL_KEY,
            secret_for_scan=FAKE_REAL_KEY,
            **kw,
        )
        return RunningProxy(cfg)

    @property
    def cassettes(self) -> list[Path]:
        return sorted((self.tmp / "cassettes" / "t").glob("*.json"))

    def log(self) -> list[dict[str, Any]]:
        lines = (self.tmp / "log" / "t.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def reserved(self) -> list[float]:
        lines = (self.tmp / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        entries = [json.loads(line) for line in lines]
        return [e["amount_usd"] for e in entries if e.get("type") == "reserve"]


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    with RawUpstream() as upstream:
        ledger = Ledger(
            tmp_path / "ledger.jsonl", budget_usd=1.0, run_budget_usd=None, run_id="r", suite="t"
        )
        yield Rig(upstream, tmp_path, ledger)


def send(
    url: str, raw: bytes, path: str = "/v1/messages", headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.post(
        url + path, content=raw, headers=headers or OK_HEADERS, timeout=30, trust_env=False
    )


def as_json(body: Any) -> bytes:
    return json.dumps(body).encode("utf-8")


# -- 1. one key for both orders ----------------------------------------------------------------


@pytest.mark.parametrize("second_mode", ["record", "replay"])
@pytest.mark.parametrize("first,second", [(["a", "b"], ["b", "a"]), (["b", "a"], ["a", "b"])])
def test_two_orders_of_the_same_results_are_one_cassette(
    rig: Rig, first: list[str], second: list[str], second_mode: str
) -> None:
    with rig.proxy("record") as p:
        recorded = send(p.url, as_json(conversation(first)))
    assert recorded.status_code == 200
    assert len(rig.upstream.raw) == 1 and len(rig.cassettes) == 1
    with rig.proxy(second_mode) as p:
        replayed = send(p.url, as_json(conversation(second)))
        misses = p.proxy.state.misses
    assert replayed.status_code == 200, replayed.text
    assert misses == []
    assert replayed.content == recorded.content
    assert len(rig.upstream.raw) == 1  # the other order was a hit, not a second upstream call
    assert len(rig.cassettes) == 1


def test_one_proxy_run_serves_the_other_order_from_the_cassette_it_just_wrote(rig: Rig) -> None:
    with rig.proxy("record") as p:
        send(p.url, as_json(conversation(["b", "a"])))
        again = send(p.url, as_json(conversation(["a", "b"])))
        outcomes = [c["outcome"] for c in p.proxy.state.calls]
    assert again.status_code == 200
    assert outcomes == ["recorded", "hit"]
    assert len(rig.upstream.raw) == 1


def test_a_different_set_of_results_is_still_a_different_request(rig: Rig) -> None:
    """Ordering must not collapse requests that really differ."""
    with rig.proxy("record") as p:
        send(p.url, as_json(conversation(["a", "b"])))
        send(p.url, as_json(conversation(["b", "a"], extra={"a": "another price"})))
    assert len(rig.upstream.raw) == 2
    assert len(rig.cassettes) == 2


# -- 2. the bytes that go upstream -------------------------------------------------------------


def test_reversed_results_are_forwarded_in_tool_use_order(rig: Rig) -> None:
    reversed_body = conversation(["b", "a"])
    with rig.proxy("record") as p:
        assert send(p.url, odd_bytes(reversed_body)).status_code == 200
    [forwarded] = rig.upstream.raw
    sent = json.loads(forwarded)
    assert result_ids(sent) == ["a", "b"]
    assert sent == conversation(["a", "b"])  # everything else untouched, non-ASCII intact


def test_an_in_order_body_is_forwarded_byte_identical(rig: Rig) -> None:
    raw = odd_bytes(conversation(["a", "b"]))
    with rig.proxy("record") as p:
        assert send(p.url, raw).status_code == 200
    assert rig.upstream.raw == [raw]


def test_a_body_with_no_tool_results_is_forwarded_byte_identical(rig: Rig) -> None:
    body = {"model": MODEL, "max_tokens": 64, "messages": [{"role": "user", "content": "hi café"}]}
    raw = odd_bytes(body)
    with rig.proxy("record") as p:
        assert send(p.url, raw).status_code == 200
    assert rig.upstream.raw == [raw]


def test_the_slots_rule_holds_through_the_proxy(rig: Rig) -> None:
    body = conversation(["a", "b"])
    body["messages"][2]["content"] = [
        text_block("t1"),
        result("b"),
        text_block("t2"),
        result("a"),
    ]
    with rig.proxy("record") as p:
        assert send(p.url, as_json(body)).status_code == 200
    [forwarded] = rig.upstream.raw
    content = json.loads(forwarded)["messages"][2]["content"]
    assert content == [text_block("t1"), result("a"), text_block("t2"), result("b")]


def test_the_live_mode_forwards_the_reordered_body_too(rig: Rig) -> None:
    with rig.proxy("live") as p:
        assert send(p.url, as_json(conversation(["b", "a"]))).status_code == 200
    assert result_ids(json.loads(rig.upstream.raw[0])) == ["a", "b"]
    assert rig.cassettes == []


# -- 3. the helper: slots ----------------------------------------------------------------------


def user_content(body: Any, message: int = -1) -> list[Any]:
    content: list[Any] = body["messages"][message]["content"]
    return content


def test_results_fill_the_slots_results_already_hold() -> None:
    body = conversation()
    body["messages"][2]["content"] = [
        text_block("t1"),
        result("b"),
        text_block("t2"),
        result("a"),
    ]
    out = ordered_tool_results(body)
    assert out is not None
    assert user_content(out) == [text_block("t1"), result("a"), text_block("t2"), result("b")]


def test_a_result_for_no_tool_use_keeps_its_place() -> None:
    body = conversation(["b", "a"])
    body["messages"][2]["content"] = [result("b"), result("zz"), result("a")]
    out = ordered_tool_results(body)
    assert out is not None
    assert user_content(out) == [result("a"), result("zz"), result("b")]


def test_a_result_of_an_earlier_turn_is_not_a_slot_of_this_one() -> None:
    body = conversation(["a", "b"])
    body["messages"] += [
        {"role": "assistant", "content": [tool_use("c"), tool_use("d")]},
        {"role": "user", "content": [result("d"), result("a"), result("c")]},
    ]
    out = ordered_tool_results(body)
    assert out is not None
    assert user_content(out) == [result("c"), result("a"), result("d")]


def test_each_user_message_follows_its_own_preceding_assistant_message() -> None:
    body = conversation(["b", "a"])
    body["messages"] += [
        {"role": "assistant", "content": [tool_use("c"), tool_use("d")]},
        {"role": "user", "content": [result("d"), result("c")]},
    ]
    out = ordered_tool_results(body)
    assert out is not None
    assert result_ids(out, 2) == ["a", "b"]
    assert result_ids(out, 4) == ["c", "d"]


def test_non_result_blocks_never_move() -> None:
    body = conversation()
    mid = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA"}}
    body["messages"][2]["content"] = [result("b"), mid, text_block("note"), result("a")]
    out = ordered_tool_results(body)
    assert out is not None
    assert user_content(out) == [result("a"), mid, text_block("note"), result("b")]


def test_duplicate_results_for_one_tool_use_keep_their_relative_order() -> None:
    body = conversation()
    first, second = result("b", "first b"), result("b", "second b")
    body["messages"][2]["content"] = [first, result("a"), second]
    out = ordered_tool_results(body)
    assert out is not None
    assert user_content(out) == [result("a"), first, second]


def test_an_in_order_body_gives_none() -> None:
    assert ordered_tool_results(conversation(["a", "b"])) is None


def test_one_result_and_unlisted_results_give_none() -> None:
    assert ordered_tool_results(conversation(["b"], uses=["a", "b"])) is None
    assert ordered_tool_results(conversation(["y", "x"], uses=["a", "b"])) is None


def test_a_string_content_user_message_is_untouched() -> None:
    body = conversation(["b", "a"])
    body["messages"][2]["content"] = "b then a, as text"
    assert ordered_tool_results(body) is None


@pytest.mark.parametrize(
    "body",
    [
        {"model": MODEL, "max_tokens": 1},
        {"model": MODEL, "messages": []},
        {"model": MODEL, "messages": "not a list"},
        [1, 2, 3],
        "text",
        None,
    ],
)
def test_a_body_with_no_messages_is_untouched(body: Any) -> None:
    assert ordered_tool_results(body) is None


def test_a_user_message_with_no_preceding_assistant_message_is_untouched() -> None:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [result("b"), result("a")]}],
    }
    assert ordered_tool_results(body) is None


def test_the_cache_breakpoint_stays_in_its_slot_when_results_move() -> None:
    # Found by the end-to-end test while implementing (row #16): the CLI marks the message's last
    # block as a cache breakpoint, so `cache_control` belongs to the slot, not to the result.
    body = conversation(["b", "a"])
    content = user_content(body)
    slots = [i for i, block in enumerate(content) if block.get("type") == "tool_result"]
    content[slots[-1]]["cache_control"] = {"type": "ephemeral"}
    ordered = ordered_tool_results(body)
    out = user_content(ordered)
    assert [out[i]["tool_use_id"] for i in slots] == ["a", "b"]
    assert out[slots[-1]].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in out[slots[0]]


def test_the_helper_does_not_modify_its_input() -> None:
    body = conversation(["b", "a"])
    before = copy.deepcopy(body)
    out = ordered_tool_results(body)
    assert out is not None and out is not body
    assert body == before
    assert out != before  # and the result is the new, ordered one
    assert result_ids(out) == ["a", "b"]
    # everything outside the user message's content is carried over as it was
    assert {k: v for k, v in out.items() if k != "messages"} == {
        k: v for k, v in before.items() if k != "messages"
    }
    assert out["messages"][:2] == before["messages"][:2]


# -- 4. a body that does not parse -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b'{"messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b"',
        b"not json at all {]",
        b"[1, 2, 3]",
    ],
    ids=["truncated", "not-json", "not-an-object"],
)
def test_a_body_that_does_not_parse_is_forwarded_raw(rig: Rig, raw: bytes) -> None:
    with rig.proxy("live") as p:
        reply = send(p.url, raw, path="/v1/messages/count_tokens")
    assert reply.status_code == 200
    assert rig.upstream.raw == [raw]
    assert rig.upstream.paths == ["/v1/messages/count_tokens"]


def test_a_body_that_is_not_utf8_is_refused_and_never_forwarded(rig: Rig) -> None:
    # Not a parse failure the proxy forwards: `json.loads` raises outside JSONDecodeError, which
    # the runner turns into `refused-unreadable` (test_runner.py) and a local proxy into an error.
    # Either way nothing goes upstream (row #16; reported by test-writer, kept fail-closed).
    with rig.proxy("live") as p:
        reply = send(p.url, b'{"messages": "\xff\xfe not utf-8"}', path="/v1/messages/count_tokens")
    assert reply.status_code >= 400
    assert rig.upstream.raw == []


# -- 5. scrub, scan and ledger see the bytes actually forwarded --------------------------------


def test_the_cassette_and_the_log_hold_the_reordered_scrubbed_body(rig: Rig) -> None:
    with rig.proxy("record") as p:
        send(p.url, odd_bytes(conversation(["b", "a"])))
    [cassette_path] = rig.cassettes
    cassette = Cassette.from_json(cassette_path.read_text(encoding="utf-8"))
    expected = scrub(conversation(["a", "b"]))
    assert cassette.match["body"] == expected
    assert result_ids(cassette.match["body"]) == ["a", "b"]
    assert "dev-1" not in json.dumps(cassette.match)  # still scrubbed
    key = match_key("POST", "/v1/messages", {"anthropic-version": "2023-06-01"}, expected)
    assert cassette.hash == request_hash(key) == cassette_path.stem
    [entry] = rig.log()
    assert entry["body"] == expected
    assert entry["hash"] == cassette.hash[: len(entry["hash"])]


def test_the_log_of_an_in_order_body_is_unchanged(rig: Rig) -> None:
    with rig.proxy("record") as p:
        send(p.url, odd_bytes(conversation(["a", "b"])))
    [entry] = rig.log()
    assert entry["body"] == scrub(conversation(["a", "b"]))


@pytest.mark.parametrize("order", [["b", "a"], ["a", "b"]])
def test_the_gate_sees_exactly_the_bytes_that_go_upstream(rig: Rig, order: list[str]) -> None:
    gate = SpyGate()
    original = odd_bytes(conversation(order))
    with rig.proxy("live", gate=gate) as p:
        assert send(p.url, original).status_code == 200
    [forwarded] = rig.upstream.raw
    [(refuse_raw, refuse_body)] = gate.refused
    assert gate.leak_checks == [forwarded]  # request_leaks: the forwarded bytes, nothing else
    assert refuse_raw == forwarded  # refuse: the same bytes
    assert refuse_body == json.loads(forwarded)  # and the parsed form of them
    assert result_ids(json.loads(forwarded)) == ["a", "b"]
    if order == ["a", "b"]:
        assert forwarded == original  # byte-identical when nothing was reordered


def test_the_ledger_reserves_for_the_forwarded_length_not_the_original(rig: Rig) -> None:
    original = odd_bytes(conversation(["b", "a"]))
    with rig.proxy("record") as p:
        send(p.url, original)
    [forwarded] = rig.upstream.raw
    assert len(forwarded) < len(original)  # the trailing padding did not go upstream
    want = worst_case_usd(MODEL, len(forwarded), 64)
    assert rig.reserved() == [want]
    [entry] = rig.log()
    assert entry["reserved_usd"] == want


def test_the_ledger_reserves_for_the_original_length_when_nothing_moved(rig: Rig) -> None:
    original = odd_bytes(conversation(["a", "b"]))
    with rig.proxy("record") as p:
        send(p.url, original)
    assert rig.reserved() == [worst_case_usd(MODEL, len(original), 64)]


@pytest.mark.parametrize("order", [["b", "a"], ["a", "b"]])
def test_a_card_number_in_a_result_is_scanned_the_same_in_either_order(
    rig: Rig, order: list[str]
) -> None:
    body = conversation(order, extra={"a": f"card {PAN}"})
    with rig.proxy("record") as p:
        send(p.url, as_json(body))
        failures = p.proxy.state.scan_failures
    assert len(failures) == 1  # the scan ran over the cassette and refused it
    assert rig.cassettes == []


# The real runner gate, with a real (fake) key.
@dataclass
class RunnerRig:
    upstream: RawUpstream
    ledger: Ledger
    url: str
    token: str


@contextmanager
def runner_rig(tmp: Path) -> Iterator[RunnerRig]:
    runner.SESSIONS.clear()
    with RawUpstream() as upstream:
        ledger = Ledger(
            tmp / "runner-ledger.jsonl",
            budget_usd=1.0,
            run_budget_usd=0.5,
            run_id="window",
            suite="t",
        )
        window = Window(suite="t", minutes=30, idle_minutes=10, clock=time.monotonic)
        app, _ = create_runner_app(
            window, FAKE_REAL_KEY, ledger, tmp, upstream_url=upstream.url, out=lambda _line: None
        )
        server = ServerThread(app)
        server.start()
        try:
            claim = httpx.post(f"{server.url}/_runner/claim", json={"suite": "t"}, timeout=30)
            assert claim.status_code == 200, claim.text
            yield RunnerRig(upstream, ledger, server.url, claim.json()["token"])
        finally:
            server.stop()
            runner.SESSIONS.clear()


def test_the_runner_forwards_the_reordered_bytes(tmp_path: Path) -> None:
    with runner_rig(tmp_path) as r:
        reply = send(r.url, as_json(conversation(["b", "a"])), headers={"x-api-key": r.token})
    assert reply.status_code == 200, reply.text
    assert result_ids(json.loads(r.upstream.raw[0])) == ["a", "b"]


@pytest.mark.parametrize("order", [["b", "a"], ["a", "b"]])
def test_a_key_fragment_in_a_result_is_blocked_in_either_order(
    tmp_path: Path, order: list[str]
) -> None:
    body = conversation(order, extra={"a": f"look: {FAKE_REAL_KEY[3:25]}"})
    with runner_rig(tmp_path) as r:
        reply = send(r.url, as_json(body), headers={"x-api-key": r.token})
    assert reply.status_code == 502  # the runner's blocked response (as in test_runner.py)
    assert reply.json()["errorCategory"] == "permission"
    assert FAKE_REAL_KEY[3:25] not in reply.text
    assert r.upstream.raw == []
    assert r.ledger.totals().spent_usd == 0


# -- 6. scrub is position-independent ----------------------------------------------------------

RESULT_RULE = ScrubRule(
    path="messages[*].content[*].content",
    reason="test: a field inside tool_result blocks",
    pattern=r"session-[0-9a-f]+",
)


def test_scrub_of_a_reordered_body_is_the_reordered_scrub() -> None:
    texts = {"a": "price at session-1f3a ok", "b": "stock at session-9c2e ok"}
    body = conversation(["b", "a"], extra=texts)
    reordered = ordered_tool_results(body)
    assert reordered is not None and result_ids(reordered) == ["a", "b"]
    for rules in (SCRUB_RULES, (*SCRUB_RULES, RESULT_RULE)):
        want = ordered_tool_results(scrub(body, rules))
        assert want is not None and result_ids(want) == ["a", "b"]
        assert scrub(reordered, rules) == want
    # the custom rule really touched the tool_result content (the test is not vacuous)
    scrubbed = scrub(reordered, rules=(*SCRUB_RULES, RESULT_RULE))
    assert "session-1f3a" not in json.dumps(scrubbed)
    assert "<scrubbed:messages[*].content[*].content>" in json.dumps(scrubbed)
    # and the default rule (metadata.user_id) is independent of the order as well
    assert scrub(reordered)["metadata"] == scrub(body)["metadata"]


# -- 7. the repo's committed cassettes ---------------------------------------------------------


def test_every_committed_cassette_is_already_in_tool_use_order() -> None:
    offenders: list[str] = []
    checked = 0
    for path in sorted((REPO / "cassettes").rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        match = data.get("match") if isinstance(data, dict) else None
        if not isinstance(match, dict) or not isinstance(match.get("body"), dict):
            continue
        checked += 1
        if ordered_tool_results(match["body"]) is not None:
            offenders.append(
                f"{path.parent.name}/{path.name[:12]} test_id={data.get('test_id')!r} "
                f"seq={data.get('seq')}"
            )
    assert checked > 0, "no cassette with a parsed body was found under cassettes/"
    assert not offenders, (
        "these cassettes have tool_results out of tool_use order, so the reorder would change "
        "their key (re-record them):\n  " + "\n  ".join(offenders)
    )


# -- 8. miss_diff: the same test's cassettes first ---------------------------------------------

PATH = "/v1/messages?beta=true"


def match_for(text: str) -> dict[str, Any]:
    body = {"model": MODEL, "max_tokens": 64, "messages": [{"role": "user", "content": text}]}
    return match_key("POST", PATH, {"anthropic-version": "2023-06-01"}, body)


def cassette_for(hash_char: str, text: str, test_id: str | None) -> Cassette:
    return Cassette(
        hash=hash_char * 64,
        suite="s",
        recorded_at="2026-11-23T00:00:00Z",
        match=match_for(text),
        status=200,
        content_type="application/json",
        kind="json",
        body="{}",
        model=MODEL,
        usage=None,
        cost_usd=0.0,
        test_id=test_id,
        seq=0,
    )


QUESTION = "What sizes does P0001 come in, in blue?"
CLOSE = "What sizes does P0001 come in, in blue? Thanks"
FAR = "Completely different: what is the returns policy for orders placed in March?"


def miss_diff(store: Any, match: dict[str, Any], *args: Any, **kw: Any) -> str:
    """`CassetteStore.miss_diff`, called untyped: `test_id` is the signature this row adds."""
    diff: str = store.miss_diff(match, *args, **kw)
    return diff


def header_of(diff: str) -> str:
    return next(line for line in diff.splitlines() if line.startswith("---"))


def two_test_store(tmp_path: Path) -> CassetteStore:
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset())
    store.put(cassette_for("1", CLOSE, "tests/test_x.py::test_other"))  # closer text, other test
    store.put(cassette_for("2", FAR, "tests/test_x.py::test_mine"))  # farther text, same test
    return store


def test_miss_diff_compares_with_the_same_tests_cassettes_first(tmp_path: Path) -> None:
    store = two_test_store(tmp_path)
    diff = miss_diff(store, match_for(QUESTION), test_id="tests/test_x.py::test_mine")
    assert header_of(diff) == f"--- cassette {'2' * 12}"
    assert FAR in diff


def test_miss_diff_picks_the_most_similar_of_the_same_tests_cassettes(tmp_path: Path) -> None:
    store = two_test_store(tmp_path)
    store.put(cassette_for("3", CLOSE + "!", "tests/test_x.py::test_mine"))
    diff = miss_diff(store, match_for(QUESTION), test_id="tests/test_x.py::test_mine")
    assert header_of(diff) == f"--- cassette {'3' * 12}"


def test_miss_diff_falls_back_to_the_whole_suite_without_that_tests_cassettes(
    tmp_path: Path,
) -> None:
    store = two_test_store(tmp_path)
    diff = miss_diff(store, match_for(QUESTION), test_id="tests/test_x.py::test_new")
    assert header_of(diff) == f"--- cassette {'1' * 12}"


def test_miss_diff_without_a_test_id_still_compares_with_the_whole_suite(tmp_path: Path) -> None:
    store = two_test_store(tmp_path)
    assert header_of(miss_diff(store, match_for(QUESTION))) == f"--- cassette {'1' * 12}"
    assert header_of(miss_diff(store, match_for(QUESTION), None)) == f"--- cassette {'1' * 12}"
    capped = miss_diff(store, match_for(QUESTION), test_id=None, max_lines=3)
    assert "more diff lines" in capped and len(capped.splitlines()) == 4


def test_a_replay_miss_reports_the_diff_against_the_same_tests_cassette(tmp_path: Path) -> None:
    two_test_store(tmp_path)
    mine = "tests/test_x.py::test_mine"
    cfg = ProxyConfig(mode="replay", suite="s", cassette_root=tmp_path / "cassettes")
    body = {"model": MODEL, "max_tokens": 64, "messages": [{"role": "user", "content": QUESTION}]}
    with RunningProxy(cfg) as p:
        base = f"{p.url}/_t/{encode_test_id(mine)}"
        reply = send(base, as_json(body), path="/v1/messages?beta=true")
    assert reply.status_code == 400
    assert header_of(reply.json()["diff"]) == f"--- cassette {'2' * 12}"


# -- 9. end to end: the real CLI, a scripted model, a recording proxy --------------------------

E2E_DELAY = 1.5  # seconds one tool is held back so that the other finishes first
# Row 18a, N-1: the turn's tokens live 120 s, from when the turn mints them. Under load (3 failures
# in 47 runs) both gates refused the token before any tool ran. The servers' token gates read this
# clock instead of the machine's: it stands still `STILL_AFTER` seconds after the turn starts, so a
# token minted in that time is valid however long the CLI takes. The 120 s lifetime is unchanged.
STILL_AFTER = 60.0


def still_clock(after: float = STILL_AFTER) -> Callable[[], float]:
    start = time.time()
    return lambda: start + after


def e2e_catalog(
    delays: Mapping[str, Callable[[Mapping[str, Any]], float]],
    finished: list[str],
    clock: Callable[[], float],
) -> Any:
    """A real mcp-catalog whose handlers wait (without blocking the server's loop) before they
    answer, so tool calls started together finish in an order the test chooses."""
    from northwind_mcp_catalog.live import FixtureCatalog
    from northwind_mcp_catalog.server import CatalogServer, ToolHandler, ToolSpec
    from northwind_mcp_catalog.testing import KEY
    from northwind_mcp_catalog.tools import TOOLS

    def wrap(name: str, handler: ToolHandler, delay: Callable[[Mapping[str, Any]], float]) -> Any:
        async def slow(server: Any, claims: Any, args: Mapping[str, Any]) -> Any:
            await asyncio.sleep(delay(args))
            finished.append(f"{name}:{args.get('variant_id', args.get('product_id'))}")
            return await handler(server, claims, args)

        return slow

    tools = dict(TOOLS)
    for name, delay in delays.items():
        tools[name] = ToolSpec(TOOLS[name].tool, wrap(name, TOOLS[name].handler, delay))
    return CatalogServer(FixtureCatalog(), key=KEY, tools=tools, clock=clock)


@dataclass
class TurnRun:
    upstream_requests: list[dict[str, Any]]
    cassette_bodies: list[dict[str, Any]]


def record_turn(
    tmp: Path,
    blocks: list[dict[str, Any]],
    *,
    catalog: Any,
    retriever: Any,
    clock: Callable[[], float],
) -> TurnRun:
    """One shopper turn through the real CLI -> recording proxy -> ScriptedLLM, where the model
    asks for `blocks` in one assistant message and then answers."""
    from northwind_assistant.caller import Caller
    from northwind_assistant.catalog_client import CatalogEndpoint
    from northwind_assistant.rag_client import RagEndpoint
    from northwind_assistant.turn import stream_answer
    from northwind_common.llm import agent_runtime
    from northwind_mcp_catalog import testing as catalog_testing
    from northwind_mcp_rag.testing import KEY, serve_retriever

    us = Caller(region="US")
    ledger = Ledger(
        tmp / "ledger.jsonl", budget_usd=1.0, run_budget_usd=None, run_id="r", suite="t"
    )
    with (
        catalog_testing.running(catalog) as catalog_url,
        ScriptedLLM(require_placeholder=False) as llm,
    ):
        usage = {"input_tokens": 10, "output_tokens": 5}
        llm.reply_raw(
            200, blocks_sse("scripted", blocks, "tool_use", usage, "msg_turn"), "text/event-stream"
        )
        llm.reply_stream("Here you go [P0011].")
        cfg = ProxyConfig(
            mode="record",
            suite="t",
            cassette_root=tmp / "cassettes",
            ledger=ledger,
            log_path=tmp / "log.jsonl",
            upstream_url=llm.url,
            key_loader=lambda: FAKE_REAL_KEY,
            secret_for_scan=FAKE_REAL_KEY,
        )
        with RunningProxy(cfg) as proxy:
            runtime = agent_runtime(base_url=proxy.url, root=tmp / "agent")

            async def go(rag: RagEndpoint) -> None:
                endpoint = CatalogEndpoint(catalog_url, KEY)
                async for _ in stream_answer(
                    "Tell me about the Cairn jacket", us, rag, runtime, endpoint
                ):
                    pass

            with serve_retriever(retriever, Caller, key=KEY, clock=clock) as (url, _):
                asyncio.run(go(RagEndpoint(url, KEY)))
    # In request order: a cassette's file name is its request hash, so sorting by name orders
    # the turn's calls by hash, which any prompt change can flip (row #17's make ci). Each call
    # of the turn adds messages, so the message count is the request order.
    bodies = sorted(
        (
            json.loads(p.read_text(encoding="utf-8"))["match"]["body"]
            for p in (tmp / "cassettes" / "t").glob("*.json")
        ),
        key=lambda body: len(body["messages"]),
    )
    return TurnRun(llm.requests, bodies)


def e2e_retriever(slow: bool) -> Any:
    from northwind_rag.retrieval import RetrievedChunk

    source = RetrievedChunk("c1", "product", "P0011", None, "Brindlemoor Cairn Storm Jacket.")

    class Retriever:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str, caller: Any) -> list[Any]:
            self.calls += 1
            if (
                slow and self.calls > 1
            ):  # the first search is the turn's own; later ones are the tool's
                time.sleep(E2E_DELAY)
            return [source]

    return Retriever()


def two_tool_blocks() -> list[dict[str, Any]]:
    from northwind_assistant.tools import ALLOWED_TOOL
    from northwind_common.mcp_token import CATALOG_SERVER

    return [
        {"type": "tool_use", "id": "toolu_a", "name": ALLOWED_TOOL, "input": {"query": "cairn"}},
        {
            "type": "tool_use",
            "id": "toolu_b",
            "name": f"mcp__{CATALOG_SERVER}__get_product",
            "input": {"product_id": "P0011"},
        },
    ]


def second_request_body(run: TurnRun) -> dict[str, Any]:
    assert len(run.upstream_requests) >= 2, run.upstream_requests
    body: dict[str, Any] = scrub(run.upstream_requests[1]["body"])
    return body


def test_the_model_sees_the_same_second_request_whichever_tool_finishes_first(
    tmp_path: Path,
) -> None:
    """The probe's shape: retrieve_context (toolu_a) and get_product (toolu_b) in one turn. In one
    run the retriever is slow (b finishes first, so the CLI appends b, a); in the other the catalog
    is slow (a, b). The proxy must hand the model the same request both times."""
    finished_rag_slow: list[str] = []
    clock = still_clock()
    rag_slow = record_turn(
        tmp_path / "rag-slow",
        two_tool_blocks(),
        catalog=e2e_catalog({"get_product": lambda args: 0.0}, finished_rag_slow, clock),
        retriever=e2e_retriever(slow=True),
        clock=clock,
    )
    finished_catalog_slow: list[str] = []
    clock = still_clock()
    catalog_slow = record_turn(
        tmp_path / "catalog-slow",
        two_tool_blocks(),
        catalog=e2e_catalog({"get_product": lambda args: E2E_DELAY}, finished_catalog_slow, clock),
        retriever=e2e_retriever(slow=False),
        clock=clock,
    )
    first, second = second_request_body(rag_slow), second_request_body(catalog_slow)
    assert result_ids(first) == ["toolu_a", "toolu_b"]
    assert result_ids(second) == ["toolu_a", "toolu_b"]
    assert first == second
    # and what the proxy recorded is the same turn, in the same order
    assert rag_slow.cassette_bodies[-1] == catalog_slow.cassette_bodies[-1]


def test_five_parallel_prices_finishing_in_reverse_are_forwarded_in_tool_use_order(
    tmp_path: Path,
) -> None:
    """The #12 shape: five get_price calls on one server, the earliest the slowest."""
    from northwind_common import catalog_tool

    variants = [
        "P0011-SAG-REG-L",
        "P0011-SAG-REG-M",
        "P0011-SAG-REG-S",
        "P0011-SAG-REG-XL",
        "P0011-SAG-REG-XS",
    ]
    delay = {v: 0.3 * (len(variants) - i) for i, v in enumerate(variants)}  # earlier = slower
    blocks = [
        {
            "type": "tool_use",
            "id": f"toolu_p{i}",
            "name": catalog_tool.PRICE_TOOL,
            "input": {"variant_id": v},
        }
        for i, v in enumerate(variants)
    ]
    finished: list[str] = []
    clock = still_clock()
    run = record_turn(
        tmp_path,
        blocks,
        catalog=e2e_catalog({"get_price": lambda args: delay[args["variant_id"]]}, finished, clock),
        retriever=e2e_retriever(slow=False),
        clock=clock,
    )
    # the precondition: the tools really did finish in the reverse of tool_use order
    assert finished == [f"get_price:{v}" for v in reversed(variants)]
    forwarded = run.upstream_requests[1]["body"]
    assert result_ids(forwarded) == [f"toolu_p{i}" for i in range(5)]
    assert result_ids(second_request_body(run)) == [f"toolu_p{i}" for i in range(5)]
    assert result_ids(run.cassette_bodies[-1]) == [f"toolu_p{i}" for i in range(5)]


# -- after the security review (row #16, docs/reviews/2026-11-23-16-addendum8-security.raw.md) -----


def test_the_reorder_is_linear_in_the_body() -> None:
    # L1: many tool uses followed by many user messages must not cost users x tool uses.
    import time

    uses = [f"t{i}" for i in range(20_000)]
    body = {
        "messages": [
            {"role": "assistant", "content": [tool_use(i) for i in uses]},
            *({"role": "user", "content": []} for _ in range(20_000)),
            {"role": "user", "content": [result("t1"), result("t0")]},
        ]
    }
    started = time.monotonic()
    ordered = ordered_tool_results(body)
    assert time.monotonic() - started < 2.0
    assert result_ids(ordered) == ["t0", "t1"]


def _reordered_request(raw: bytes, body: Any) -> Any:
    module = importlib.import_module("northwind_llm_replay.tool_order")
    return module.reordered_request(raw, body)


def test_a_body_with_a_duplicate_key_is_not_reordered() -> None:
    # L2: the original bytes go on, so the runner gate still refuses it as `refused-body`.
    body = conversation(["b", "a"])
    raw = as_json(body)[:-1] + b',"model":"' + MODEL.encode() + b'"}'
    assert _reordered_request(raw, json.loads(raw)) is None


def test_a_body_that_is_not_utf8_is_not_reordered() -> None:
    # L2: `json.loads` accepts UTF-16; the gate's strict UTF-8 reading must see the original.
    body = conversation(["b", "a"])
    raw = json.dumps(body).encode("utf-16")
    assert _reordered_request(raw, json.loads(raw)) is None


@pytest.mark.parametrize("number", [b"1e400", b"NaN"])
def test_a_body_that_cannot_serialize_as_strict_json_is_not_reordered(number: bytes) -> None:
    # L3: `1e400` would come back as `Infinity`, invalid JSON upstream.
    raw = as_json(conversation(["b", "a"]))[:-1] + b',"temperature":' + number + b"}"
    assert _reordered_request(raw, json.loads(raw)) is None


def test_a_lone_surrogate_is_not_reordered() -> None:
    # L3: it would raise on encoding; the original bytes go on and are handled as before.
    raw = as_json(conversation(["b", "a"], extra={"a": "x"})).replace(b'"x"', b'"\\ud800"')
    assert _reordered_request(raw, json.loads(raw)) is None


def test_a_reorder_that_can_serialize_returns_the_bytes_and_body_used() -> None:
    body = conversation(["b", "a"])
    raw = as_json(body)
    new_raw, new_body = _reordered_request(raw, body)
    assert json.loads(new_raw) == new_body
    assert result_ids(new_body) == ["a", "b"]


def test_the_runner_gate_bounds_the_bytes_it_forwards() -> None:
    # L3: the size cap holds on the forwarded bytes, which a reorder may have re-serialized.
    from northwind_llm_replay import runner

    gate = runner.RunnerGate("nwfake-" + "k" * 40, None, print, print)  # type: ignore[arg-type]
    body = conversation(["a", "b"])
    raw = b" " * (runner.MAX_BODY_BYTES + 1)
    refused = gate.refuse("POST", "/v1/messages", "", raw, body, dict(OK_HEADERS))
    assert refused is not None and refused[0] == "refused-body"
    assert refused[1].status_code == 413
