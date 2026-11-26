"""Row 18a, parts 0 and 4, gap tests (test-writer). Written from the row's criteria, not from the
code: a negation earlier in the same sentence leaves a stock claim unvouched whatever the live
stock; "none left" wordings are stock claims shown only at zero everywhere; part 4 lifts only two
narrow over-blocks and everything else stays blocked however the stream is split."""

from __future__ import annotations

import re

import pytest

from northwind_assistant.output_check import (
    FIXED_SENTENCES,
    LiveValues,
    OutputCheck,
    check_text,
    hits,
)

pytestmark = pytest.mark.req("RAG-09")

STOCK = FIXED_SENTENCES["stock"]


def stock(**units: int) -> LiveValues:
    live = LiveValues()
    live.stock.update(units)
    return live


def every_way(text: str, live: LiveValues | None = None) -> set[str]:
    shown: set[str] = set()
    for cut in range(len(text) + 1):
        check = OutputCheck(set(), [], live=live or LiveValues())
        check.feed(text[:cut])
        check.feed(text[cut:])
        check.finish()
        shown.add(check.shown)
    check = OutputCheck(set(), [], live=live or LiveValues())
    for piece in re.findall(r"\s+|\S+", text):
        check.feed(piece)
    check.finish()
    shown.add(check.shown)
    for size in (1, 2, 4):
        check = OutputCheck(set(), [], live=live or LiveValues())
        for at in range(0, len(text), size):
            check.feed(text[at : at + size])
        check.finish()
        shown.add(check.shown)
    return shown


# ---- part 0, M-B: a negation earlier in the sentence --------------------------------------------

LONG_ASIDE = (
    "The towel is not, as far as I can tell from the catalogue pages I looked at earlier today "
    "and the notes my colleague left on the shared board last week, currently in stock."
)
NEGATED = [
    "The towel is not, in Slate, in stock.",
    "The towel isn't, in Slate, in stock.",
    "The towel is not [Slate] in stock.",
    "The towel is not (Slate, Bath) available.",
    "The towel is not\navailable.",
    "The 2.5 m towel is not, in Slate, in stock.",
    "None of the 2.5 m towels, Bath or Hand, are available.",
    "Neither the Slate, nor the Sage, is in stock.",
]


@pytest.mark.parametrize("text", NEGATED)
@pytest.mark.parametrize("units", [{}, {"a": 0}, {"a": 5}, {"a": 0, "b": 3}])
def test_a_negated_stock_claim_is_never_vouched(text: str, units: dict[str, int]) -> None:
    live = stock(**units)
    assert hits(text, live=live) == {"stock": 1}, text
    assert STOCK in check_text(text, live=live)


@pytest.mark.parametrize("text", NEGATED)
def test_a_negated_stock_claim_is_blocked_however_the_stream_is_split(text: str) -> None:
    for live in (stock(a=5), stock(a=0)):
        assert all(STOCK in shown for shown in every_way(text, live)), text


def test_a_negation_in_an_earlier_sentence_does_not_unvouch_a_later_claim() -> None:
    text = "It is not waterproof. It is in stock."
    assert check_text(text, live=stock(a=5)) == text
    assert hits(text) == {"stock": 1}


def test_a_dot_in_a_number_does_not_end_the_sentence() -> None:
    text = "There is no 2.5 m rug, in Slate, in stock."
    assert hits(text, live=stock(a=5)) == {"stock": 1}


# ---- part 0, M-A: none-left wordings ------------------------------------------------------------

NONE_LEFT = [
    "There aren't any left.",
    "There aren't any left in size M.",
    "There are zero left.",
    "Zero left in Slate.",
    "We have no units left.",
    "No units left in M.",
    "There is no stock remaining.",
    "No stock remaining for the M.",
    "None of them left in M.",
    "There is no more stock left.",
    "No more stock left, sadly.",
]


@pytest.mark.parametrize("text", NONE_LEFT)
def test_none_left_is_a_stock_claim_when_nothing_was_checked(text: str) -> None:
    assert hits(text) == {"stock": 1}, text
    assert STOCK in check_text(text)


@pytest.mark.parametrize("text", NONE_LEFT)
def test_none_left_is_shown_only_at_zero_for_every_variant(text: str) -> None:
    assert hits(text, live=stock(a=0, b=0, c=0)) == {}, text
    assert check_text(text, live=stock(a=0, b=0)) == text
    assert hits(text, live=stock(a=0, b=0, c=1)) == {"stock": 1}
    assert hits(text, live=stock(a=7)) == {"stock": 1}


@pytest.mark.parametrize("text", NONE_LEFT)
def test_none_left_is_blocked_however_the_stream_is_split(text: str) -> None:
    assert every_way(text) == {STOCK} or all(STOCK in s for s in every_way(text)), text
    assert all(STOCK in s for s in every_way(text, stock(a=5))), text


def test_none_left_after_an_unrelated_sentence_leaves_the_first_alone() -> None:
    text = "The jacket is fleece-lined. There aren't any left."
    assert check_text(text) == "The jacket is fleece-lined. " + STOCK


@pytest.mark.parametrize(
    "text",
    [
        "No questions left in the thread.",
        "There are no comments left on the review.",
        "There are no sizes left to compare on the chart.",
    ],
)
def test_none_left_about_something_else_is_not_a_stock_claim(text: str) -> None:
    assert hits(text) == {}, text


@pytest.mark.parametrize("units", [{"a": 0}, {}])
def test_a_negation_after_a_long_aside_is_blocked_at_zero_or_unchecked(
    units: dict[str, int],
) -> None:
    assert hits(LONG_ASIDE, live=stock(**units)) == {"stock": 1}


def test_a_negation_after_a_long_aside_is_never_vouched_by_units_on_hand() -> None:
    live = stock(a=5)
    assert hits(LONG_ASIDE, live=live) == {"stock": 1}
    assert all(STOCK in shown for shown in every_way(LONG_ASIDE, live))


def test_none_of_them_are_left_is_a_stock_claim() -> None:
    assert hits("None of them are left.") == {"stock": 1}
    assert hits("None of them are left.", live=stock(a=0)) == {}


def test_none_left_on_the_reviews_stays_blocked() -> None:
    """The user's D4, 2026-11-26: blocked by row #30's M-4 pattern (`none left`, the user's approval
    of 2026-11-25), and kept so. The plan named it a near-miss that stays shown; that was wrong.
    It costs a sentence; showing it would loosen M-4."""
    assert hits("None left on the reviews page.") == {"stock": 1}


# ---- part 0: a widening only ---------------------------------------------------------------------

BEFORE_REPLACED = [
    "It is in stock.",
    "The M is available.",
    "Only 3 left in M.",
    "It isn't in stock, but the L is available.",
]


@pytest.mark.parametrize("text", BEFORE_REPLACED)
def test_what_an_unchecked_claim_was_replaced_for_is_still_replaced(text: str) -> None:
    assert hits(text) != {}
    assert STOCK in check_text(text)


def test_a_vouched_plain_statement_is_still_shown() -> None:
    assert check_text("It is not in stock.", live=stock(a=0)) == "It is not in stock."
    assert check_text("It is in stock.", live=stock(a=4)) == "It is in stock."


# ---- part 4: the two lifts, and nothing more -----------------------------------------------------

LIFTED = [
    "I can't confirm it's available.",
    "I can't confirm that it's in stock.",
    "I can't confirm they're available right now.",
    "I can't confirm they're available now.",
    "I can't say whether it's in stock.",
    "I can't say whether they're in stock right now.",
    "I can't say that it's available.",
    "I can't say it's available now.",
    "The data available to me does not cover that.",
    "That is not in the details available to us.",
    "The information available to you is limited.",
    "I could not find it in the sources available to me.",
]

BLOCKED = [
    "I can't confirm it's available in Charcoal.",
    "I can't confirm it's available right now, but it is.",
    "I can't confirm it's available right now in M.",
    "I can't confirm it's in stock, so grab one.",
    "I can't say whether it's in stock right now, though I think so.",
    "I can't confirm it's not available.",
    "I can't confirm the M is available.",
    "I can't confirm it's available. It's in stock.",
    "The M is available to me.",
    "The M information available to me shows it.",
    "The size M information available to me.",
    "The information for TS-0042 available to us says more.",
    "The M is available to you in Sage.",
    "Sizes available to you: M, L.",
    "It is available to you.",
    "In stock and available to me.",
    "Stock available to you.",
]


@pytest.mark.parametrize("text", LIFTED)
def test_a_lifted_sentence_is_shown_in_every_split(text: str) -> None:
    assert hits(text) == {}, text
    assert every_way(text) == {text}, text


@pytest.mark.parametrize("text", BLOCKED)
def test_everything_else_stays_blocked_in_every_split(text: str) -> None:
    assert hits(text).get("stock", 0) >= 1, text
    assert all(STOCK in shown for shown in every_way(text)), text


def test_a_lift_does_not_cover_a_claim_in_the_next_sentence() -> None:
    text = "I can't confirm it's available. The M is in stock."
    assert check_text(text) == "I can't confirm it's available. " + STOCK
    assert check_text(text, live=stock(a=3)) == text
