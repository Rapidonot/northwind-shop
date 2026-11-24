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

from typing import Any

import pytest

from northwind_assistant import output_check
from northwind_assistant.output_check import FIXED_SENTENCES, OutputCheck, check_text

pytestmark = pytest.mark.req("RAG-08")

PROMO = FIXED_SENTENCES["promotion"]


def _feed(text: str, step: int = 7) -> OutputCheck:
    check = OutputCheck(set(), [])
    for i in range(0, len(text), step):
        check.feed(text[i : i + step])
    check.finish()
    return check


class _CountingPattern:
    """Wraps a compiled pattern and adds up how many characters each search starts from."""

    read = 0
    searches = 0

    def __init__(self, pattern: Any) -> None:
        self._pattern = pattern

    def finditer(self, text: str, pos: int = 0) -> Any:
        _CountingPattern.read += len(text) - pos
        _CountingPattern.searches += 1
        return self._pattern.finditer(text, pos)


def test_many_blocked_sentences_scan_the_screen_a_bounded_number_of_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each search reads only what the screen gained since the last one, plus a fixed overlap, so
    the characters read grow with the answer, not with blocked sentences times its length
    (finding 1). Counted, not timed (the flaky-test precedent, N-1; advisor re-read N3)."""
    copies = {k: _CountingPattern(v) for k, v in output_check._FIXED_COPIES.items()}
    monkeypatch.setattr(output_check, "_FIXED_COPIES", copies)
    _CountingPattern.read = _CountingPattern.searches = 0
    text = "Use a coupon. Ok. " * 1600
    check = _feed(text)
    assert check.shown.count(PROMO) == 1 and check.counts["promotion"] == 1600
    assert _CountingPattern.searches > 1000  # the search really ran, once per blocked sentence
    per_search = output_check._FIXED_OVERLAP + len("Use a coupon. Ok. ")
    assert _CountingPattern.read <= _CountingPattern.searches * per_search


class _CountingText(str):
    """A string that counts how many characters are read by index."""

    reads = 0

    def __getitem__(self, key: Any) -> Any:
        _CountingText.reads += 1
        return str.__getitem__(self, key)


def test_the_whole_sentence_test_reads_a_bounded_number_of_characters() -> None:
    """A copy after a long run of spaces is not a sentence start, and the run is not read to its
    end, so it costs the same for every later blocked sentence (re-review low 2). Counted."""
    shown = _CountingText("Use a coupon. x" + " " * 20000 + FIXED_SENTENCES["money"])
    start = len(shown) - len(FIXED_SENTENCES["money"])
    _CountingText.reads = 0
    assert not output_check._whole_sentence_at(shown, start)
    assert _CountingText.reads <= output_check._MAX_LEAD_SPACES + 2


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


# Scoped re-review (row #17, 2026-11-24), lows 1 and 2.


def test_a_struck_through_copy_right_before_does_not_hide_the_replacement() -> None:
    """Row #12's adjacent rule also needs a whole, plain copy (re-review low 1)."""
    struck = "I c̶a̶n̶'̶t̶ help with offers or codes in chat."
    assert check_text(f"{struck} Use the discount code.").count(PROMO) == 1


def test_the_adjacent_rule_still_says_a_plain_copy_once() -> None:
    assert check_text(f"Fine. {PROMO} Use the discount code.").count(PROMO) == 1
    assert check_text("Use the discount code. A coupon works too.").count(PROMO) == 1


def test_a_long_space_run_before_a_failed_copy_still_shows_each_answer_right() -> None:
    """The long-run case from re-review low 2, end to end: the money copy after the run is not a
    sentence start, the promotion sentence is said once, and every blocked sentence is counted."""
    money = FIXED_SENTENCES["money"]
    check = _feed("Use a coupon. x" + " " * 20000 + money + " " + "Use a coupon. " * 400)
    assert check.shown.count(PROMO) == 1
    assert check.counts["promotion"] == 401
