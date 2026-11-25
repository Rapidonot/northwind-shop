"""Row #30, security M-4 (the user's approval, 2026-11-25): "none left" and "no stock left" are
stock claims, vouched by this turn's `check_stock` as "out of stock" is. Failing first."""

from __future__ import annotations

import re
from re import _parser  # type: ignore[attr-defined]

import pytest

from northwind_assistant.output_check import (
    FIXED_SENTENCES,
    PATTERNS,
    L,
    LiveValues,
    OutputCheck,
    check_text,
    hits,
    live_backed,
)

pytestmark = pytest.mark.req("RAG-09")

STOCK = FIXED_SENTENCES["stock"]
CLAIMS = [
    "There are none left in size M.",
    "None left, sorry.",
    "There is no stock left.",
    "There's **no stock left** in Navy.",
    "We have none  left.",
]


def stock(**units: int) -> LiveValues:
    live = LiveValues()
    live.stock.update(units)
    return live


@pytest.mark.parametrize("text", CLAIMS)
def test_it_is_a_stock_claim_with_nothing_checked(text: str) -> None:
    assert hits(text) == {"stock": 1}
    assert check_text(text) == STOCK


@pytest.mark.parametrize("text", CLAIMS)
def test_it_is_vouched_only_when_every_checked_variant_is_at_zero(text: str) -> None:
    assert hits(text, live=stock(a=0, b=0)) == {}
    assert hits(text, live=stock(a=1)) == {"stock": 1}  # "none" is not read as "one"
    assert hits(text, live=stock(a=0, b=3)) == {"stock": 1}
    assert live_backed(text, stock(a=0)) == 1


@pytest.mark.parametrize("text", CLAIMS)
def test_it_never_stays_on_screen_however_the_deltas_fall(text: str) -> None:
    for cut in range(len(text) + 1):
        check = OutputCheck(set(), [])
        check.feed(text[:cut])
        check.feed(text[cut:])
        check.finish()
        assert check.shown == STOCK, cut


@pytest.mark.parametrize("text", ["There are none left in size M.", "None left, sorry."])
def test_none_is_held_until_the_phrase_is_decided(text: str) -> None:
    check = OutputCheck(set(), [])
    deltas = re.findall(r"\s+|\S+", text)  # the space alone: a trailing word is held without this
    sent = [piece for delta in deltas for piece in check.feed(delta)] + check.finish()
    assert not any(piece and "none" in piece.lower() for piece in sent)


@pytest.mark.parametrize(
    "text",
    ["None of these is waterproof.", "Turn left at the hem.", "It has no left pocket."],
)
def test_other_uses_of_the_words_pass(text: str) -> None:
    assert hits(text) == {}


def test_every_pattern_still_fits_the_hold_window() -> None:
    for pattern in PATTERNS:
        assert _parser.parse(pattern.regex.pattern).getwidth()[1] <= L, pattern.regex.pattern
