"""Row #30 (the user's decision, 2026-11-25): `_negated` cut a clause at each comma but reused the
positions of the original string, so with two commas the "not" was chopped off and a negated stock
claim read as a positive one. On main since row #12. It failed open (hard rule 2: "…, …, is not in
stock" passed with units in stock) and blocked a true "is not in stock" (IC-032). Failing first."""

from __future__ import annotations

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
NEGATED = [
    "The towel is not in stock.",
    "The towel in Slate, is not in stock.",
    "The towel in Slate, Bath size, is not in stock.",
    "The towel in Slate, Bath size, UK, is not in stock.",
    "The towel in Slate, Bath size, isn't in stock.",
    "The towel in Slate, Bath size, is not available.",
    "The towel in Slate, Bath size, is no longer in stock.",
    "No, the Halvardine Home Birch Towel in Slate, Bath Towel size, is not in stock in the UK right"
    " now.",
]
POSITIVE = [
    "The towel in Slate, Bath size, is in stock.",
    "The towel in Slate, Bath size, UK, is available.",
]


def stock(units: int) -> LiveValues:
    live = LiveValues()
    live.stock.update({"P0154-SLT-BATHTOWEL": units})
    return live


@pytest.mark.parametrize("text", NEGATED)
def test_a_negated_claim_is_blocked_with_units_in_stock(text: str) -> None:
    assert hits(text, live=stock(5)) == {"stock": 1}
    assert check_text(text, live=stock(5)) == STOCK


@pytest.mark.parametrize("text", NEGATED)
def test_a_true_negated_claim_passes_at_zero(text: str) -> None:
    assert hits(text, live=stock(0)) == {}
    assert check_text(text, live=stock(0)) == text


@pytest.mark.parametrize("text", POSITIVE)
def test_a_positive_claim_keeps_its_polarity(text: str) -> None:
    assert hits(text, live=stock(5)) == {}
    assert hits(text, live=stock(0)) == {"stock": 1}


@pytest.mark.parametrize("text", [*NEGATED, *POSITIVE])
def test_the_verdict_is_the_same_however_the_deltas_fall(text: str) -> None:
    for units in (0, 5):
        whole = check_text(text, live=stock(units))
        for cut in range(len(text) + 1):
            check = OutputCheck(set(), [], live=stock(units))
            check.feed(text[:cut])
            check.feed(text[cut:])
            check.finish()
            assert check.shown == whole, (units, cut)


@pytest.mark.parametrize(
    "text",
    [
        "The towel in Slate, Bath size, is not available in sizes M and L.",
        "The towel in Slate, Bath size, isn't available in three colours.",
    ],
)
def test_a_negated_range_is_a_stock_claim_across_commas_too(text: str) -> None:
    assert hits(text) == {"stock": 1}
    assert hits(text, live=stock(5)) == {"stock": 1}


@D5
def test_a_negation_in_an_earlier_clause_does_not_govern() -> None:
    text = "It is not waterproof, not packable, and it is in stock."
    assert hits(text, live=stock(5)) == {}
