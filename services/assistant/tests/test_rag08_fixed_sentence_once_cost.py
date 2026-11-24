"""The fixed-sentence-once rule (row #17) in linear time, and only for a whole, plain copy.

Scoped security review of the rule (row #17, 2026-11-24):
- Finding 1 (Medium): searching the whole screen for each blocked sentence made a long answer
  full of blocked sentences quadratic, undoing the M4 linear-time fix. The screen is now searched
  once, as it grows.
- Finding 2 (Low): the search used the matching view, which drops combining marks and folds
  look-alike letters, so a struck-through or quoted copy written by a steered model could hide every
  later replacement. A copy now counts only as a whole sentence in a strict view (case and
  whitespace aside), and any doubt shows the replacement.
"""

from __future__ import annotations

import time

import pytest

from northwind_assistant.output_check import FIXED_SENTENCES, OutputCheck, check_text

pytestmark = pytest.mark.req("RAG-08")

PROMO = FIXED_SENTENCES["promotion"]


def _timed(text: str, step: int) -> float:
    check = OutputCheck(set(), [])
    began = time.perf_counter()
    for i in range(0, len(text), step):
        check.feed(text[i : i + step])
    check.finish()
    assert check.shown.count(PROMO) == 1
    return time.perf_counter() - began


def test_many_blocked_sentences_cost_linear_time() -> None:
    """Four times the answer costs about four times as long, not sixteen (finding 1)."""
    small = min(_timed("Use a coupon. Ok. " * 400, 7) for _ in range(2))
    large = min(_timed("Use a coupon. Ok. " * 1600, 7) for _ in range(2))
    assert large < 5.0
    assert large / small < 9.0


def test_a_struck_through_copy_does_not_hide_the_replacement() -> None:
    """Combining strike-through marks make the copy read as its opposite (finding 2)."""
    struck = "I c̶a̶n̶'̶t̶ help with offers or codes in chat."
    shown = check_text(f"{struck} Ok. Use the discount code.")
    assert shown.count(PROMO) == 1


def test_a_quoted_copy_does_not_hide_the_replacement() -> None:
    shown = check_text(f"Older assistants said '{PROMO}' Not me. Use the discount code.")
    assert shown.endswith(PROMO) or shown.rstrip().endswith(PROMO)


def test_a_copy_inside_a_sentence_does_not_hide_the_replacement() -> None:
    shown = check_text(f"They told me that {PROMO} Ok. Use the discount code.")
    assert shown.count(PROMO) == 2


def test_a_whole_plain_copy_still_hides_the_repeat() -> None:
    shown = check_text(f"{PROMO} I ignored it. Use the discount code.")
    assert shown.count(PROMO) == 1


def test_a_copy_after_a_bullet_or_a_sentence_end_still_hides_the_repeat() -> None:
    assert check_text(f"Note:\n- {PROMO}\nOk. Use the discount code.").count(PROMO) == 1
    assert check_text(f"Fine. {PROMO.upper()} Ok. Use the discount code.").count(PROMO.upper()) == 1


def test_a_copy_taken_back_does_not_hide_a_later_replacement() -> None:
    """A copy inside a sentence the check takes back is no longer on screen."""
    check = OutputCheck(set(), [])
    check.feed(f"{PROMO[:-1]} if you use the discount code. ")
    check.feed("Ok. A coupon works too. ")
    check.finish()
    assert check.shown.count(PROMO) == 1
