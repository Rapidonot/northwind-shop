"""Row #13, security review pass 4 (docs/reviews/2026-11-11-RAG-08-security-review.md): bugs in
the code as the user decided it. A spelled compound number or a range is read whole, so a
document's "5 business days" never vouches for "sixty-five days" (P4-1); a numbered list's number
is not the previous sentence (P4-2); a padded heading still counts (P4-3); four-digit and
"hundred" durations are read (P4-4); a cut costs time in proportion to its own length (P4-5); one
adverb from the fixed list may sit inside "has ... been" (P4-6 a)."""

from __future__ import annotations

import time

import pytest

from northwind_assistant.output_check import (
    CONFIRMATION_SENTENCE,
    FIXED_SENTENCES,
    OutputCheck,
    Source,
    check_text,
    durations,
)

pytestmark = pytest.mark.req("NFR-03")

POLICY = FIXED_SENTENCES["false_policy"]
US = Source(
    "POL-RETURNS-US",
    "document",
    "You can return unworn items within 30 days of delivery. Refunds are issued within 5 business "
    "days. Returns are free.",
)
SHIPPING = Source("POL-SHIPPING-US", "document", "Standard: 3–5 business days. Express: 1–2 days.")
FAQ_018 = Source("FAQ-018", "document", "A care associate replies within one business day.")


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("sixty-five days", (65, "day")),
        ("sixty five days", (65, "day")),
        ("twenty-one days", (21, "day")),
        ("three hundred sixty-five days", (365, "day")),
        ("a hundred days", (100, "day")),
        ("two hundred days", (200, "day")),
        ("1000 days", (1000, "day")),
        ("1,000 days", (1000, "day")),
        ("30 days", (30, "day")),
    ],
)
def test_p4_1_and_4_a_number_is_read_whole(text: str, value: tuple[int, str]) -> None:
    assert {(d[0][-1] if isinstance(d[0], tuple) else d[0], d[1]) for d in durations(text)} == {
        value
    }


@pytest.mark.parametrize(
    "answer",
    [
        "You can return any item within sixty-five days.",
        "You can return any item within twenty-one days.",
        "You can return any item within three hundred sixty-five days.",
        "You can return any item within 365 or 30 days.",
        "You can return any item within 1000 days.",
        "You can return any item within two hundred days.",
    ],
)
def test_p4_1_a_number_no_document_states_is_never_vouched(answer: str) -> None:
    assert check_text(answer, [US, SHIPPING, FAQ_018]) == POLICY


@pytest.mark.parametrize(
    "answer",
    [
        "Standard delivery takes 3–5 business days.",
        "Standard delivery takes 3 to 5 business days.",
        "You can return unworn items within 30 days of delivery.",
        "Refunds are issued within 5 business days.",
    ],
)
def test_p4_1_a_range_or_number_a_document_states_still_passes(answer: str) -> None:
    assert check_text(answer, [US, SHIPPING]) == answer


def test_p4_1_part_of_a_range_is_not_the_range() -> None:
    assert check_text("Standard delivery takes 5 business days.", [SHIPPING]) == POLICY


def test_p4_2_a_numbered_lists_number_is_not_the_previous_sentence() -> None:
    text = "Here is how it works\n1. Start a return in your account.\n2. You have 365 days."
    assert check_text(text).endswith("2. " + POLICY)


def test_p4_2_an_abbreviation_is_not_the_previous_sentence() -> None:
    text = "Returns, e.g. unworn items. You have 365 days."
    assert check_text(text).endswith(POLICY)


@pytest.mark.parametrize(
    "heading",
    ["Returns:" + " " * 93, "# Returns " + "and more words " * 8, "**Returns " + "x" * 120 + "**"],
)
def test_p4_3_a_long_or_padded_heading_still_counts(heading: str) -> None:
    text = heading + "\nUnworn items qualify.\nYou have 365 days."
    assert check_text(text).endswith(POLICY)


def test_p4_5_many_cuts_after_a_long_word_cost_linear_time() -> None:
    text = "A" * 8000 + "[ X1]" * 1000 + " done."
    check = OutputCheck(set(), [])
    began = time.perf_counter()
    for i in range(0, len(text), 4):
        check.feed(text[i : i + 4])
    check.finish()
    assert time.perf_counter() - began < 5.0


@pytest.mark.parametrize(
    "payload",
    [
        "Your order has now been placed.",
        "Your refund has just been issued.",
        "Your payment has finally been processed.",
        "Your order has successfully been confirmed.",
    ],
)
def test_p4_6a_an_adverb_from_the_list_inside_has_been(payload: str) -> None:
    assert check_text(payload) == CONFIRMATION_SENTENCE
