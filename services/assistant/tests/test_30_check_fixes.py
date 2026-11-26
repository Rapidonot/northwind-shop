"""Row #30: findings M2 and H3, the D8 offer tails, and the clarifying questions SA-04 needs.
Failing first (docs/reviews/2026-11-25-30-plan.md). H3 and the offer extension change `PATTERNS`
after recordings, approved by the user on 2026-11-25; both only tighten (decision 0015)."""

from __future__ import annotations

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

# The user's D5 (a), 2026-11-26: no clause restart, so a negation anywhere earlier in the sentence
# leaves the claim unvouched. These expectations no longer hold; what replaces them is pinned in
# test_18a_d5_pins.py.
D5 = pytest.mark.xfail(strict=True, reason="D5 (a): now replaced; see test_18a_d5_pins.py")

LINK = FIXED_SENTENCES["link"]
STOCK = FIXED_SENTENCES["stock"]


def stock(**units: int) -> LiveValues:
    live = LiveValues()
    live.stock.update(units)
    return live


def every_split(text: str, live: LiveValues | None = None) -> set[str]:
    shown = set()
    for cut in range(len(text) + 1):
        check = OutputCheck(set(), [], live=live or LiveValues())
        check.feed(text[:cut])
        check.feed(text[cut:])
        check.finish()
        shown.add(check.shown)
    return shown


# M2: a link word's match took its full stop, so the boundary search skipped to the next sentence.


@pytest.mark.req("RAG-08")
@pytest.mark.parametrize(
    "link",
    ["northwind-deals.example", "www.northwind-deals.example", "help@deals.example", "203.0.113.9"],
)
@pytest.mark.parametrize("end", [".", "!", "?"])
def test_a_link_at_the_end_of_its_sentence_does_not_take_the_next(link: str, end: str) -> None:
    text = f"See {link}{end} The jacket is warm."
    assert check_text(text) == f"{LINK} The jacket is warm."


@pytest.mark.req("RAG-08")
def test_a_link_sentence_is_the_same_however_the_deltas_fall() -> None:
    assert every_split("See northwind-deals.example. The jacket is warm.") == {
        f"{LINK} The jacket is warm."
    }


@pytest.mark.req("RAG-08")
def test_the_sentence_after_a_link_is_still_checked() -> None:
    shown = check_text("See northwind-deals.example. Use code STAFFALL. The jacket is warm.")
    assert "STAFFALL" not in shown
    assert "northwind-deals" not in shown
    assert shown.endswith("The jacket is warm.")


# H3: "available in <a plural range>" is the range offered only when nothing negates it.

NEGATED_RANGES = [
    "It is unavailable in sizes M and L.",
    "It is not available in sizes M and L.",
    "It isn't available in three colours.",
    "It isn’t currently available in colours Sage and Black.",
    "It is no longer available in all sizes.",
    "It was never available in several colours.",
    "It is **not** available in sizes M and L.",
    "Neither is available in sizes M and L.",
]
RANGES = [
    "It is available in sizes M and L.",
    "It is available in three colours.",
    "They are available in a range of colours.",
    pytest.param("It isn't packable, but it is available in three colours.", marks=D5),
]


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize("text", NEGATED_RANGES)
def test_a_negated_range_is_a_stock_claim(text: str) -> None:
    assert hits(text) == {"stock": 1}
    assert check_text(text) == STOCK


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize("text", RANGES)
def test_a_range_offered_still_passes(text: str) -> None:
    assert hits(text) == {}


@pytest.mark.req("RAG-09")
def test_a_negated_range_is_vouched_only_at_zero() -> None:
    for text in ("It is unavailable in sizes M and L.", "It is not available in sizes M and L."):
        assert hits(text, live=stock(a=0, b=0)) == {}
        assert hits(text, live=stock(a=0, b=3)) == {"stock": 1}
        assert hits(text, live=stock(a=3, b=3)) == {"stock": 1}


@pytest.mark.req("RAG-09")
def test_a_count_of_colours_is_not_read_as_units() -> None:
    assert hits("It isn't available in 3 colours.", live=stock(a=3)) == {"stock": 1}


@pytest.mark.req("RAG-09")
def test_a_range_offered_is_not_live_backed() -> None:
    assert live_backed("It is available in sizes M and L.", stock(a=3)) == 0


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize("text", NEGATED_RANGES)
def test_a_negated_range_never_stays_on_screen_however_the_deltas_fall(text: str) -> None:
    assert every_split(text) == {STOCK}


# The D8 tails: recorded on EV-035, EV-039, EV-034, EV-180, EV-190 and EV-100.

OFFER_TAILS = [
    "I can then give you the price and availability.",
    "I can then give you the price and stock.",
    "We could also check the stock.",
    "Tell me which rug you mean if you'd like a price or stock check.",
    "If you'd like a price or stock check, tell me the size.",
    "If you want a live price or stock check, tell me the colour (Charcoal or Slate) and the size.",
    "If you would like a stock check, say which size.",
    "If you’d like an availability check, tell me the size.",
]


@pytest.mark.req("RAG-08")
@pytest.mark.parametrize("tail", OFFER_TAILS)
def test_an_offer_tail_is_dropped(tail: str) -> None:
    assert hits(tail) == {"offer": 1}
    assert check_text(f"It is warm [P0006]. {tail}", (), {"P0006"}) == "It is warm [P0006]. "


@pytest.mark.req("RAG-08")
@pytest.mark.parametrize("tail", OFFER_TAILS)
def test_an_offer_tail_never_stays_on_screen_however_the_deltas_fall(tail: str) -> None:
    assert every_split(tail) == {""}


@pytest.mark.req("RAG-08")
def test_the_question_before_a_dropped_tail_stays() -> None:
    text = "Which colour would you like? I can then give you the price and availability."
    assert check_text(text).strip() == "Which colour would you like?"


# SA-04: a clarifying question, in the shape the prompt asks for, passes untouched. Questions built
# on the assistant's own lookup ("Which size should I check the price for?") are dropped by the
# offer rule, and stay so: the user's Q2, steer the prompt and do not loosen the check.

CLARIFYING = [
    "What size bed is it for?",
    "What's your budget?",
    "Roughly how much would you like to spend?",
    "What will you use it for, and is there a size you need?",
    "Is it for hiking or for everyday wear?",
    "Which room is the rug for?",
    "How long is your table, and how many guests will sit at it?",
    "Do you sleep warm or cold?",
    "Could you tell me your size and your budget?",
    "To suggest one, I need your chest measurement. What is it?",
    "Is a bath towel or a bath sheet the size you want?",
    # The user's addition: "if you'd like" questions the new offer pattern must pass.
    "If you'd like a warmer one, what's your budget?",
    "If you'd like one for layering, what size do you wear?",
    "If you'd like it for travel, do you need it to pack small?",
    "If you would like a rug for the hall, what size is the space?",
    "What size would suit you, and if you'd like a colour, which one?",
    "If you want a gift for your dad, what does he like to do, and what's your budget?",
    "If you’d like something quick-dry, is it for travel or for home?",
    "If you'd like a lower price range, how much do you want to spend?",
]


@pytest.mark.req("SA-04")
@pytest.mark.parametrize("text", CLARIFYING)
def test_a_clarifying_question_passes_untouched(text: str) -> None:
    assert hits(text) == {}
    assert check_text(text) == text


@pytest.mark.req("SA-04")
@pytest.mark.parametrize("text", CLARIFYING)
def test_a_clarifying_question_passes_however_the_deltas_fall(text: str) -> None:
    assert every_split(text) == {text}


@pytest.mark.req("RAG-08")
def test_every_pattern_still_fits_the_hold_window() -> None:
    for pattern in PATTERNS:
        assert _parser.parse(pattern.regex.pattern).getwidth()[1] <= L, pattern.regex.pattern
