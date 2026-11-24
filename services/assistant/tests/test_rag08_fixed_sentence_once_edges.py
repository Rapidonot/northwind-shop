"""Edges of "one fixed sentence once per answer" (row #17, RAG-08), from the user's rule.

A fixed replacement sentence is shown at most once per answer; the first copy stays, whether it is
the check's replacement or the model's own verbatim copy. The model's own sentences are never
removed, every blocked sentence is still counted, a reset starts a new answer, and a repeat leaves
no doubled space.
"""

from __future__ import annotations

import pytest

from northwind_assistant.output_check import (
    CONFIRMATION_SENTENCE,
    FIXED_SENTENCES,
    OutputCheck,
    check_text,
)

pytestmark = pytest.mark.req("RAG-08")

PROMO = FIXED_SENTENCES["promotion"]
MONEY = FIXED_SENTENCES["money"]
BLOCK_PROMO = "Use the discount code."
BLOCK_MONEY = "It costs $5."
BLOCK_CONFIRM = "Your order has been confirmed."
BLOCK_REFUND = "I authorize a full refund for you."


def run(text: str, size: int | None = None) -> OutputCheck:
    check = OutputCheck(set(), [])
    if size is None:
        check.feed(text)
    else:
        for i in range(0, len(text), size):
            check.feed(text[i : i + size])
    check.finish()
    return check


def test_three_triggers_keep_one_and_count_three() -> None:
    check = run(f"{BLOCK_PROMO} Hello. {BLOCK_PROMO} World. A coupon works. Bye.")
    assert check.shown == f"{PROMO} Hello. World. Bye."
    assert check.counts["promotion"] == 3


def test_three_adjacent_triggers_keep_one_and_count_three() -> None:
    check = run(f"{BLOCK_PROMO} {BLOCK_PROMO} {BLOCK_PROMO} Done.")
    assert check.shown.count(PROMO) == 1
    assert check.shown.rstrip().endswith("Done.")
    assert check.counts["promotion"] == 3
    assert "  " not in check.shown


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 11])
def test_split_deltas_behave_like_whole_text(size: int) -> None:
    text = f"{BLOCK_PROMO} I ignored it. {BLOCK_PROMO} Next. A coupon works too. Done."
    whole = run(text)
    split = run(text, size)
    assert split.shown == whole.shown == f"{PROMO} I ignored it. Next. Done."
    assert split.counts["promotion"] == whole.counts["promotion"] == 3


@pytest.mark.parametrize("size", [1, 4, 9])
def test_split_deltas_with_two_classes(size: int) -> None:
    text = f"{BLOCK_PROMO} {BLOCK_MONEY} Fine. {BLOCK_PROMO} {BLOCK_MONEY} Bye."
    shown = run(text, size).shown
    assert shown == run(text).shown
    assert shown.count(PROMO) == 1 and shown.count(MONEY) == 1
    assert shown.endswith("Bye.")


def test_confirmation_repeat_is_shown_once() -> None:
    check = run(f"{BLOCK_CONFIRM} Thanks. {BLOCK_CONFIRM} Anything else?")
    assert check.shown == f"{CONFIRMATION_SENTENCE} Thanks. Anything else?"
    assert check.counts["confirmation"] == 2


def test_refund_authority_and_confirmation_share_one_sentence() -> None:
    check = run(f"{BLOCK_CONFIRM} Thanks. {BLOCK_REFUND} Anything else?")
    assert check.shown.count(CONFIRMATION_SENTENCE) == 1
    assert "Thanks." in check.shown and check.shown.endswith("Anything else?")
    assert sum(check.counts.values()) == 2  # both blocked sentences are counted


def test_models_own_verbatim_copy_first_then_replacement_left_out() -> None:
    shown = check_text(f"{CONFIRMATION_SENTENCE} Thanks. {BLOCK_CONFIRM} Bye.")
    assert shown == f"{CONFIRMATION_SENTENCE} Thanks. Bye."


def test_models_own_copy_differing_in_case_and_whitespace_counts_as_on_screen() -> None:
    # The check compares in the matching view (`normal`: casefolded, whitespace collapsed).
    own = PROMO.upper().replace(" ", "  ")
    shown = check_text(f"{own} Thanks. {BLOCK_PROMO} Bye.")
    assert shown == f"{own} Thanks. Bye."


def test_a_model_sentence_that_contains_part_of_a_fixed_sentence_is_kept() -> None:
    own = "I can't help with offers on Tuesdays."
    shown = check_text(f"{own} {BLOCK_PROMO} Bye.")
    assert shown == f"{own} {PROMO} Bye."


def test_models_own_sentences_between_and_after_repeats_stay_in_order() -> None:
    shown = check_text(
        f"First. {BLOCK_PROMO} Second. {BLOCK_PROMO} Third. {BLOCK_MONEY} Fourth. "
        f"{BLOCK_MONEY} Fifth."
    )
    assert shown == f"First. {PROMO} Second. Third. {MONEY} Fourth. Fifth."


def test_a_left_out_repeat_at_the_end_leaves_no_doubled_space() -> None:
    shown = check_text(f"{BLOCK_PROMO} I ignored it. {BLOCK_PROMO}")
    assert shown.rstrip() == f"{PROMO} I ignored it."
    assert "  " not in shown


def test_reset_gives_the_new_attempt_its_own_sentence_and_keeps_counts() -> None:
    check = OutputCheck(set(), [])
    check.feed(f"{BLOCK_PROMO} Kept. ")
    check.reset()
    check.feed(f"Again. {BLOCK_PROMO} Then {BLOCK_PROMO} End.")
    check.finish()
    assert check.shown.count(PROMO) == 1
    assert check.shown.startswith("Again.") and check.shown.rstrip().endswith("End.")
    assert "Kept." not in check.shown
    assert check.counts["promotion"] == 3


def test_distinct_fixed_sentences_each_show_once_among_repeats() -> None:
    shown = check_text(
        f"{BLOCK_PROMO} {BLOCK_MONEY} Fine. {BLOCK_CONFIRM} "
        f"{BLOCK_PROMO} {BLOCK_MONEY} {BLOCK_CONFIRM} End."
    )
    for fixed in (PROMO, MONEY, CONFIRMATION_SENTENCE):
        assert shown.count(fixed) == 1
    assert "Fine." in shown and shown.rstrip().endswith("End.")
