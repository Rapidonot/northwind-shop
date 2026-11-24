"""RAG-08 (row #16, Addendum 8): the offer-drop rule. A sentence in which the assistant offers to
look up, check or give a price, stock or availability is dropped with nothing in its place.
Failing first: written from docs/reviews/2026-11-17-16-plan.md, "Addendum 8" and "update 1",
before the rule exists.

Synthetic text only. Decisions held here: "will" and "'ll" are not offer frames; the verbs are look
up, check and give (also "give you"); the nouns are price(s), stock and availability, within 26
characters after the verb (the width test's cap under `L`), in the same sentence."""

from __future__ import annotations

from decimal import Decimal
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
)

pytestmark = pytest.mark.req("RAG-08")

SEEN = ["P0006"]
BEFORE = "It comes in a relaxed fit [P0006]."
AFTER = "It is machine washable [P0006]."

USER_EXAMPLES = [
    "I can look up the live price of the Halvardine Home Wren Bath Towel.",
    "I can give you its live price.",
    "Want me to check stock?",
]

W5_SENTENCES = [
    "I can check price and stock once you tell me which.",
    "If so, tell me your size and I can give you the price and stock.",
    "I can check live stock and price for the Sand Twin if you'd like.",
    "I can check the live price of the Tidewick Marsh Overshirt for you if you tell me the colour, "
    "size and fit you want.",
    "Tell me which rug you like and I can look up its price and stock.",
    "If you tell me the size you want, I can look up the live price for you.",
    "Do you want me to look up the current live price for the Wren Bath Towel?",
    "Tell me which one you like and which size you want, and I can check the price and stock.",
    "If you tell me which rug, size and colour you want, I can give you the price.",
    "Then I can give you the live price and stock, or add it to your cart.",
    "Tell me which one you like and I can check the price and stock.",
    "If you tell me the colour and size, I can check the live price and stock.",
    "If you tell me the size, I can check the price and stock.",
    "I can check the price and stock for either.",
    "Tell me which one you like, plus your colour and size, and I can look up the price and stock.",
    "I can check today's price, but the jacket comes in Black and Cobalt and in sizes S to 2XL "
    "[P0006].",
    "If you'd like, tell me the colour and size you want and I can check live stock and price.",
    "Tell me which one you like, plus the colour and size, and I can check price and stock.",
    "Tell me which colour you want and I can check the price and stock for you.",
]

OFFERS = [*USER_EXAMPLES, *W5_SENTENCES]

KEPT = [
    "I'll check the price.",
    "I will check the price.",
    "I checked the live price for you.",
    "I looked up the stock.",
    FIXED_SENTENCES["money"],
    FIXED_SENTENCES["stock"],
    "Would you like me to add it to your cart?",
    "Tell me if you'd like me to check that one, or another colour and size.",
    "I can tell you the live price.",
    "Want me to look up what the Halvardine Home Wren Bath Towel costs today?",
    "Let me check the price.",
]

LIVE_40 = LiveValues(prices={(Decimal("40.00"), "USD")})


def screen(events: list[str | None], screen_so_far: str = "") -> str:
    """What the shopper's screen holds after these events: `None` clears it."""
    for event in events:
        screen_so_far = "" if event is None else screen_so_far + event
    return screen_so_far


def run(pieces: list[str], live: LiveValues | None = None) -> tuple[list[str | None], OutputCheck]:
    check = OutputCheck(set(SEEN), [], live=live or LiveValues())
    events: list[str | None] = []
    for piece in pieces:
        events += check.feed(piece)
    events += check.finish()
    return events, check


def chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


# ---- dropped -----------------------------------------------------------------------------------


@pytest.mark.parametrize("sentence", OFFERS)
def test_an_offer_sentence_is_counted_and_dropped_at_the_end(sentence: str) -> None:
    text = f"{BEFORE} {sentence}"
    assert hits(text, seen=SEEN) == {"offer": 1}
    assert check_text(text, seen=SEEN).rstrip() == BEFORE


@pytest.mark.parametrize("sentence", OFFERS)
def test_an_offer_sentence_is_dropped_from_the_middle_with_no_doubled_whitespace(
    sentence: str,
) -> None:
    text = f"{BEFORE} {sentence} {AFTER}"
    assert hits(text, seen=SEEN) == {"offer": 1}
    assert check_text(text, seen=SEEN) == f"{BEFORE} {AFTER}"


def test_an_offer_sentence_alone_leaves_nothing() -> None:
    assert check_text("Want me to check stock?").strip() == ""


# ---- kept --------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", KEPT)
def test_these_sentences_are_not_offers_and_stay_unchanged(text: str) -> None:
    assert check_text(text) == text
    assert "offer" not in hits(text)


def test_a_reported_live_price_is_not_an_offer() -> None:
    text = "The live price is 40 dollars."
    assert check_text(text, live=LIVE_40) == text
    assert "offer" not in hits(text, live=LIVE_40)


# ---- precedence --------------------------------------------------------------------------------


def test_an_offer_with_an_unbacked_price_gets_the_money_sentence() -> None:
    text = "I can check the price: it's 45 dollars."
    assert check_text(text) == FIXED_SENTENCES["money"]
    result = hits(text)
    assert result.get("money") == 1
    assert "offer" not in result


def test_an_offer_with_an_unbacked_stock_count_gets_the_stock_sentence() -> None:
    text = "I can check stock: 12 left."
    assert check_text(text) == FIXED_SENTENCES["stock"]
    result = hits(text)
    assert result.get("stock") == 1
    assert "offer" not in result


def test_an_offer_with_a_promotion_gets_the_promotion_sentence() -> None:
    text = "I can look up the price with your discount code."
    assert check_text(text) == FIXED_SENTENCES["promotion"]
    result = hits(text)
    assert result.get("promotion") == 1
    assert "offer" not in result


def test_an_offer_whose_only_other_match_is_live_backed_is_still_dropped() -> None:
    text = "I can check the price, which is 40 dollars."
    assert hits(text, live=LIVE_40) == {"offer": 1}
    assert check_text(text, live=LIVE_40).strip() == ""


def test_a_blocked_class_in_one_sentence_leaves_a_later_offer_to_be_dropped() -> None:
    text = f"It is 45 dollars. I can check the price. {AFTER}"
    assert check_text(text, seen=SEEN) == f"{FIXED_SENTENCES['money']} {AFTER}"
    assert hits(text, seen=SEEN) == {"money": 1, "offer": 1}


# ---- hard rule 11 ------------------------------------------------------------------------------


def test_a_stand_alone_disclosure_survives_a_following_offer() -> None:
    disclosure = "I'm an AI shopping assistant."
    text = f"{disclosure} I can check the price and stock for you."
    assert check_text(text).strip() == disclosure
    assert hits(text) == {"offer": 1}


# ---- streaming ---------------------------------------------------------------------------------


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 11])
@pytest.mark.parametrize(
    "sentence",
    [
        "I can look up the live price of the Halvardine Home Wren Bath Towel.",
        "Tell me which one you like and I can check the price and stock.",
        "Want me to check stock?",
    ],
)
def test_an_offer_split_across_deltas_shows_what_check_text_shows(sentence: str, size: int) -> None:
    text = f"{BEFORE} {sentence} {AFTER}"
    events, check = run(chunks(text, size))
    assert check.shown == check_text(text, seen=SEEN) == f"{BEFORE} {AFTER}"
    assert screen(events) == check.shown
    assert check.counts["offer"] == 1


def test_an_offer_split_mid_word_and_mid_phrase() -> None:
    pieces = ["Fine [P0006]. I ca", "n loo", "k up the li", "ve pr", "ice for you", ". Done."]
    events, check = run(pieces)
    assert check.shown.strip() == "Fine [P0006]. Done."
    assert screen(events) == check.shown


def test_an_offer_sentence_is_never_released_while_undecided() -> None:
    pieces = chunks(f"{BEFORE} I can check the price and stock for you.", 4)
    check = OutputCheck(set(SEEN), [])
    sent = ""
    for piece in pieces:
        sent += "".join(e for e in check.feed(piece) if e is not None)
    sent += "".join(e for e in check.finish() if e is not None)
    assert "can check" not in sent and "price and stock" not in sent


def test_a_partly_released_offer_gets_a_reset_and_the_screen_lacks_it() -> None:
    # The words before "I can" hold no trigger and run past `L` after the "[" of BEFORE's citation,
    # so they are released before the offer completes: the check must take them back.
    sentence = (
        "Tell me which of the colours from the range above suits you best, "
        "and I can check the price and stock for you."
    )
    pieces = f"{BEFORE} {sentence}".split(" ")
    pieces = [p + " " for p in pieces[:-1]] + [pieces[-1]]
    events, check = run(pieces)
    assert None in events, "words of the sentence went out before the match completed"
    assert screen(events) == check.shown
    assert check.shown.rstrip() == BEFORE
    assert check.shown == check_text(f"{BEFORE} {sentence}", seen=SEEN)


def test_an_offer_held_from_an_earlier_trigger_never_reaches_the_screen() -> None:
    # The first input of the Reset test above: BEFORE's "[" holds the next `L` characters, so no
    # word of the offer is released and no Reset is needed. Kept for its result.
    sentence = "Tell me which colour you want and I can check the price and stock for you."
    pieces = f"{BEFORE} {sentence}".split(" ")
    pieces = [p + " " for p in pieces[:-1]] + [pieces[-1]]
    events, check = run(pieces)
    assert screen(events) == check.shown
    assert check.shown.rstrip() == BEFORE


@pytest.mark.parametrize("size", [1, 3, 6])
def test_streaming_never_leaves_more_on_screen_than_check_text(size: int) -> None:
    for sentence in OFFERS:
        text = f"{BEFORE} {sentence} {AFTER}"
        events, check = run(chunks(text, size))
        assert screen(events) == check.shown == check_text(text, seen=SEEN), sentence


# ---- width -------------------------------------------------------------------------------------


def test_offer_patterns_exist_and_none_is_wider_than_l() -> None:
    offer = [p for p in PATTERNS if p.cls == "offer"]
    assert offer, "no pattern of class `offer`"
    for pattern in offer:
        assert _parser.parse(pattern.regex.pattern).getwidth()[1] <= L, pattern.regex.pattern


# -- after the security review (row #16, L4): an offer still open when the text is thrown away ---


@pytest.mark.parametrize("how", ["discard", "abort"])
def test_an_open_offer_is_counted_when_the_turn_is_thrown_away(how: str) -> None:
    check = OutputCheck(set(SEEN), [])
    shown = check.feed(f"{BEFORE} I can check the price and stock for you")
    if how == "discard":
        shown += check.reset(discard=True)
    else:
        check.abort()
    assert check.counts["offer"] == 1
    assert "check the price" not in "".join(s for s in shown if s is not None)


def test_an_open_offer_with_an_unbacked_price_is_counted_as_money_when_thrown_away() -> None:
    check = OutputCheck(set(SEEN), [])
    check.feed(f"{BEFORE} I can check the price: it's 45 dollars")
    check.abort()
    assert check.counts["money"] == 1
    assert check.counts["offer"] == 0
