"""Row #18, after `ship`'s e2e failure (the user's stop-8 yes, 2026-11-28): a replay miss never
blocks the proxy.

On a miss the proxy reported a diff against the nearest cassette, chosen with
`SequenceMatcher(autojunk=False).ratio()` over every candidate, inline in its async handler. A Care
request with no cassette of its own test was compared with all of `assistant-smoke` (18 cassettes
of 16-20 KB): 18-24 s of CPU, during which a recorded chat request that is served in 0.0 s alone
took 23.0 s. In CI's e2e job two handoff journeys' Care misses held the second suggestion's chat
turn past its 90 s timeout.

The fix: the diff runs off the event loop, and its cost is bounded. The nearest cassette is chosen
by `real_quick_ratio` (lengths only) and then `quick_ratio` (a multiset count), only the nearest is
diffed, and above `MAX_DIFF_CHARS` the diff is skipped. Hashing and matching are unchanged.
"""

from __future__ import annotations

import difflib
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from northwind_llm_replay import cassette as cassette_module
from northwind_llm_replay.cassette import Cassette, CassetteStore
from northwind_llm_replay.proxy import ProxyConfig, RunningProxy
from northwind_llm_replay.scrub import match_key, request_hash

pytestmark = pytest.mark.req("FOUND-2")

MODEL = "claude-sonnet-5-5"
PATH = "/v1/messages?beta=true"
HEADERS = {"anthropic-version": "2023-06-01"}
KEY = "northwind-proxy-placeholder"


def body_of(text: str) -> dict[str, Any]:
    return {"model": MODEL, "max_tokens": 64, "messages": [{"role": "user", "content": text}]}


def cassette_for(hash_char: str | None, text: str, test_id: str | None = None) -> Cassette:
    """`hash_char=None`: stored under the hash the proxy computes for this request, so it hits."""
    match = match_key("POST", PATH, HEADERS, body_of(text))
    return Cassette(
        hash=request_hash(match) if hash_char is None else hash_char * 64,
        suite="s",
        recorded_at="2026-11-28T00:00:00Z",
        match=match,
        status=200,
        content_type="application/json",
        kind="json",
        body='{"content": [{"type": "text", "text": "ok"}]}',
        model=MODEL,
        usage=None,
        cost_usd=0.0,
        test_id=test_id,
        seq=0,
    )


def post(url: str, text: str) -> tuple[float, int]:
    import json

    start = time.monotonic()
    reply = httpx.post(
        f"{url}{PATH}",
        content=json.dumps(body_of(text)).encode(),
        headers={"x-api-key": KEY, "content-type": "application/json", **HEADERS},
        timeout=30,
    )
    return time.monotonic() - start, reply.status_code


def test_a_hit_is_served_while_a_slow_miss_is_being_diffed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user's bound: a hit sent while a miss is being diffed returns in under 1 s. The diff is
    held for 3 s by a slow stand-in, so a diff run on the event loop would hold the hit too."""
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset())
    hit_text = "What sizes does P0001 come in?"
    store.put(cassette_for(None, hit_text))
    started = threading.Event()

    def slow_diff(self: CassetteStore, match: dict[str, Any], *args: Any, **kw: Any) -> str:
        started.set()
        time.sleep(3.0)
        return "(slow)"

    monkeypatch.setattr(CassetteStore, "miss_diff", slow_diff)
    cfg = ProxyConfig(mode="replay", suite="s", cassette_root=tmp_path / "cassettes")
    with RunningProxy(cfg) as running:
        # The hit really is a hit before the race starts.
        alone, status = post(running.url, hit_text)
        assert status == 200, "the recorded request must hit"
        results: dict[str, tuple[float, int]] = {}
        miss = threading.Thread(
            target=lambda: results.__setitem__("miss", post(running.url, "something else"))
        )
        miss.start()
        assert started.wait(10), "the miss never reached its diff"
        results["hit"] = post(running.url, hit_text)
        miss.join()
    took, status = results["hit"]
    assert status == 200
    assert took < 1.0, f"the hit waited {took:.2f} s behind the miss's diff"
    assert results["miss"][1] == 400


def big_text(seed: int, size: int) -> str:
    words = [f"w{(seed * 7919 + i * 104729) % 9973}" for i in range(size // 6)]
    return " ".join(words)


def test_a_diff_above_the_cap_is_skipped_and_never_computed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Above `MAX_DIFF_CHARS` (the two canonical forms together) only the header is reported:
    the line diff is never run. The cap bounds the work; a thread alone does not, under the GIL."""
    cap = cassette_module.MAX_DIFF_CHARS
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset())
    store.put(cassette_for("2", big_text(1, cap)))

    def no_diff(*args: Any, **kw: Any) -> Any:
        raise AssertionError("unified_diff ran above the cap")

    monkeypatch.setattr(difflib, "unified_diff", no_diff)
    match = match_key("POST", PATH, HEADERS, body_of(big_text(2, cap)))
    diff = store.miss_diff(match)
    assert diff.splitlines()[0] == f"--- cassette {'2' * 12}"
    assert "diff skipped (too large" in diff


def test_below_the_cap_the_diff_is_still_reported(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset())
    store.put(cassette_for("3", "What sizes does P0001 come in?"))
    diff = store.miss_diff(
        match_key("POST", PATH, HEADERS, body_of("What colours does P0001 come in?"))
    )
    assert "diff skipped" not in diff
    assert any(line.startswith("-") and "What sizes" in line for line in diff.splitlines())
    assert any(line.startswith("+") and "What colours" in line for line in diff.splitlines())


def test_the_nearest_cassette_is_chosen_without_a_full_sequence_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Choosing the nearest cassette never runs `ratio()` or `get_matching_blocks()`, the quadratic
    part that took 18-24 s; only `real_quick_ratio` and `quick_ratio` are allowed."""
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset())
    store.put(cassette_for("4", "What sizes does P0001 come in, in blue? Thanks"))
    store.put(cassette_for("5", "Completely different: the returns policy for March orders"))

    def quadratic(*args: Any, **kw: Any) -> Any:
        raise AssertionError("a full SequenceMatcher match ran while choosing the nearest")

    from northwind_llm_replay.scrub import canonical

    want = match_key("POST", PATH, HEADERS, body_of("What sizes does P0001 come in, in blue?"))
    with monkeypatch.context() as patched:
        patched.setattr(difflib.SequenceMatcher, "ratio", quadratic)
        patched.setattr(difflib.SequenceMatcher, "get_matching_blocks", quadratic)
        nearest = cassette_module._nearest(store.all(), canonical(want))
    assert nearest.hash == "4" * 64
    assert store.miss_diff(want).splitlines()[0] == f"--- cassette {'4' * 12}"


def test_choosing_the_nearest_of_many_candidates_reads_few_of_them_in_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A suite like `backend-replay` (5,959 cassettes): `quick_ratio`, linear in the text, runs
    on at most `NEAREST_SHORTLIST` candidates, picked by length alone."""
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset())
    for i in range(60):
        store.put(cassette_for(None, f"question {i} " * (i + 1)))
    calls = {"quick": 0}
    real = difflib.SequenceMatcher.quick_ratio

    def counted(self: difflib.SequenceMatcher[str]) -> float:
        calls["quick"] += 1
        return real(self)

    monkeypatch.setattr(difflib.SequenceMatcher, "quick_ratio", counted)
    store.miss_diff(match_key("POST", PATH, HEADERS, body_of("question 7 " * 8)))
    assert 0 < calls["quick"] <= cassette_module.NEAREST_SHORTLIST


def test_a_diff_with_too_many_lines_is_skipped_however_few_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security L1: the line diff's cost follows lines, and `pretty()` puts one scalar per line, so
    a short but deeply split body is capped by `MAX_DIFF_LINES` too."""
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset())
    many = cassette_module.MAX_DIFF_LINES  # one line per list item in `pretty()`
    recorded = match_key("POST", PATH, HEADERS, {**body_of("x"), "stop_sequences": [0] * many})
    store.put(
        Cassette(
            hash="6" * 64,
            suite="s",
            recorded_at="2026-11-28T00:00:00Z",
            match=recorded,
            status=200,
            content_type="application/json",
            kind="json",
            body="{}",
            model=MODEL,
            usage=None,
            cost_usd=0.0,
            test_id=None,
            seq=0,
        )
    )

    def no_diff(*args: Any, **kw: Any) -> Any:
        raise AssertionError("unified_diff ran above the line cap")

    monkeypatch.setattr(difflib, "unified_diff", no_diff)
    request = match_key("POST", PATH, HEADERS, {**body_of("y"), "stop_sequences": [1] * many})
    from northwind_llm_replay.scrub import canonical

    assert len(canonical(recorded)) + len(canonical(request)) < cassette_module.MAX_DIFF_CHARS
    diff = store.miss_diff(request)
    assert diff.splitlines()[0] == f"--- cassette {'6' * 12}"
    assert "lines, cap" in diff


def test_misses_are_counted_in_arrival_order_whatever_their_diffs_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security L2: a miss is counted before its diff is built, so `state.misses[0]` (the diff the
    pytest plugin prints) is the first miss to arrive, even when its diff finishes last."""
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset())
    store.put(cassette_for(None, "recorded"))
    first_started = threading.Event()

    def diff_slow_for_the_first(
        self: CassetteStore, match: dict[str, Any], *args: Any, **kw: Any
    ) -> str:
        if "first miss" in str(match):
            first_started.set()
            time.sleep(1.5)
            return "(first)"
        return "(second)"

    monkeypatch.setattr(CassetteStore, "miss_diff", diff_slow_for_the_first)
    cfg = ProxyConfig(mode="replay", suite="s", cassette_root=tmp_path / "cassettes")
    with RunningProxy(cfg) as running:
        first = threading.Thread(target=lambda: post(running.url, "first miss"))
        first.start()
        assert first_started.wait(10)
        post(running.url, "second miss")
        first.join()
        misses = running.proxy.state.misses
    assert [m["diff"] for m in misses] == ["(first)", "(second)"]
