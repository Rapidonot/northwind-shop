"""Row #23 (SA-06): "an explicit request for a person is honored immediately" — the deterministic
trigger, scored on a labelled set written before the matcher (the user's change 2 to the plan).

`handoff_phrases.jsonl` was drafted by a fresh subagent from a short guide, with no matcher in the
repository, and committed alone at `680b36c` (sha256 below) before the matcher was written.
- First score, the honest one (matcher at `a3b1d04`): positives 49/56 (7 missed), near-misses 1/55
  false handoffs. It missed the target.
- One fix round (in-sample, so not evidence of generalisation): positives 55/56 (1 missed:
  pos-032, "transfer this chat to a human ..."), false handoffs 0/55.
Target (the user): 0 false handoffs on near-misses, at most 2 misses on positives; a miss falls
through to the assistant as normal."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from northwind_gateway.handoff_trigger import asks_for_person

pytestmark = pytest.mark.req("SA-06")

SET = Path(__file__).with_name("handoff_phrases.jsonl")
SET_SHA256 = "8c49184e1352a0f0ad8680591f266878917f9b7f0fbb609c39f7469041273a5b"
MAX_MISSED_POSITIVES = 2


def _rows() -> list[dict[str, str]]:
    return [json.loads(line) for line in SET.read_text(encoding="utf-8").splitlines()]


def test_the_labelled_set_is_the_pinned_one() -> None:
    """Editing the set to fit the matcher would turn this into an in-sample score only."""
    assert hashlib.sha256(SET.read_bytes()).hexdigest() == SET_SHA256


def test_the_set_has_at_least_40_of_each_label() -> None:
    labels = [row["label"] for row in _rows()]
    assert labels.count("handoff") >= 40 and labels.count("no_handoff") >= 40


def test_no_near_miss_hands_off() -> None:
    false = [r["id"] for r in _rows() if r["label"] == "no_handoff" and asks_for_person(r["text"])]
    assert false == []


def test_at_most_two_positives_are_missed() -> None:
    missed = [
        r["id"] for r in _rows() if r["label"] == "handoff" and not asks_for_person(r["text"])
    ]
    assert len(missed) <= MAX_MISSED_POSITIVES, missed


@pytest.mark.parametrize("text", ["", "   ", "\u200b", "?!", "a" * 2000])
def test_empty_or_odd_messages_do_not_hand_off(text: str) -> None:
    assert asks_for_person(text) is False


# Security review M2: the matcher runs on the gateway's event loop, so a 2,000-character message
# (chat's limit) must cost linear time. Before the fix, "talk to " * 250 took about 1.2 s; after it,
# under 10 ms. The bound is generous so a slow CI machine does not flake.
WORST_CASES = [
    "talk to " * 250,
    "connect me " * 182,
    "i want " * 286,
    "speak with the a " * 118,
    "how do i " * 223,
    "no " * 667,
]


@pytest.mark.parametrize("text", WORST_CASES, ids=lambda t: t.split()[0])
def test_a_worst_case_message_is_read_in_linear_time(text: str) -> None:
    started = time.perf_counter()
    asks_for_person(text[:2000])
    assert time.perf_counter() - started < 0.25
