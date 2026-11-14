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
