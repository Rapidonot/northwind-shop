"""Scrubbing and request hashing (FOUND-2, user change [2]: scrub only spike-proven paths)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from northwind_llm_replay.scrub import SCRUB_RULES, ScrubRule, match_key, request_hash, scrub

HEADERS = {"anthropic-version": "2023-06-01", "anthropic-beta": "claude-code-20250219"}


def body(user_text: str = "Reply with the word: ready", session: str = "s-1") -> dict[str, Any]:
    return {
        "model": "claude-haiku-5-5",
        "max_tokens": 1024,
        "metadata": {
            "user_id": json.dumps(
                {"device_id": f"dev-{session}", "account_uuid": "", "session_id": session}
            )
        },
        "system": [{"type": "text", "text": "You are a fixture."}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": user_text}]}],
        "stream": True,
    }


def h(
    b: dict[str, Any], headers: dict[str, str] = HEADERS, path: str = "/v1/messages?beta=true"
) -> str:
    return request_hash(match_key("POST", path, headers, scrub(b)))


@pytest.mark.req("FOUND-2")
def test_scrub_rules_are_exactly_the_spike_proven_paths() -> None:
    assert [r.path for r in SCRUB_RULES] == ["metadata.user_id"]
    assert all(r.reason for r in SCRUB_RULES)


@pytest.mark.req("FOUND-2")
def test_identical_requests_hash_equal_and_key_order_does_not_matter() -> None:
    a = body()
    b = json.loads(json.dumps(a, sort_keys=True))
    reordered = dict(reversed(list(b.items())))
    assert h(a) == h(b) == h(reordered)


@pytest.mark.req("FOUND-2")
def test_variation_at_scrubbed_path_hashes_equal() -> None:
    assert h(body(session="ed3f2876-1147")) == h(body(session="379234f0-cb33"))


@pytest.mark.req("FOUND-2")
def test_date_inside_user_message_changes_hash() -> None:
    """No free-text sweeps: a date the model is meant to read is part of its input."""
    assert h(body("Deliver by 2026-11-04?")) != h(body("Deliver by 2026-11-05?"))


@pytest.mark.req("FOUND-2")
@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.__setitem__("model", "claude-sonnet-5-5"),
        lambda b: b.__setitem__("max_tokens", 2048),
        lambda b: b["system"][0].__setitem__("text", "You are a different fixture."),
        lambda b: b["messages"][0]["content"][0].__setitem__("text", "Reply with: steady"),
        lambda b: b["metadata"].__setitem__("extra", "x"),
    ],
)
def test_any_content_change_changes_hash(mutate: Any) -> None:
    a = body()
    b = copy.deepcopy(a)
    mutate(b)
    assert h(a) != h(b)


@pytest.mark.req("FOUND-2")
def test_matched_headers_and_path_are_part_of_hash_but_other_headers_are_not() -> None:
    a = body()
    assert h(a) != h(a, {**HEADERS, "anthropic-beta": "claude-code-20250219,effort-2025-11-24"})
    assert h(a) != h(a, path="/v1/messages")
    noisy = {
        **HEADERS,
        "x-stainless-os": "MacOS",
        "user-agent": "x",
        "x-claude-code-session-id": "u",
    }
    assert h(a) == h(a, noisy)


@pytest.mark.req("FOUND-2")
def test_pattern_rule_replaces_only_matches_at_its_path() -> None:
    rule = ScrubRule(path="system[*].text", reason="test", pattern=r"\d{4}-\d{2}-\d{2}")
    out = scrub({"system": [{"text": "today 2026-11-04 ok"}], "note": "2026-11-04"}, (rule,))
    assert out["system"][0]["text"] == "today <scrubbed:system[*].text> ok"
    assert out["note"] == "2026-11-04"


@pytest.mark.req("FOUND-2")
def test_scrub_does_not_mutate_input_and_tolerates_missing_paths() -> None:
    a = body()
    before = copy.deepcopy(a)
    scrub(a)
    assert a == before
    assert scrub({"model": "m"}) == {"model": "m"}
