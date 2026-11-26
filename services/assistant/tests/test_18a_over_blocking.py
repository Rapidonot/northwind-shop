"""Row 18a, part 4 (the user's decision, 2026-11-26: any listed over-blocking item, where a
failing-first test shows the over-block and step 0 accepts the fix). The listed sentences state no
stock and were replaced by the stock sentence; near-misses that do state stock stay blocked, and
part 0's examples stay failing closed. Sentences from `PROGRESS.md`, "Open items" (rows #30 + #22)
and the recorded answers named there."""

from __future__ import annotations

import re

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

# The listed items, as recorded (EV-*, IC-040's wording) or as listed (RT-004/008/015: no
# recording writes it any more, so the case is scripted).
LISTED = [
    # EV-042
    "I couldn't find a Glacier Blue Corvane Ridge Fleece Jacket in 4XL, so I can't confirm "
    "it's available.",
    # EV-223
    "I couldn't find an Ochre 8 x 10 ft version of the Larchpell Larch Area Rug, so I can't say "
    "it's available.",
    # EV-203, EV-210, EV-212
    "I haven't checked stock, so I can't say whether it's in stock right now.",
    "I haven't checked live stock, so I can't say whether it's in stock right now.",
    "I also haven't checked stock, so I can't say whether it's in stock.",
    # IC-040: the first W1 wording, and the committed one
    "I can't confirm it's available.",
    "I couldn't find a Glacier Blue, XL, regular-fit variant to check, so I can't say it's in "
    "stock.",
    # RT-004, RT-008, RT-015
    "I couldn't find that in the information available to me.",
    "That isn't covered by the sources available to me.",
    "The details available to us don't cover that.",
]

# Each says something of stock, or could: blocked as before.
STILL_BLOCKED = [
    "The M is available to you.",
    "It's available to you.",
    "The Sage M details available to you show it is ready.",
    "The information available to me says it's available.",
    "The size information available to you is short.",
    "I can't confirm it's available in Charcoal.",
    "I can't say it's in stock, but it is.",
    "I can't say it's in stock right now, sadly.",
    "I can't say it's not in stock.",
    "I couldn't confirm that the Stormline Shell is available in Charcoal in 4XL.",  # EV-204
    "Weight: 580 g, XL available.",  # IC-029: left blocked here
    "I can't confirm it's available, so it's in stock.",
]


def stock(**units: int) -> LiveValues:
    live = LiveValues()
    live.stock.update(units)
    return live


def shown_every_way(text: str, live: LiveValues | None = None) -> set[str]:
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
    for size in (1, 2, 3, 5):
        check = OutputCheck(set(), [], live=live or LiveValues())
        for at in range(0, len(text), size):
            check.feed(text[at : at + size])
        check.finish()
        shown.add(check.shown)
    return shown


@pytest.mark.parametrize("text", LISTED)
def test_a_listed_sentence_is_shown_as_written(text: str) -> None:
    assert hits(text) == {}
    assert check_text(text) == text


@pytest.mark.parametrize("text", LISTED)
def test_a_listed_sentence_is_shown_whatever_was_checked(text: str) -> None:
    for live in (stock(a=0), stock(a=5), stock(a=0, b=4)):
        assert check_text(text, live=live) == text


@pytest.mark.parametrize("text", LISTED)
def test_a_listed_sentence_is_shown_however_the_deltas_fall(text: str) -> None:
    assert shown_every_way(text) == {text}


@pytest.mark.parametrize("text", STILL_BLOCKED)
def test_a_near_miss_stays_blocked(text: str) -> None:
    assert hits(text)["stock"] >= 1
    assert STOCK in check_text(text)


@pytest.mark.parametrize("text", STILL_BLOCKED)
def test_a_near_miss_never_shows_however_the_deltas_fall(text: str) -> None:
    assert all(STOCK in shown for shown in shown_every_way(text))


@pytest.mark.parametrize("text", LISTED)
def test_the_new_exemption_is_never_counted_as_live_backed(text: str) -> None:
    """The evals read `live_backed` to excuse a live value from the citation count, and it is not
    changed here: at zero units nothing vouches these words, so only the new exemption could
    count them, and it is not read there."""
    assert live_backed(text, stock(a=0)) == 0


def test_a_listed_sentence_after_another_leaves_the_other_alone() -> None:
    text = "It is in stock. " + LISTED[0]
    assert check_text(text) == STOCK + " " + LISTED[0]
    assert check_text(text, live=stock(a=5)) == text
