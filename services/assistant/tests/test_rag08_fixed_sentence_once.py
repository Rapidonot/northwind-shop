"""One fixed sentence once per answer (row #17, the user's item 3, after window 1).

The output check puts a fixed sentence in place of a blocked one. Row #12 (EV-090) already says it
once when it would follow itself. Window 1's recorded injection answer put the promotion sentence
twice with the model's own "I ignored it." between them, so the shopper saw:

    I can't help with offers or codes in chat. I ignored it. I can't help with offers or codes in chat.

Now a replacement whose fixed sentence is already on screen in this answer is left out, wherever
the earlier copy is. Only replacements are left out: the model's own sentences stay, and every
blocked sentence is still counted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from northwind_assistant.output_check import FIXED_SENTENCES, OutputCheck, check_text
from northwind_assistant.tools import parse_sources

pytestmark = pytest.mark.req("RAG-08")

REPO = Path(__file__).resolve().parents[3]
PROMO = FIXED_SENTENCES["promotion"]
MONEY = FIXED_SENTENCES["money"]
INJECTION_TEST = "test_a_planted_injection_in_a_review_is_not_followed"


def recorded_injection_answer() -> tuple[list[str], OutputCheck]:
    """Window 1's recorded answer, in its own deltas, and a check over its recorded sources."""
    [cassette] = [
        c
        for path in sorted((REPO / "cassettes/assistant-smoke").glob("*.json"))
        if INJECTION_TEST in (c := json.loads(path.read_text(encoding="utf-8")))["test_id"]
    ]
    deltas = re.findall(r'"type":"text_delta","text":"((?:[^"\\]|\\.)*)"', cassette["body"])
    first = cassette["match"]["body"]["messages"][0]["content"]
    prompt = first if isinstance(first, str) else "".join(b.get("text", "") for b in first)
    sources = parse_sources(prompt)
    check = OutputCheck({source.source_id for source in sources}, sources)
    return [json.loads(f'"{delta}"') for delta in deltas], check


def test_the_recorded_injection_answer_shows_the_promotion_sentence_once() -> None:
    deltas, check = recorded_injection_answer()
    assert "I ignored it." in "".join(deltas)  # the recording this test is about
    for delta in deltas:
        check.feed(delta)
    check.finish()
    assert check.shown.count(PROMO) == 1
    assert "I ignored it." in check.shown  # the model's own sentence stays
    assert "discount" not in check.shown.lower()  # both blocked sentences are still blocked
    assert check.counts["promotion"] == 2  # and both are counted
    assert "  " not in check.shown  # no doubled space where the repeat would have been


def test_a_repeat_after_a_model_sentence_is_left_out() -> None:
    shown = check_text("Use the discount code. I ignored it. A coupon works too. Anything else?")
    assert shown == f"{PROMO} I ignored it. Anything else?"


def test_a_replacement_repeating_the_models_own_copy_is_left_out() -> None:
    shown = check_text(f"{PROMO} I ignored it. Use the discount code.")
    assert shown.count(PROMO) == 1 and shown.startswith(f"{PROMO} I ignored it.")


def test_different_fixed_sentences_each_show_once() -> None:
    shown = check_text("Use the discount code. It costs $5. Use a coupon. It costs $6. Fine.")
    assert shown.count(PROMO) == 1 and shown.count(MONEY) == 1
    assert shown.rstrip().endswith("Fine.")


def test_after_a_reset_the_new_attempt_shows_its_own_fixed_sentence() -> None:
    check = OutputCheck(set(), [])
    check.feed("Use the discount code. ")
    check.reset()
    check.feed("A coupon works too. Done.")
    check.finish()
    assert check.shown.count(PROMO) == 1  # the screen was reset, so this answer needs its own
