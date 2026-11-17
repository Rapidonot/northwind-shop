"""Row #12, the user's re-read of 72285d7..5f74e3f (2026-11-14): the H3 fix's copula lookbehinds
missed "available" after any adverb or emphasis (high, hard rule 2). Every sentence the finding
gave, as tests, plus the plain view the stock and money patterns now run on (RAG-09)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from northwind_assistant.output_check import (
    FIXED_SENTENCES,
    LiveValues,
    OutputCheck,
    check_text,
    hits,
    live_backed,
)

pytestmark = pytest.mark.req("RAG-09")

STOCK = FIXED_SENTENCES["stock"]
MONEY = FIXED_SENTENCES["money"]

ALL_IN = LiveValues(stock={"P0035-SAGE-M": 4, "P0035-SAGE-3XL": 2})
ALL_OUT = LiveValues(stock={"P0035-SAGE-M": 0, "P0035-SAGE-3XL": 0})
MIXED = LiveValues(stock={"P0035-SAGE-M": 3, "P0035-SAGE-3XL": 0})

# The finding's five sentences, verbatim.
FINDING = [
    "The M is now available.",
    "The Sage 3XL is also available.",
    "Both sizes are all available.",
    "It's readily available in the US.",
    "The M is **available** today.",
]


@pytest.mark.parametrize("text", FINDING)
def test_the_findings_sentences_are_blocked_with_no_live_values(text: str) -> None:
    assert hits(text) == {"stock": 1}
    assert check_text(text) == STOCK


@pytest.mark.parametrize("text", FINDING)
def test_the_findings_sentences_pass_when_every_stock_check_agrees(text: str) -> None:
    assert hits(text, live=ALL_IN) == {}
    assert check_text(text, live=ALL_IN) == text


@pytest.mark.parametrize("text", FINDING)
@pytest.mark.parametrize("live", [ALL_OUT, MIXED], ids=["all-out", "mixed"])
def test_the_findings_sentences_are_blocked_when_any_check_disagrees(
    text: str, live: LiveValues
) -> None:
    assert hits(text, live=live) == {"stock": 1}


@pytest.mark.parametrize(
    "text",
    [
        "Yes, it's widely available.",
        "The M is, happily, available.",
        "The jacket — in Sage — is available.",
        "Available: the M.",
        "AVAILABLE in M.",
        "It is avail**able** now.",  # emphasis inside the word
        "It is `available`.",  # a code marker
        "It is _available_ in the US.",
    ],
)
def test_any_available_is_a_stock_claim_whatever_precedes_it(text: str) -> None:
    assert hits(text) == {"stock": 1}
    assert hits(text, live=ALL_IN) == {}


def test_a_struck_negation_is_never_read_as_one() -> None:
    """Strikethrough stays in the view: "~~not~~" shows as struck, so the sentence reads
    "available", and must not pass as a negative claim when every check is at zero."""
    for live in (None, ALL_IN, ALL_OUT, MIXED):
        assert hits("It is ~~not~~ available.", live=live) == {"stock": 1}


@pytest.mark.parametrize(
    "text",
    ["The M is unavailable.", "The 3XL is currently **un**available.", "Sadly unavailable in M."],
)
def test_unavailable_needs_every_check_at_zero(text: str) -> None:
    assert hits(text) == {"stock": 1}
    assert hits(text, live=ALL_OUT) == {}
    assert hits(text, live=ALL_IN) == {"stock": 1}
    assert hits(text, live=MIXED) == {"stock": 1}


def test_a_negated_unavailable_is_a_positive_claim() -> None:
    text = "The M isn't unavailable."
    assert hits(text, live=ALL_IN) == {}
    assert hits(text, live=ALL_OUT) == {"stock": 1}


def test_a_plural_range_with_no_time_word_is_still_the_range_offered() -> None:
    for text in ("The jacket is available in sizes XS-XL.", "It comes available in three colours."):
        assert hits(text) == {}
    # A time word on either side makes it a stock claim.
    for text in (
        "The jacket is available in sizes M and L right now.",
        "The jacket is now available in sizes M and L.",
    ):
        assert hits(text) == {"stock": 1}


def test_a_count_before_available_is_read_as_a_count() -> None:
    """D3: a count passes when it equals one variant's units, even with another variant at 0."""
    assert hits("Only 3 available.", live=MIXED) == {}
    assert hits("Only **3** available.", live=MIXED) == {}
    assert hits("Only 5 available.", live=MIXED) == {"stock": 1}


def test_the_information_sentence_is_blocked_in_every_stock_state() -> None:
    """Measured, as the re-read asked: "information available" is not stock, but the clause's other
    negation makes any stock claim in it unvouched, so it is blocked even when stock agrees. An
    exemption for a non-stock sense needs the user's approval (none is added)."""
    text = "I don't have that information available."
    for live in (None, ALL_IN, ALL_OUT, MIXED):
        assert hits(text, live=live) == {"stock": 1}


def test_the_raw_spans_map_back_through_removed_markers() -> None:
    text = "Great jacket. The M is **available** today. Ask me more."
    assert check_text(text) == f"Great jacket. {STOCK} Ask me more."


def test_a_split_marked_word_is_held_until_it_is_whole() -> None:
    check = OutputCheck(set(), [])
    shown = []
    for delta in ("The M is a**va", "il**able", " today. Ask me more."):
        shown += [p for p in check.feed(delta) if p]
    shown += [p for p in check.finish() if p]
    assert "avail" not in "".join(shown).replace(STOCK, "")
    assert check.shown == f"{STOCK} Ask me more."


# ---- money on the plain view --------------------------------------------------------------------

PRICE = LiveValues(prices={(Decimal("279.00"), "USD")})


@pytest.mark.parametrize("text", ["It costs $2**7**9.00.", "It is `$279`.", "It is 2_79 USD."])
def test_markers_inside_an_amount_do_not_hide_it(text: str) -> None:
    assert hits(text) == {"money": 1}
    assert hits(text, live=PRICE) == {}


def test_a_marked_amount_split_across_deltas_is_read_whole() -> None:
    """ "$2**" then "79**.00": the match ends before markers at a delta's end, so it is held."""
    check = OutputCheck(set(), [], live=LiveValues(prices={(Decimal("2.00"), "USD")}))
    for delta in ("It is $2**", "79**.00 today."):
        check.feed(delta)
    check.finish()
    assert check.shown == MONEY


def test_live_backed_reads_the_plain_view() -> None:
    assert live_backed("The M is **available** today.", ALL_IN) == 1
    assert live_backed("It costs $2**7**9.00.", PRICE) == 1


# ---- hyphenated stock phrases (the user, 2026-11-14) ---------------------------------------------

POSITIVE_HYPHENATED = ["The M is in-stock.", "The M is in - stock.", "The M is in‑stock."]
NEGATIVE_HYPHENATED = ["The M is out-of-stock.", "The 3XL is sold-out.", "The M is out-of stock."]


@pytest.mark.parametrize("text", POSITIVE_HYPHENATED + NEGATIVE_HYPHENATED)
def test_a_hyphenated_stock_phrase_is_blocked_with_no_live_values(text: str) -> None:
    assert hits(text) == {"stock": 1}
    assert check_text(text) == STOCK


@pytest.mark.parametrize("text", POSITIVE_HYPHENATED)
def test_in_stock_with_hyphens_needs_every_check_above_zero(text: str) -> None:
    assert hits(text, live=ALL_IN) == {}
    assert hits(text, live=ALL_OUT) == {"stock": 1}
    assert hits(text, live=MIXED) == {"stock": 1}


@pytest.mark.parametrize("text", NEGATIVE_HYPHENATED)
def test_out_of_stock_and_sold_out_with_hyphens_need_every_check_at_zero(text: str) -> None:
    assert hits(text, live=ALL_OUT) == {}
    assert hits(text, live=ALL_IN) == {"stock": 1}
    assert hits(text, live=MIXED) == {"stock": 1}


def test_a_negated_hyphenated_phrase_flips_its_polarity() -> None:
    assert hits("The M isn't in-stock.", live=ALL_OUT) == {}
    assert hits("The M isn't in-stock.", live=ALL_IN) == {"stock": 1}


def test_a_hyphenated_count_phrase_is_read_as_a_count() -> None:
    assert hits("Only 3 units in-stock.", live=LiveValues(stock={"a": 3})) == {}
    assert hits("Only 5 units in-stock.", live=LiveValues(stock={"a": 3})) == {"stock": 1}
    assert hits("Plenty in-stock.", live=ALL_IN) == {"stock": 1}  # a quantity no tool returns


def test_a_hyphenated_time_word_ends_the_range_exception() -> None:
    assert hits("The jacket is available in sizes M and L, all in-stock.") == {"stock": 1}
