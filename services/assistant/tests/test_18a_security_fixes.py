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
        "There's nothing left to add.",
        "Nothing left me cold on the hike.",
        "The colours are all good.",
        "You won't run out of warmth.",
    ],
)
def test_other_uses_of_the_words_pass(text: str) -> None:
    assert hits(text) == {}


# ---- held until decided, not sent and taken back -----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Zero left.",
        "No units left.",
        "No stock remaining.",
        "There's nothing left in size M.",
        "There are hardly any left.",
        "They're all gone.",
        "We've run out of the M.",
    ],
)
def test_a_none_left_wording_is_held_until_it_is_decided(text: str) -> None:
    first = {"Zero": "zero", "No": "no", "There's": "nothing", "There": "hardly"}.get(
        text.split()[0], text.split()[-1].strip(".").lower()
    )
    check = OutputCheck(set(), [])
    pieces = [p for d in re.findall(r"\s+|\S+", text) for p in check.feed(d)] + check.finish()
    sent = [p for p in pieces if p]
    assert not any(re.search(rf"\b{first}\b", p, re.IGNORECASE) for p in sent), sent
    assert check.shown == STOCK
