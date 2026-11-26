"""Row 18a, security review (`docs/reviews/2026-11-26-18a-security-review.raw.md`): M1, L1, L2 and
L3, hard rule 2. Each input is the reviewer's, failing first at `94048d9`;
near-misses stay shown."""

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

# The user's D5 (a), 2026-11-26: no clause restart, so a negation anywhere earlier in the sentence
# leaves the claim unvouched. These expectations no longer hold; what replaces them is pinned in
# test_18a_d5_pins.py.
D5 = pytest.mark.xfail(strict=True, reason="D5 (a): now replaced; see test_18a_d5_pins.py")

STOCK = FIXED_SENTENCES["stock"]


def stock(**units: int) -> LiveValues:
    live = LiveValues()
    live.stock.update(units)
    return live


def word_by_word(text: str, live: LiveValues) -> str:
    check = OutputCheck(set(), [], live=live)
    for piece in re.findall(r"\s+|\S+", text):
        check.feed(piece)
    check.finish()
    return check.shown


# ---- M1: an aside between dashes -----------------------------------------------------------------

DASHED = [
    "None of the Slate towels — Bath or Hand — is in stock.",
    "The towel is not — as far as I can tell from this morning's check — in stock.",
    "None of the Slate towels – Bath or Hand – is in stock.",
    "None of the Slate towels - Bath or Hand - is in stock.",
]


def test_neither_across_a_dashed_aside_is_a_stock_claim_with_nothing_checked() -> None:
    text = "Neither colour — Sage or Black — is available in sizes M and L."
    assert hits(text) == {"stock": 1}
    assert hits(text, live=stock(a=5)) == {"stock": 1}


@pytest.mark.parametrize("text", DASHED)
def test_a_negation_before_a_dashed_aside_is_not_vouched_by_units(text: str) -> None:
    assert hits(text, live=stock(a=5)) == {"stock": 1}
    assert STOCK in word_by_word(text, stock(a=5))


@D5
@pytest.mark.parametrize(
    "text",
    [
        "It isn't waterproof — it is in stock.",
        "No hood — but it is in stock.",
        "It's not heavy - it's available in several colours.",  # row #30's pin, kept
    ],
)
def test_a_dash_that_starts_a_new_clause_still_restarts(text: str) -> None:
    assert check_text(text, live=stock(a=5)) == text


# ---- L1: an abbreviation does not end the sentence ----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "None of the towels (e.g. Bath or Hand) is in stock.",
        "None of the towels, i.e. Bath and Hand, is in stock.",
        "None of the rugs, approx. 2 m long, is in stock.",
        "None of the sets, incl. the Queen, is in stock.",
    ],
)
def test_a_negation_before_an_abbreviation_is_not_vouched_by_units(text: str) -> None:
    assert hits(text, live=stock(a=5)) == {"stock": 1}


def test_a_real_sentence_end_still_ends_the_sentence() -> None:
    text = "None of the towels is waterproof. The Bath size is in stock."
    assert check_text(text, live=stock(a=5)) == text


# ---- L2: only an attitude adverb is left out ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "It is, not yet, in stock.",
        "These are, no longer, available.",
        "It is, not currently, in stock.",
        "It is, not now, in stock.",
    ],
)
def test_a_negated_time_word_between_commas_keeps_the_negation(text: str) -> None:
    assert hits(text, live=stock(a=5)) == {"stock": 1}


@D5
def test_a_negated_attitude_adverb_is_still_left_out() -> None:
    text = "It is, not surprisingly, available in three colours."  # row #30's pin
    assert check_text(text) == text


# ---- L3: more "none left" wordings; "hardly any left" is low stock ------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "There's nothing left in size M.",
        "We've run out of the M.",
        "They're all gone.",
        "We ran out of the Sage.",
    ],
)
def test_a_none_left_wording_is_a_stock_claim(text: str) -> None:
    assert hits(text) == {"stock": 1}
    assert hits(text, live=stock(a=5)) == {"stock": 1}
    assert hits(text, live=stock(a=0)) == {}


@pytest.mark.parametrize(
    "text",
    ["There are hardly any left.", "There are barely any left.", "Scarcely any remaining."],
)
def test_hardly_any_left_is_low_stock_and_never_passes(text: str) -> None:
    for live in (None, stock(a=5), stock(a=0), stock(a=1)):
        assert hits(text, live=live) == {"stock": 1}


@pytest.mark.parametrize(
    "text",
    [
        "The colours are all good.",
        "You won't run out of warmth.",
    ],
)
def test_other_uses_of_the_words_pass(text: str) -> None:
    assert hits(text) == {}


# ---- held until decided, not sent and taken back (re-review N10: a reset counts as sent) -------

# The word that makes each one a claim. Words before it may be sent and then taken back by the
# reset that replaces the sentence ("There's ", "They're all "): they state nothing. The claim's own
# word must never be sent.
HELD = {
    "Zero left.": "zero",
    "No units left.": "units",
    "No stock remaining.": "remaining",
    "There's nothing left in size M.": "nothing",
    "There are hardly any left.": "hardly",
    "They're all gone.": "gone",
    "We've run out of the M.": "run",
    "We're out of the M.": "out",
    "Hardly any are left.": "hardly",
}


@pytest.mark.parametrize("text", list(HELD))
def test_a_none_left_wording_is_held_until_it_is_decided(text: str) -> None:
    check = OutputCheck(set(), [])
    pieces = [p for d in re.findall(r"\s+|\S+", text) for p in check.feed(d)] + check.finish()
    word = HELD[text]
    assert not any(p and re.search(rf"\b{word}\b", p, re.IGNORECASE) for p in pieces), pieces
    assert check.shown == STOCK


# ---- re-review N1: a range dash or a colon is no new clause ------------------------------------


@pytest.mark.parametrize(
    ("text", "live"),
    [
        ("None of the Bath towels in sizes S–M are in stock.", {"a": 5}),
        ("None of the jackets in UK 8–12 is available in three colours.", None),
        ("None of these sizes: M or L is in stock.", {"a": 5}),
        ("None of the Bath towels in sizes S‒M are in stock.", {"a": 5}),
    ],
)
def test_a_range_dash_or_a_colon_does_not_restart_the_clause(
    text: str, live: dict[str, int] | None
) -> None:
    assert hits(text, live=stock(**live) if live else None) == {"stock": 1}


def test_a_closing_dash_whose_opening_dash_is_out_of_reach_does_not_restart() -> None:
    aside = " as far as I can tell from the notes and the pages I was given last week," * 4
    text = "None of the Slate towels —" + aside + " — is in stock."
    assert hits(text, live=stock(a=5)) == {"stock": 1}


@D5
@pytest.mark.parametrize(
    "text",
    [
        "Nothing beats it: it is available in all sizes.",  # row #30's pin
        "It isn't packable; it is available in sizes S to XL.",  # row #30's pin
        "It isn't waterproof—it is in stock.",
    ],
)
def test_a_colon_or_dash_before_a_new_subject_still_restarts(text: str) -> None:
    assert check_text(text, live=stock(a=5)) == text


# ---- re-review N5: a full stop before a lower-case word or a digit ends no sentence ------------


@pytest.mark.parametrize(
    "text",
    ["None of the U.S. stores has the M in stock.", "None of the No. 5 towels is in stock."],
)
def test_a_full_stop_inside_the_sentence_does_not_end_it(text: str) -> None:
    assert hits(text, live=stock(a=5)) == {"stock": 1}


def test_a_full_stop_before_a_capital_still_ends_the_sentence() -> None:
    text = "Nothing in the U.S. range is waterproof. The M is in stock."
    assert check_text(text, live=stock(a=5)) == text


# ---- re-review N6 and N7: more wordings, and the stream and the whole text agree ---------------


@pytest.mark.parametrize(
    "text",
    [
        "There's nothing left to buy in size M.",
        "We haven't run out of the M.",
        "It's not gone.",
        "We're out of the M.",
        "Hardly any are left.",
        "There are hardly any of them left.",
    ],
)
def test_more_stock_wordings_are_never_shown_unchecked(text: str) -> None:
    assert hits(text) == {"stock": 1}


def test_out_of_their_packaging_is_no_stock_claim() -> None:  # step 0, a2a-05-partner
    text = "Pillows can't be returned once they're out of their packaging."
    assert hits(text) == {}


@pytest.mark.parametrize("text", ["There's nothing left to add.", "Nothing left me cold."])
def test_the_stream_and_the_whole_text_agree_on_nothing_left(text: str) -> None:
    whole = check_text(text)
    for cut in range(len(text) + 1):
        check = OutputCheck(set(), [])
        check.feed(text[:cut])
        check.feed(text[cut:])
        check.finish()
        assert check.shown == whole, cut
    assert word_by_word(text, LiveValues()) == whole
