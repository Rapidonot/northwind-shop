"""Row #12, security review pass 1 (`docs/reviews/2026-11-14-12-security-review.raw.md`): every
example the reviewer gave for findings 1-4 and 7, as tests (RAG-09, hard rule 2)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from northwind_assistant.output_check import (
    FIXED_SENTENCES,
    LiveValues,
    check_text,
    hits,
    live_backed,
)

pytestmark = pytest.mark.req("RAG-09")

MONEY = FIXED_SENTENCES["money"]
STOCK = FIXED_SENTENCES["stock"]


# ---- finding 1 (high): money formats ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "It costs USD 279.",  # code before the amount
        "It is EUR 130.66 now.",
        "It is CAD 189.",
        "It costs 130.66 €.",  # sign after the amount
        "It costs 130,66 €.",
        "It costs 279$.",
        "It costs 199 pounds.",  # digits and "pounds", not about weight
        "The price is 279.00.",  # a bare amount said to be a price
        "It costs 279.",
        "It is priced at 279.",
        "Fifteen hundred dollars is the price.",  # size words no parser here reads
        "It is one thousand dollars.",
        "It is a couple hundred bucks.",
        "It is 1.5k dollars.",
        "It is €9,99 now.",  # a decimal comma is not read: blocked, not skipped
        "It is $1,29 now.",
        "It is $**279** today.",  # markdown inside
        "It is **279** USD.",
        "It is NZ$279.",  # a dollar the catalog never returns
        "It is HK$279.",
    ],
)
def test_every_price_format_is_money_and_blocked_without_a_live_price(text: str) -> None:
    assert hits(text) == {"money": 1}
    assert MONEY in check_text(text)


@pytest.mark.parametrize(
    ("text", "price"),
    [
        ("It costs USD 279.", (Decimal("279.00"), "USD")),
        ("It costs 130.66 €.", (Decimal("130.66"), "EUR")),
        ("It costs EUR 130.66.", (Decimal("130.66"), "EUR")),
        ("It costs 199 pounds.", (Decimal("199.00"), "GBP")),
        ("The price is 279.00.", (Decimal("279.00"), "USD")),
        ("It is priced at 279.", (Decimal("279.00"), "USD")),
        ("It is $**279** today.", (Decimal("279.00"), "USD")),
        ("It is AU$279.", (Decimal("279.00"), "AUD")),
    ],
)
def test_each_format_passes_when_it_equals_a_live_price(
    text: str, price: tuple[Decimal, str]
) -> None:
    assert hits(text, live=LiveValues(prices={price})) == {}


def test_a_narrowed_dollar_does_not_match_another_dollar() -> None:
    assert hits("It is AU$279.", live=LiveValues(prices={(Decimal("279.00"), "USD")})) == {
        "money": 1
    }


@pytest.mark.parametrize("text", ["It weighs 2 pounds.", "It weighs about 2.1 pounds."])
def test_pounds_of_weight_are_not_money(text: str) -> None:
    assert hits(text) == {}


def test_a_price_word_after_the_weight_makes_pounds_money_again() -> None:
    assert hits("It weighs little and costs 199 pounds.") == {"money": 1}


def test_a_sentences_full_stop_is_not_part_of_the_amount() -> None:
    live = LiveValues(prices={(Decimal("250.00"), "USD")})
    assert check_text("It costs $5. It costs $250. Fine.", live=live) == (
        f"{MONEY} It costs $250. Fine."
    )


# ---- finding 2 (high): asking words that do not govern the clause -------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "If you need it soon the Sage M is available now.",
        "Not sure if you saw but M is available.",
        "Check if it fits and XL is available today.",
        "Check whether it fits and all sizes are available.",
        "Not sure if you like it so Sage is available.",
        "Whether you hike or commute the M is still available.",
        "The size which you asked about is available now.",
        "Here is which size is available now: the M.",
        "Is it here if it fits?The M is available now.",
        "See whether it fits because the M is available.",
        "Tell me if it fits, then the M is available.",
    ],
)
def test_an_asking_word_without_an_embedded_question_leaves_a_claim(text: str) -> None:
    assert check_text(text) == STOCK


@pytest.mark.parametrize(
    "text",
    [
        "Check the product page to see whether that size is available right now.",
        "The live product page will show whether Glacier Blue is available in 4XL right now.",
        "I can't see stock levels, so check the live product page to see whether the M is "
        "available now.",
        "The live product page shows which size and colour combinations are available, and it "
        "also has current stock.",
        "I couldn't find anything on compensation for late deliveries, or on what care agents are "
        "told to offer, so I can't say whether any is available.",
    ],
)
def test_the_five_measured_questions_still_pass(text: str) -> None:
    assert hits(text) == {}


# ---- finding 3 (medium): negated stock claims ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["The M isn't in stock right now.", "The M is not available now.", "It is no longer in stock."],
)
def test_a_negated_stock_claim_follows_the_reversed_polarity(text: str) -> None:
    assert check_text(text, live=LiveValues(stock={"v": 5})) == STOCK
    assert check_text(text, live=LiveValues(stock={"v": 0})) == text


def test_a_negated_count_never_passes() -> None:
    assert check_text("There are not 3 left.", live=LiveValues(stock={"v": 3})) == STOCK


# ---- finding 4 (medium): quantities the count pattern could not read -----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "There are a dozen left.",
        "Only a few left!",
        "There are only a handful left.",
        "There are a couple left.",
        "There are plenty in stock.",
    ],
)
def test_a_quantity_no_tool_returns_is_always_blocked(text: str) -> None:
    assert check_text(text, live=LiveValues(stock={"v": 12})) == STOCK


@pytest.mark.parametrize(
    ("text", "on_hand"),
    [
        ("There are 12345 left.", 12345),
        ("There are 10000 in stock.", 10000),
        ("There are 1,200 units available.", 1200),
    ],
)
def test_a_long_count_is_read_whole(text: str, on_hand: int) -> None:
    assert check_text(text) == STOCK
    assert check_text(text, live=LiveValues(stock={"v": on_hand})) == text
    assert check_text(text, live=LiveValues(stock={"v": on_hand % 1000})) == STOCK


# ---- finding 7 (low): a question is not a live-backed claim --------------------------------------


def test_a_question_about_availability_is_not_a_live_backed_claim() -> None:
    live = LiveValues(stock={"v": 4})
    assert live_backed("I can check whether the S is available too.", live) == 0
    assert live_backed("The M is available now.", live) == 1


# ==== the scoped re-review (`docs/reviews/2026-11-14-12-security-rereview.raw.md`) ================

# ---- N1 (high, a regression): "which ... and" only as a noun phrase ------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Check which fits you best and the M is available now.",
        "See which colour you prefer and the M is available today.",
        "Check which size fits and it is available now.",
    ],
)
def test_n1_which_and_with_a_verb_or_subject_is_a_claim(text: str) -> None:
    assert check_text(text) == STOCK


# ---- N2 (high): a word between the amount and the currency ---------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The Summit Shell is 279 Canadian dollars.",
        "It is 279 US dollars.",
        "It is 49 Australian dollars.",
        "It is 199 British pounds.",
        "It is 279 US$.",
        "It is 279 (USD).",
        "It is ¥40,000.",
        "It is CHF 279.",
        "It is 279 kr.",
        "It is 279 yen.",
        "It is ₹2,500.",
    ],
)
def test_n2_a_qualified_or_foreign_price_is_money(text: str) -> None:
    assert hits(text) == {"money": 1}


@pytest.mark.parametrize(
    ("text", "price"),
    [
        ("It is 279 Canadian dollars.", (Decimal("279.00"), "CAD")),
        ("It is 279 US dollars.", (Decimal("279.00"), "USD")),
        ("It is 199 British pounds.", (Decimal("199.00"), "GBP")),
        ("It is 279 (USD).", (Decimal("279.00"), "USD")),
    ],
)
def test_n2_a_qualified_price_passes_only_in_its_own_currency(
    text: str, price: tuple[Decimal, str]
) -> None:
    assert hits(text, live=LiveValues(prices={price})) == {}
    other = (price[0], "AUD")
    assert hits(text, live=LiveValues(prices={other})) == {"money": 1}


def test_n2_a_us_price_does_not_vouch_canadian_dollars() -> None:
    live = LiveValues(prices={(Decimal("279.00"), "USD")})
    assert hits("The price is 279 Canadian dollars.", live=live) == {"money": 1}


# ---- N3 (medium, a regression): only a governing negation flips polarity -------------------------


@pytest.mark.parametrize(
    "text", ["There's no doubt the M is in stock.", "Never mind, it is in stock now."]
)
def test_n3_a_negation_that_does_not_govern_blocks_at_any_stock(text: str) -> None:
    if text.startswith("Never mind,"):  # the comma ends the clause: a plain claim, checked as one
        assert check_text(text, live=LiveValues(stock={"v": 0})) == STOCK
        assert check_text(text, live=LiveValues(stock={"v": 5})) == text
        return
    assert check_text(text, live=LiveValues(stock={"v": 0})) == STOCK
    assert check_text(text, live=LiveValues(stock={"v": 5})) == STOCK


@pytest.mark.parametrize(
    "text", ["No worries — the M is in stock.", "Don't wait — the M is available now."]
)
def test_n3_a_dash_ends_the_negations_clause(text: str) -> None:
    # The claim after the dash stands alone: false at zero, true above it.
    assert check_text(text, live=LiveValues(stock={"v": 0})) == STOCK
    assert check_text(text, live=LiveValues(stock={"v": 5})) == text


# ---- N4 (medium): a full clause between the asking word and "available" --------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Not sure if you noticed the M is available now.",
        "Let me know if you need it today the M is available now.",
        "Check whether it fits (the M is available now).",
    ],
)
def test_n4_another_clause_after_the_asking_word_leaves_a_claim(text: str) -> None:
    assert check_text(text) == STOCK


def test_n4_a_pronoun_as_the_questions_own_subject_still_asks() -> None:
    assert hits("Check whether it is available in your region.") == {}


# ---- N5 (medium): only a weight verb or noun governing the figure makes pounds a weight ----------


@pytest.mark.parametrize(
    "text",
    [
        "This lightweight shell is just 149 pounds.",
        "Weighing just 400 g, it's 149 pounds.",
        "It weighs 2 pounds and sells for 199 pounds.",
    ],
)
def test_n5_a_price_near_a_weight_word_is_still_money(text: str) -> None:
    assert hits(text) == {"money": 1}


@pytest.mark.parametrize(
    "text", ["It weighs about 2.1 pounds.", "Its weight is 3 pounds.", "It weighs in at 2 pounds."]
)
def test_n5_a_governed_weight_is_not_money(text: str) -> None:
    assert hits(text) == {}


# ---- N6 (medium): a hedge word between the price word and the amount -----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "It costs just 279.",
        "The price is now 279.00.",
        "It's priced at only 279.",
        "The price is around 280.",
    ],
)
def test_n6_a_hedged_bare_price_is_money(text: str) -> None:
    assert hits(text) == {"money": 1}


# ---- N7 (low): more quantity words ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "There are still some left.",
        "There are a lot left.",
        "There are a few sizes left.",
        "There are tons in stock.",
        "It is limited stock.",
    ],
)
def test_n7_more_quantities_are_blocked(text: str) -> None:
    assert check_text(text, live=LiveValues(stock={"v": 12})) == STOCK


# ---- N9 (low): a colon after "available" is seen even when it arrives late ---------------------


def test_n9_a_late_colon_after_a_question_is_still_seen() -> None:
    """The colon arrives 57 characters after "available", past the trigger's hold window, with no
    other trigger in between (measured: 55-60 leaked before the fix)."""
    from northwind_assistant.output_check import OutputCheck

    filler = " and see the page where it shows up for you there xxxxxxx"[:57]
    text = "Check whether the M is available" + filler + ": it is."
    check = OutputCheck(set(), [])
    sent: list[str] = []
    for i in range(0, len(text), 3):
        sent += [p for p in check.feed(text[i : i + 3]) if p is not None]
    sent += [p for p in check.finish() if p is not None]
    assert STOCK in check.shown
    assert "available" not in "".join(sent)


# ==== the second scoped re-review (`docs/reviews/2026-11-14-12-security-rereview-2.raw.md`) ======
# Only its two regressions against d4e1c4f are fixed in this row (the user's rule: lower findings
# are logged; the highs H1-H3 stop the chain).

# ---- M1 (medium, a regression from N6): "will be" and markdown before a bare price ---------------


@pytest.mark.parametrize(
    "text", ["The price will be 279.", "The cost will be 279.", "The price is ***279***."]
)
def test_m1_bare_price_forms_blocked_before_n6_stay_blocked(text: str) -> None:
    assert hits(text) == {"money": 1}


# ---- M2 (medium, a regression from N3): a governing negation across an aside --------------------


@pytest.mark.parametrize(
    "text",
    [
        "The M is not (yet) in stock.",
        # The reviewer's "The M isn't (currently) available." matches no pattern, at d4e1c4f too:
        # "available" after anything but a literal "is"/"are" is H3, open (stop 10).
        "The M isn't (yet) available now.",
        "The M is not — for now — in stock.",
        "The M is not, sadly, in stock.",
    ],
)
def test_m2_a_negation_across_an_aside_is_unvouched(text: str) -> None:
    assert check_text(text, live=LiveValues(stock={"v": 5})) == STOCK
    assert check_text(text, live=LiveValues(stock={"v": 0})) == STOCK


def test_m2_a_negation_in_another_clause_still_leaves_a_plain_claim() -> None:
    text = "No worries — the M is in stock."
    assert check_text(text, live=LiveValues(stock={"v": 5})) == text
