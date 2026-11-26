"""Row 18a, the user's D5 (a), 2026-11-26 (hard rule 2, RAG-09): no clause restart. A negation
anywhere earlier in the sentence leaves a stock claim unvouched, after three review rounds each
found a restart rule that hid one (M1, N1, R1 and R2). Measured first at $0: 0 of 448 recorded
answer calls change, and D1's five eval answers stay shown. The 24 earlier expectations this
changes are strict xfails (`D5`) where they were; their sentences are pinned here as replaced,
with the live values those tests used. Also R1's, R2's and R3's inputs, failing first at
`418614f`."""

from __future__ import annotations

import pytest

from northwind_assistant.output_check import FIXED_SENTENCES, LiveValues, check_text, hits

pytestmark = pytest.mark.req("RAG-09")

STOCK = FIXED_SENTENCES["stock"]


def stock(**units: int) -> LiveValues:
    live = LiveValues()
    live.stock.update(units)
    return live


# The earlier pins, now replaced whatever is in stock (rows #12, #30 and 18a's first rounds).
WAS_SHOWN = [
    "There is no size chart on the page, but it is available in sizes M and L.",
    "It isn't packable; it is available in sizes S to XL.",
    "Not only is it warm, it is available in three colours.",
    "It's not heavy - it's available in several colours.",
    "Nothing beats it: it is available in all sizes.",
    "It is not cheap (nor is it dear); it is available in a range of colours.",
    "It is, not surprisingly, available in three colours.",
    "I can't promise a colour, but I can tell you it is available in three colours.",
    "It isn't packable, but it is available in three colours.",
    "It is not waterproof, not packable, and it is in stock.",
    "Not sure if you saw, but the M is available.",
    "I can't see stock levels, so check the live product page to see whether the M is "
    "available now.",
    "Never mind, it is in stock now.",
    "No worries — the M is in stock.",
    "Don't wait — the M is available now.",
    "It isn't waterproof—it is in stock.",
    "It isn't waterproof — it is in stock.",
    "No hood — but it is in stock.",
]


@pytest.mark.parametrize("text", WAS_SHOWN)
def test_an_earlier_pin_is_now_replaced_whatever_is_in_stock(text: str) -> None:
    for live in (None, stock(a=5), stock(a=3, b=4), stock(a=0)):
        assert check_text(text, live=live) == STOCK, live


# The second scoped re-review's open inputs, and the gap option B would have left.
STILL_OPEN_AT_418614F = [
    "None of the sizes S, M, and L are in stock.",  # R1, Oxford comma
    "None of the Slate, Charcoal, and Moss towels are in stock.",
    "None of the Bath towels in sizes S - M are in stock.",  # R1, spaced hyphen
    "None of the towels in sizes S—M are in stock.",  # R1, bare em dash
    "None of the jackets you, he or she tried on is in stock.",  # R2
    "None of the jackets he, you or I tried on is in stock.",
    "No towel in sizes S, M, and L is in stock.",  # option B's gap
    "None of the U.S.  stores has the M in stock.",  # R3, two spaces
]


@pytest.mark.parametrize("text", STILL_OPEN_AT_418614F)
def test_a_negation_before_any_clause_mark_is_never_vouched_by_units(text: str) -> None:
    assert hits(text, live=stock(a=5)) == {"stock": 1}
    assert hits(text, live=stock(a=5, b=2)) == {"stock": 1}


def test_a_negated_range_across_a_spaced_dash_is_a_stock_claim_with_nothing_checked() -> None:
    assert hits("None of the jackets in UK 8 – 12 is available in three colours.") == {"stock": 1}


@pytest.mark.parametrize(
    "text",
    [
        "No, the Charcoal L is out of stock right now.",  # the reply word (step 0)
        "It isn't waterproof. It is available in three colours.",  # another sentence
        "None of the U.S. range is waterproof. The M is in stock.",
    ],
)
def test_what_is_still_read_as_its_own_sentence_is_shown(text: str) -> None:
    live = stock(a=0) if "out of stock" in text else stock(a=5)
    assert check_text(text, live=live) == text
