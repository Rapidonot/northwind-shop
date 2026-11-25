"""Row #30 gap tests (test-writer): H3, M2, M1 and the offer patterns of the output check, written
from the plan's table, decision 0015's "Row #30 note" and the criteria, not from the code's logic.
Synthetic text only; no model. The strict xfails are findings the user logged, not loosened tests."""

from __future__ import annotations

import logging
import re

import pytest

from northwind_assistant.output_check import (
    FIXED_SENTENCES,
    LiveValues,
    OutputCheck,
    check_text,
    citation_ids,
    citation_parts,
    hits,
    live_backed,
)
from northwind_assistant.turn import cited_ids

LINK = FIXED_SENTENCES["link"]
STOCK = FIXED_SENTENCES["stock"]
SEEN = {"P0001", "P0011", "R00002", "FAQ-019", "POL-RETURNS-US", "SG-TOWELS", "CG-SHELL"}

# The user's decision (stop 5, 2026-11-25): 19 tests that fail are strict xfails, so a later fix
# cannot pass unnoticed. Findings as numbered in the row's report to the user.
RANGE_SPLIT = pytest.mark.xfail(
    strict=True,
    reason="finding 1: the stock rule replaces a range split mid-phrase; fails closed, on main "
    "too, no recording has it (counted at W6; window B's output-check prerequisite row before "
    "RECORD-ALL owns the fix if any appear)",
)
LONG_GROUP = pytest.mark.xfail(
    strict=True,
    reason="finding 2: an invented ID in a group over 62 characters stays on screen; the check "
    "reads groups up to that length, and the evals still fail the answer",
)
NEWLINE_GROUP = pytest.mark.xfail(
    strict=True,
    reason="finding 3: an invented ID in a group with a newline stays on screen; the check reads "
    "one line, and the evals still fail the answer",
)
OFFER_EDGE = pytest.mark.xfail(
    strict=True,
    reason="finding 4: an offer-pattern edge (bold inside the offer, or an extra adjective); "
    "D8 only, no recording has it",
)
PARENTHETICAL = pytest.mark.xfail(
    strict=True,
    reason="finding 5: a parenthetical negation that governs nothing blocks the range; fails "
    "closed, on main too",
)


def stock(**units: int) -> LiveValues:
    live = LiveValues()
    live.stock.update(units)
    return live


def streamed(
    text: str, step: int, live: LiveValues | None = None, seen: set[str] | None = None
) -> str:
    """What stays on screen when `text` arrives `step` characters at a time."""
    check = OutputCheck(set(seen or ()), [], live=live or LiveValues())
    for at in range(0, len(text), step):
        check.feed(text[at : at + step])
    check.finish()
    return check.shown


def by_words(text: str, live: LiveValues | None = None, seen: set[str] | None = None) -> str:
    check = OutputCheck(set(seen or ()), [], live=live or LiveValues())
    for piece in re.findall(r"\S+\s*", text):
        check.feed(piece)
    check.finish()
    return check.shown


def every_split(
    text: str, live: LiveValues | None = None, seen: set[str] | None = None
) -> set[str]:
    shown = set()
    for cut in range(len(text) + 1):
        check = OutputCheck(set(seen or ()), [], live=live or LiveValues())
        check.feed(text[:cut])
        check.feed(text[cut:])
        check.finish()
        shown.add(check.shown)
    return shown


# ---------------------------------------------------------------------------------------------
# H3: "available in <plural range>" is the range offered only when nothing negates it.
# ---------------------------------------------------------------------------------------------

NEGATED = [
    "Not available in sizes M and L.",
    "Sadly, not available in three colours.",
    "It's currently unavailable in sizes M and L.",
    "Unavailable in all sizes.",
    "IT IS NOT AVAILABLE IN SIZES M AND L.",
    "It wasn't available in several colours.",
    "They aren't available in sizes M and L.",
    "It won't be available in sizes M and L.",
    "It isn't ever available in three colours.",
    "None are available in sizes M and L.",
    "Nothing is available in three colours.",
    "It is not available in many sizes.",
    "It is not, as far as I know, available in sizes M and L.",
    "It isn't (sadly) available in three colours.",
    "It isn't - sadly - available in three colours.",
    "It is not yet available in sizes M and L.",
    "It is not **available** in sizes M and L.",
    "It is not available in a range of colours.",
    "They're not available in a range of sizes.",
    "It is not available in 4 sizes.",
    "It's unavailable in colours Sage and Black.",
]


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize("text", NEGATED)
def test_a_negated_range_wording_is_a_stock_claim(text: str) -> None:
    assert hits(text) == {"stock": 1}, text
    assert check_text(text) == STOCK


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize("text", NEGATED)
@pytest.mark.parametrize("step", [1, 2, 3])
def test_a_negated_range_never_shows_when_it_arrives_in_tokens(text: str, step: int) -> None:
    assert streamed(text, step) == STOCK


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize("text", NEGATED[:6])
def test_a_negated_range_never_shows_when_it_arrives_word_by_word(text: str) -> None:
    assert by_words(text) == STOCK


@pytest.mark.req("RAG-09")
def test_a_negated_range_after_other_text_replaces_only_its_sentence() -> None:
    text = "The jacket is warm. It is unavailable in sizes M and L. It dries fast."
    for shown in (check_text(text), streamed(text, 1), by_words(text)):
        assert shown == f"The jacket is warm. {STOCK} It dries fast."


@pytest.mark.req("RAG-09")
def test_a_negated_range_in_a_list_line_is_replaced() -> None:
    shown = check_text("Options:\n- It is not available in sizes M and L.\n- It dries fast.")
    assert "sizes M and L" not in shown
    assert "It dries fast." in shown


# An ordinary range, with a negation that governs something else, passes.

ORDINARY = [
    "There is no size chart on the page, but it is available in sizes M and L.",
    "It isn't waterproof. It is available in three colours.",
    "It isn't packable; it is available in sizes S to XL.",
    "Not only is it warm, it is available in three colours.",
    "It's not heavy - it's available in several colours.",
    "It is available in sizes M and L (no returns on sale items).",
    "It is available in sizes M and L.\nNot machine washable.",
    "Nothing beats it: it is available in all sizes.",
    "It is not cheap (nor is it dear); it is available in a range of colours.",
    "Do not tumble dry. It is available in a range of colours.",
    "It is, not surprisingly, available in three colours.",
    pytest.param("It is available (not that you asked) in three colours.", marks=PARENTHETICAL),
    "I can't promise a colour, but I can tell you it is available in three colours.",
    "It is available in sizes M and L, and there are no hidden extras.",
    "They are available in three colours and they never fade.",
]


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize("text", ORDINARY)
def test_an_ordinary_range_with_a_negation_elsewhere_passes(text: str) -> None:
    assert hits(text) == {}, text
    assert check_text(text) == text


STREAMED_ORDINARY = [
    "It is available in three colours.",
    "They are available in sizes M and L.",
    "There is no size chart on the page, but it is available in sizes M and L.",
    "It isn't waterproof. It is available in three colours.",
]


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize("text", STREAMED_ORDINARY)
@RANGE_SPLIT
def test_an_ordinary_range_passes_when_it_arrives_in_tokens(text: str) -> None:
    assert streamed(text, 1) == text
    assert streamed(text, 3) == text


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize("text", STREAMED_ORDINARY)
@RANGE_SPLIT
def test_an_ordinary_range_passes_when_it_arrives_word_by_word(text: str) -> None:
    assert by_words(text) == text


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize("text", STREAMED_ORDINARY)
@RANGE_SPLIT
def test_an_ordinary_range_passes_however_the_deltas_split(text: str) -> None:
    assert every_split(text) == {text}


# Vouching: a negated range is a stock claim like any other, vouched only by check_stock at zero.

UNAVAILABLE = "It is unavailable in sizes M and L."


@pytest.mark.req("RAG-09")
def test_an_unvouched_negated_range_with_no_stock_check_is_blocked() -> None:
    assert hits(UNAVAILABLE, live=LiveValues()) == {"stock": 1}
    assert hits(UNAVAILABLE, live=stock()) == {"stock": 1}


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize(
    "units",
    [{"a": 0}, {"a": 0, "b": 0, "c": 0}],
    ids=["one-zero", "all-zero"],
)
def test_a_negated_range_is_vouched_when_every_checked_variant_is_zero(
    units: dict[str, int],
) -> None:
    assert hits(UNAVAILABLE, live=stock(**units)) == {}
    assert check_text(UNAVAILABLE, live=stock(**units)) == UNAVAILABLE


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize(
    "units",
    [{"a": 0, "b": 0, "c": 1}, {"a": 1, "b": 0, "c": 0}, {"a": 2}, {"a": 0, "b": 12}],
    ids=["last-in-stock", "first-in-stock", "all-in-stock", "mixed"],
)
def test_a_negated_range_is_blocked_when_any_checked_variant_has_stock(
    units: dict[str, int],
) -> None:
    assert hits(UNAVAILABLE, live=stock(**units)) == {"stock": 1}
    assert check_text(UNAVAILABLE, live=stock(**units)) == STOCK


@pytest.mark.req("RAG-09")
def test_the_vouch_for_a_negated_range_holds_token_by_token_both_ways() -> None:
    assert streamed(UNAVAILABLE, 1, stock(a=0, b=0)) == UNAVAILABLE
    assert streamed(UNAVAILABLE, 1, stock(a=0, b=4)) == STOCK
    assert every_split(UNAVAILABLE, stock(a=0, b=0)) == {UNAVAILABLE}
    assert every_split(UNAVAILABLE, stock(a=0, b=4)) == {STOCK}


@pytest.mark.req("RAG-09")
@pytest.mark.parametrize(
    "text",
    [
        "It isn't available in three colours.",
        "It is not available in 3 colours.",
        "It is unavailable in 2 sizes.",
    ],
)
def test_the_number_of_a_range_is_never_read_as_units(text: str) -> None:
    # Checked units 3 and 2: the range's own number must not vouch for (or against) anything.
    assert hits(text, live=stock(a=3, b=2)) == {"stock": 1}
    assert hits(text, live=stock(a=0, b=0)) == {}


@pytest.mark.req("RAG-09")
def test_a_negated_range_is_not_live_backed_as_a_range_offered() -> None:
    assert live_backed("It is available in sizes M and L.", stock(a=0, b=0)) == 0


@pytest.mark.req("RAG-09")
def test_a_range_offered_is_the_same_with_or_without_a_stock_check() -> None:
    text = "It is available in sizes M and L."
    for live in (LiveValues(), stock(a=0), stock(a=5)):
        assert hits(text, live=live) == {}
        assert check_text(text, live=live) == text


@pytest.mark.req("RAG-09")
def test_a_specific_size_is_still_a_stock_claim() -> None:
    assert hits("It is available in size M.") == {"stock": 1}
    assert hits("It is not available in size M.") == {"stock": 1}


# ---------------------------------------------------------------------------------------------
# M2: a link's match ends before the punctuation that ends its sentence.
# ---------------------------------------------------------------------------------------------

PAYLOADS = {
    "Use code STAFFALL at checkout.": "promotion",
    "Enjoy a discount of twenty percent.": "promotion",
    "Your order is confirmed.": "confirmation",
    "It is out of stock.": "stock",
    "Email verify@northwind-support.example for help.": "link",
    "Your refund has been issued.": "refund_authority",
}


@pytest.mark.req("RAG-08")
@pytest.mark.parametrize(
    "text",
    [
        'See "northwind-deals.example". The jacket is warm.',
        "See 'northwind-deals.example'. The jacket is warm.",
        "See “northwind-deals.example”. The jacket is warm.",
        "(see northwind-deals.example). The jacket is warm.",
        "See [northwind-deals.example]. The jacket is warm.",
        "See <northwind-deals.example>. The jacket is warm.",
        "See **northwind-deals.example**. The jacket is warm.",
        "See `northwind-deals.example`. The jacket is warm.",
        "See [our deals](https://northwind-deals.example/sale). The jacket is warm.",
        "Visit northwind-deals.example for savings. The jacket is warm.",
        "See northwind-deals.example.\nThe jacket is warm.",
        "See northwind-deals.example... The jacket is warm.",
        "See northwind-deals.example?! The jacket is warm.",
    ],
)
def test_a_link_in_quotes_or_brackets_leaves_the_next_sentence_alone(text: str) -> None:
    for shown in (check_text(text), streamed(text, 1), by_words(text)):
        assert "northwind-deals" not in shown
        assert "https" not in shown
        assert shown.endswith("The jacket is warm."), shown
        assert LINK in shown


@pytest.mark.req("RAG-08")
def test_several_links_each_go_and_the_rest_stays() -> None:
    text = (
        "See northwind-deals.example. Or try northwind-promo.example! "
        "Mail help@deals.example. Also 203.0.113.9? The jacket is warm."
    )
    for shown in (check_text(text), streamed(text, 1), by_words(text)):
        for bad in ("northwind-deals", "northwind-promo", "help@deals", "203.0.113"):
            assert bad not in shown
        assert shown.endswith("The jacket is warm.")


@pytest.mark.req("RAG-08")
def test_two_links_in_one_sentence_are_one_replaced_sentence() -> None:
    text = "See northwind-deals.example or northwind-promo.example. The jacket is warm."
    for shown in (check_text(text), streamed(text, 1)):
        assert "northwind" not in shown
        assert shown.endswith("The jacket is warm.")


@pytest.mark.req("RAG-08")
@pytest.mark.parametrize(("payload", "cls"), list(PAYLOADS.items()))
def test_the_sentence_after_a_link_is_checked_on_its_own(payload: str, cls: str) -> None:
    text = f"See northwind-deals.example. {payload}"
    for shown in (check_text(text), streamed(text, 1), by_words(text)):
        assert "northwind-deals" not in shown
        assert payload not in shown
        assert FIXED_SENTENCES[cls] in shown, shown


@pytest.mark.req("RAG-08")
@pytest.mark.parametrize(("payload", "cls"), list(PAYLOADS.items()))
def test_a_payload_after_a_link_never_shows_however_the_deltas_fall(payload: str, cls: str) -> None:
    text = f"See northwind-deals.example. {payload}"
    for shown in every_split(text):
        assert payload not in shown
        assert "northwind-deals" not in shown
        assert FIXED_SENTENCES[cls] in shown


@pytest.mark.req("RAG-08")
def test_a_payload_after_a_link_in_quotes_never_shows() -> None:
    text = 'See "northwind-deals.example". Use code STAFFALL.'
    for shown in (check_text(text), streamed(text, 1)):
        assert "STAFFALL" not in shown
        assert "northwind-deals" not in shown


@pytest.mark.req("RAG-08")
def test_a_link_at_the_very_end_of_the_text_is_replaced() -> None:
    for text in ("See northwind-deals.example", "See northwind-deals.example."):
        assert "northwind-deals" not in check_text(text)
        assert "northwind-deals" not in streamed(text, 1)


@pytest.mark.req("RAG-08")
def test_the_care_address_and_ordinary_dots_still_pass() -> None:
    for text in (
        "Write to care@northwind.example. The jacket is warm.",
        "It weighs 1.2 kg. The jacket is warm.",
        "It is 4.5 mm thick, e.g. for winter. It is warm.",
        "Rated 4.5 stars from 120 reviews. It is warm.",
    ):
        assert check_text(text) == text, text
        assert streamed(text, 1) == text, text


# ---------------------------------------------------------------------------------------------
# M1: one reading of a citation group.
# ---------------------------------------------------------------------------------------------


@pytest.mark.req("RAG-08")
@pytest.mark.parametrize(
    ("group", "ids"),
    [
        ("P0001, P0011; R00002", ["P0001", "P0011", "R00002"]),
        ("P0001 ,P0011;R00002", ["P0001", "P0011", "R00002"]),
        ("P0001;P0011;R00002", ["P0001", "P0011", "R00002"]),
        ("P0001,,P0011", ["P0001", "P0011"]),
        (";P0001;", ["P0001"]),
        ("P0001,\nP0011", ["P0001", "P0011"]),
        ("P0001;\n  P0011", ["P0001", "P0011"]),
        ("\tP0001 ; P0011\t", ["P0001", "P0011"]),
        ("P9999", ["P9999"]),
        ("P0001; P9999", ["P0001", "P9999"]),
        ("size chart; P0001", ["P0001"]),
        ("FAQ-019; POL-RETURNS-US", ["FAQ-019", "POL-RETURNS-US"]),
        ("P0156-CHR-BATHTOWEL; SG-TOWELS", ["P0156-CHR-BATHTOWEL", "SG-TOWELS"]),
        ("", []),
        (" ; , ", []),
    ],
)
def test_citation_ids_reads_commas_and_semicolons(group: str, ids: list[str]) -> None:
    assert citation_ids(group) == ids


@pytest.mark.req("RAG-08")
def test_citation_parts_are_stripped_and_non_empty_and_keep_other_text() -> None:
    assert citation_parts(" P0001 ;size chart,, R00002 ") == ["P0001", "size chart", "R00002"]
    assert citation_parts("") == []


@pytest.mark.req("RAG-08")
def test_a_long_group_is_read_whole() -> None:
    real = [f"P{n:04d}" for n in range(1, 41)]
    group = "; ".join(real[:20]) + ", " + ", ".join(real[20:]) + "; P9999"
    assert citation_ids(group) == [*real, "P9999"]
    answer = f"Warm [{group}]."
    seen = set(real)
    assert cited_ids(answer, seen) == tuple(real)


LONG_REAL = ["P0001", "P0011", "R00002", "FAQ-019", "POL-RETURNS-US", "SG-TOWELS", "CG-SHELL"]


@pytest.mark.req("RAG-08")
@LONG_GROUP
def test_an_invented_id_in_a_long_group_leaves_the_screen() -> None:
    """A group of 8 parts is longer than the hold window; an invented ID in it must still go."""
    group = "; ".join([*LONG_REAL, "P9999"])
    assert len(group) > 64
    for shown in (
        check_text(f"Warm [{group}].", (), SEEN),
        streamed(f"Warm [{group}].", 1, seen=SEEN),
    ):
        assert "P9999" not in shown
        for real in LONG_REAL:
            assert real in shown


@pytest.mark.req("RAG-08")
@NEWLINE_GROUP
def test_an_invented_id_in_a_group_with_a_newline_leaves_the_screen() -> None:
    shown = check_text("Warm [P0001,\nP9999].", (), SEEN)
    assert "P9999" not in shown


@pytest.mark.req("RAG-08")
@pytest.mark.parametrize(
    "group",
    [
        "P0001, P0011; P9999; R00002",
        "P9999; P0001",
        "P0001; P0011, P9999",
        "P0001 ;P9999",
        "P0001;P9999;R00002",
    ],
)
def test_an_invented_id_among_real_ones_goes_and_the_real_ones_stay(group: str) -> None:
    text = f"Warm [{group}]. Dry [P0011]."
    for shown in (
        check_text(text, (), SEEN),
        streamed(text, 1, seen=SEEN),
        by_words(text, seen=SEEN),
    ):
        assert "P9999" not in shown
        for real in citation_ids(group):
            if real != "P9999":
                assert real in shown
        assert shown.endswith("Dry [P0011].")


@pytest.mark.req("RAG-08")
def test_an_invented_id_is_the_same_wherever_the_deltas_fall_in_a_long_group() -> None:
    text = "Warm [P0001, P0011; P9999; R00002; FAQ-019]. Dry [SG-TOWELS]."
    results = every_split(text, seen=SEEN)
    assert len(results) == 1
    (shown,) = results
    assert "P9999" not in shown
    assert shown.endswith("Dry [SG-TOWELS].")


@pytest.mark.req("RAG-08")
def test_several_groups_each_lose_only_their_invented_ids() -> None:
    text = "A [P0001; P8888]. B [P9999]. C [R00002, P7777; FAQ-019]."
    shown = check_text(text, (), SEEN)
    for bad in ("P8888", "P9999", "P7777"):
        assert bad not in shown
    for good in ("P0001", "R00002", "FAQ-019"):
        assert good in shown


@pytest.mark.req("RAG-08")
def test_a_group_of_real_ids_with_mixed_separators_is_left_as_written() -> None:
    text = "Warm [P0001, P0011; R00002]. Return it per policy [FAQ-019; POL-RETURNS-US]."
    assert check_text(text, (), SEEN) == text
    assert streamed(text, 1, seen=SEEN) == text
    assert every_split(text, seen=SEEN) == {text}


@pytest.mark.req("RAG-08")
def test_cited_ids_keeps_first_mention_order_and_drops_repeats_and_invented() -> None:
    answer = "A [P0011; P9999]. B [P0001, P0011]. C [R00002;FAQ-019] [P0001]."
    assert cited_ids(answer, SEEN) == ("P0011", "P0001", "R00002", "FAQ-019")


@pytest.mark.req("RAG-08")
def test_cited_ids_with_nothing_real_is_empty_and_text_in_brackets_is_not_a_citation() -> None:
    assert cited_ids("Warm [P9998; P9999].", SEEN) == ()
    assert cited_ids("It is warm [size chart; see below].", SEEN) == ()
    assert cited_ids("No brackets at all.", SEEN) == ()


@pytest.mark.req("RAG-08")
def test_cited_ids_reads_a_semicolon_and_a_comma_group_the_same() -> None:
    assert cited_ids("W [P0001; P0011; R00002].", SEEN) == cited_ids(
        "W [P0001, P0011, R00002].", SEEN
    )


@pytest.mark.req("RAG-08")
def test_cited_ids_logs_the_invented_ones(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        cited_ids("Warm [P0001; P9999].", SEEN)
    assert any("citation" in record.getMessage() for record in caplog.records)


@pytest.mark.req("RAG-08")
def test_the_check_and_turn_agree_on_what_is_cited() -> None:
    """What the check leaves on screen is what `cited_ids` reports (the user's M1 aim)."""
    text = "A [P0011; P9999]. B [P0001, P8888, R00002]. C [FAQ-019;P7777]."
    shown = check_text(text, (), SEEN)
    on_screen = tuple(
        dict.fromkeys(
            part for group in re.findall(r"\[([^\[\]]+)\]", shown) for part in citation_ids(group)
        )
    )
    assert on_screen == cited_ids(text, SEEN)


# ---------------------------------------------------------------------------------------------
# D8: the offer class and the clarifying questions that must pass.
# ---------------------------------------------------------------------------------------------

OFFERS = [
    "I can then look up the price for you.",
    "I could then check the stock for you.",
    "We can then give you the availability.",
    "I can also look up the price.",
    "I can also check availability for you.",
    "I can then give you a live price.",
    "I can then give you the exact price and stock.",
    "We can also give you the stock.",
    "I would then check the price.",
    "If you want an availability check, just say.",
    "If you'd like a price check for your size, tell me.",
    "If you would like a price check, let me know.",
    "IF YOU'D LIKE A PRICE OR STOCK CHECK, TELL ME THE SIZE.",
    pytest.param("If you'd like a **price or stock check**, tell me the size.", marks=OFFER_EDGE),
    "If you'd like a live price check, just ask.",
    "If you want a stock check on that size, say so.",
    pytest.param("If you'd like a quick availability check, tell me the colour.", marks=OFFER_EDGE),
    "I can then give you price and availability.",
    pytest.param("I can **check the stock** for you.", marks=OFFER_EDGE),
    pytest.param("I can **then give you the price**.", marks=OFFER_EDGE),
]


@pytest.mark.req("RAG-08")
@pytest.mark.parametrize("text", OFFERS)
def test_an_offer_wording_is_dropped(text: str) -> None:
    assert hits(text) == {"offer": 1}, text
    assert check_text(f"It is warm [P0001]. {text}", (), {"P0001"}).strip() == "It is warm [P0001]."


@pytest.mark.req("RAG-08")
@pytest.mark.parametrize("text", OFFERS[:9])
def test_an_offer_wording_never_shows_when_it_arrives_in_tokens(text: str) -> None:
    for step in (1, 2, 5):
        assert streamed(text, step) == ""
    assert every_split(text) == {""}


@pytest.mark.req("RAG-08")
@pytest.mark.parametrize(
    "text",
    [
        "Which size should I check the price for?",
        "Which colour should I check availability for?",
        "What size shall I look up the price for?",
    ],
)
def test_a_question_built_on_the_assistants_own_lookup_is_still_dropped(text: str) -> None:
    """The user's Q2: the check is not loosened; the prompt steers SA-04's questions instead."""
    assert hits(text) == {"offer": 1}
    assert check_text(text) == ""


PASSING_QUESTIONS = [
    "If you'd like something under a set price, what size do you need?",
    "If you'd like a rug, which room is it for and what is your price range?",
    "If you'd like a bed with a firm feel, what size is it?",
    "If you want a jacket for the cold, what is your budget?",
    "If you would like a gift, who is it for and what is your budget?",
    "If you'd like a bigger one, how many guests will you seat?",
    "If you'd like a warmer layer, will you wear it outdoors or inside?",
    "If you want one for hiking, how long are your trips?",
    "If you'd like a towel, is it for the bath or the beach?",
    "If you would like a fleece, do you want a light or a heavy one?",
    "If you'd like to narrow it down, what is your chest measurement?",
    "If you want a recommendation, what size do you wear and what is your budget?",
    "If you'd like a different colour, tell me which, and what size you need.",
    "If you want me to narrow it down, what will you use it for?",
    "If you’d like a quick-dry one, what is the weather like where you hike?",
    "Which size do you need, and if you'd like a colour, which one?",
    "What size do you need? If you'd like one for layering, tell me.",
    "Can you tell me your size and what you will use it for?",
    "Could you tell me your budget so I can narrow it down?",
    "So I can find the right fit, what is your chest measurement?",
    "What size do you need? Then I can recommend one.",
    "Once I know your size I can then give you a recommendation.",
    "Tell me your budget and I can then narrow the choices.",
    "Tell me your size and I can then suggest a few that fit.",
    "I can then give you a few ideas once I know your budget.",
    "I can also help you pick a colour. What size do you need?",
    "Do you need to know the price before you decide? What is your budget?",
]


@pytest.mark.req("SA-04")
@pytest.mark.parametrize("text", PASSING_QUESTIONS)
def test_a_clarifying_question_passes_untouched_gaps(text: str) -> None:
    assert hits(text) == {}, text
    assert check_text(text) == text


@pytest.mark.req("SA-04")
@pytest.mark.parametrize("text", PASSING_QUESTIONS[:12])
def test_a_clarifying_question_passes_when_it_arrives_in_tokens(text: str) -> None:
    for step in (1, 2, 5):
        assert streamed(text, step) == text
    assert by_words(text) == text
    assert every_split(text) == {text}


@pytest.mark.req("SA-04")
def test_a_clarifying_question_beside_a_dropped_offer_keeps_the_question() -> None:
    text = "What size do you need? If you'd like a price or stock check, tell me the size."
    assert check_text(text).strip() == "What size do you need?"
    assert streamed(text, 1).strip() == "What size do you need?"


@pytest.mark.req("SA-04")
def test_a_multi_line_clarifying_answer_passes() -> None:
    text = "To pick one I need two things:\n- What size is the bed?\n- What is your budget?"
    assert check_text(text) == text
    assert streamed(text, 1) == text
    assert every_split(text) == {text}
