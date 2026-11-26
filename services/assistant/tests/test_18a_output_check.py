"""Row 18a, part 0 (hard rule 2; the user's decision: first on 18a's list, every example failing
first). Rows #30 + #22 security read 3: M-B, a negation whose scope crosses a cut read as positive,
so a false "not in stock" was vouched by units on hand, and "Neither …" passed with nothing checked;
M-A, "none left" wordings that matched no stock pattern. A widening only: nothing replaced before
may become shown. Written from `PROGRESS.md`, "Open items", and the plan, not from the code."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from northwind_assistant.output_check import (
    FIXED_SENTENCES,
    PATTERNS,
    LiveValues,
    OutputCheck,
    _negated,
    check_text,
    hits,
)

pytestmark = pytest.mark.req("RAG-09")

STOCK = FIXED_SENTENCES["stock"]

# M-B, worst case first: shown with no stock check at all.
NEITHER = "Neither colour, Sage or Black, is available in sizes M and L."
# M-B: shown with 5 units checked.
CROSSING = [
    "None of the Slate towels, Bath or Hand, is in stock.",
    "None of the 2.5 m rugs is in stock.",
    "The towel is not, in Slate, Bath size, in stock.",
    "The towel is not (in Slate) (Bath) in stock.",
    "The towel is not, as far as I can tell today, currently in stock.",
    "The towel is not\nin stock.",
]
# M-A: shown with nothing checked.
NONE_LEFT = [
    "There aren't any left in size M.",
    "We don't have any left.",
    "Zero left.",
    "No units left.",
    "No stock remaining.",
    "None of them left.",
    "No more stock left.",
]
# Near-misses of the same shapes that still say there is no stock: blocked with units on hand.
CROSSING_MORE = [
    "The towel is not, and I checked, in stock.",
    "None of them, it seems, is in stock.",
    "The towel is not, in Slate,\nin stock.",
    "None of the 2.5 m, 3 m or 4.5 m rugs is in stock.",
]


def stock(**units: int) -> LiveValues:
    live = LiveValues()
    live.stock.update(units)
    return live


def every_cut(text: str, live: LiveValues | None = None) -> set[str]:
    shown: set[str] = set()
    for cut in range(len(text) + 1):
        check = OutputCheck(set(), [], live=live or LiveValues())
        check.feed(text[:cut])
        check.feed(text[cut:])
        check.finish()
        shown.add(check.shown)
    return shown


def word_by_word(text: str, live: LiveValues | None = None) -> str:
    check = OutputCheck(set(), [], live=live or LiveValues())
    for piece in re.findall(r"\s+|\S+", text):
        check.feed(piece)
    check.finish()
    return check.shown


# ---- M-B --------------------------------------------------------------------------------------


def test_neither_with_no_stock_check_is_a_stock_claim() -> None:
    assert hits(NEITHER) == {"stock": 1}
    assert check_text(NEITHER) == STOCK
    assert hits(NEITHER, live=stock(a=5)) == {"stock": 1}


@pytest.mark.parametrize("text", [*CROSSING, *CROSSING_MORE])
def test_a_negation_across_a_cut_is_not_vouched_by_units_on_hand(text: str) -> None:
    assert hits(text, live=stock(a=5)) == {"stock": 1}
    assert hits(text, live=stock(a=5, b=2)) == {"stock": 1}
    assert hits(text) == {"stock": 1}


@pytest.mark.parametrize("text", [NEITHER, *CROSSING])
def test_a_crossing_negation_never_stays_on_screen_however_the_deltas_fall(text: str) -> None:
    live = stock(a=5)
    assert all(STOCK in shown and "in stock." not in shown for shown in every_cut(text, live))
    assert STOCK in word_by_word(text, live)


@pytest.mark.parametrize(
    "text",
    [
        "Neither colour is waterproof. It is available in sizes M and L.",
        "No, the Charcoal L is out of stock right now.",
    ],
)
def test_a_negation_outside_the_sentence_or_a_reply_word_is_left_alone(text: str) -> None:
    live = stock(a=0) if "out of stock" in text else stock(a=5)
    assert check_text(text, live=live) == text


def test_a_reply_word_does_not_hide_a_negation_after_it() -> None:
    text = "No, none of the Slate towels, Bath or Hand, is in stock."
    assert hits(text, live=stock(a=5)) == {"stock": 1}


# ---- M-A --------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", NONE_LEFT)
def test_a_none_left_wording_is_a_stock_claim_with_nothing_checked(text: str) -> None:
    assert hits(text) == {"stock": 1}
    assert check_text(text) == STOCK


@pytest.mark.parametrize("text", NONE_LEFT)
def test_a_none_left_wording_is_vouched_only_when_every_checked_variant_is_at_zero(
    text: str,
) -> None:
    assert hits(text, live=stock(a=0, b=0)) == {}
    assert hits(text, live=stock(a=5)) == {"stock": 1}
    assert hits(text, live=stock(a=0, b=1)) == {"stock": 1}


@pytest.mark.parametrize("text", NONE_LEFT)
def test_a_none_left_wording_never_stays_on_screen_however_the_deltas_fall(text: str) -> None:
    assert every_cut(text) == {STOCK}
    assert word_by_word(text) == STOCK


@pytest.mark.parametrize(
    "text",
    [
        "It has no left pocket.",
        "Turn left at the hem.",
        "None of these is waterproof.",
        "There are no questions left in the reviews.",
        "Is there any left-hand pocket?",
        "No more than two washes a month.",
        "Any of them would suit a wet commute.",
    ],
)
def test_other_uses_of_the_words_pass(text: str) -> None:
    assert hits(text) == {}
    assert check_text(text) == text


# ---- a widening only -------------------------------------------------------------------------

BEFORE = Path(__file__).parent / "fixtures" / "18a_patterns_at_81683c2.json"


def test_every_pattern_of_the_base_commit_is_still_checked_unchanged() -> None:
    now = [[p.cls, p.regex.pattern, p.exempt] for p in PATTERNS]
    before = json.loads(BEFORE.read_text())
    assert len(before) == 63
    missing = [entry for entry in before if entry not in now]
    assert missing == []


SENTENCES = [
    *CROSSING,
    *CROSSING_MORE,
    NEITHER,
    "It is in stock.",
    "It isn't in stock.",
    "It is not waterproof, not packable, and it is in stock.",
    "There is no size chart on the page, but it is available in sizes M and L.",
    "There's no doubt the M is in stock.",
    "Not sure if you saw, but the M is available.",
    "It is, not surprisingly, available in three colours.",
    "No, the Charcoal L is out of stock right now.",
]


@pytest.mark.parametrize("text", SENTENCES)
def test_the_sentence_rule_only_ever_takes_a_vouch_away(text: str) -> None:
    """`_negated` is unchanged; past it the new rule can only turn "no negation" into "unvouched"
    (None), never into a vouch."""
    from northwind_assistant.output_check import negated_in_sentence  # new in 18a

    for found in re.finditer(r"in stock|available|out of stock", text):
        old = _negated(text, found.start())
        wide = negated_in_sentence(text[: found.start()])
        assert old in (True, False, None)
        if old is not False:
            continue  # the new rule is not read: the old answer stands
        assert wide in (True, False)


def test_the_claim_is_either_shown_whole_or_replaced_at_every_stock_level() -> None:
    """A newline ends the sentence that is replaced (`_BOUNDARY`), so for "is not" newline "in
    stock" the first line stays and the claim's line goes."""
    for text in [*CROSSING, *CROSSING_MORE, *NONE_LEFT, NEITHER]:
        head = text.rsplit("\n", 1)[0] + "\n" if "\n" in text else ""
        for live in (None, stock(a=0), stock(a=5), stock(a=0, b=5)):
            shown = check_text(text, live=live)
            assert shown in (text, STOCK, head + STOCK), (text, shown)


# ---- the reach (test-writer's gap: a negation 160 characters back was missed) ------------------

ASIDE = ", as far as I can tell from the pages I read and the notes I was given last week"


def test_a_negation_past_the_reach_leaves_the_sentence_unread_and_the_claim_unvouched() -> None:
    text = "The towel is not" + ASIDE * 4 + ", currently in stock."
    assert len(text) > 4 * 64 + 40
    assert hits(text, live=stock(a=5)) == {"stock": 1}
    assert hits(text, live=stock(a=0)) == {"stock": 1}


def test_a_long_sentence_with_no_negation_is_read_whole_within_the_reach() -> None:
    """About 200 characters before the claim, all in one sentence: read whole, so a true claim
    shows."""
    text = "The towel" + ASIDE.replace(",", "") * 2 + " and on the label, is in stock."
    assert 150 < text.index("in stock") < 4 * 64
    assert check_text(text, live=stock(a=5)) == text
    assert hits(text) == {"stock": 1}
