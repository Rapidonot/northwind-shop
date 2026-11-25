"""SA-04 and SA-03 scorers (rows #30, #22), from the frozen guide's scoring rules
(docs/reviews/2026-11-25-30-labelling-guide.md). Failing first. Synthetic answers only."""

from __future__ import annotations

from typing import Any

import pytest

from northwind_assistant.output_check import FIXED_SENTENCES
from northwind_evals.intents import asking_text, has_wording, sa03_problems, sa04_problems

CL = {"accepts": ["size", "budget"], "id": "CL-X"}
USE = {"accepts": ["use"], "id": "CL-Y"}


def ic(**kw: Any) -> dict[str, Any]:
    base = {
        "absent": [],
        "cites": [],
        "facts": [],
        "id": "IC-X",
        "intent": "product_question",
        "query": "q",
        "region": "US",
        "variants": [],
    }
    return {**base, **kw}


# --- SA-04 ----------------------------------------------------------------------------------------


@pytest.mark.req("SA-04")
@pytest.mark.parametrize(
    "answer",
    [
        "What size do you need?",
        "Which sizes do you wear?",
        "What's your budget?",
        "Happy to help. Roughly how much would you like to spend?",
        "How much are you hoping to pay?",
        "Tell me your chest measurement.",
        "Could you tell me:\n\n- your size\n- the colour",
        "Let me know a few things:\n- Waist\n- Colour",
        "**What size** bed is it for?",
        "How long is your table, or how many does it seat?",
        "How many guests are coming?",
        "The guide lists three sizes [SG-TOWELS]. Which size do you want?",
    ],
)
def test_asking_about_an_accepted_slot_passes(answer: str) -> None:
    assert sa04_problems(CL, answer) == []


@pytest.mark.req("SA-04")
@pytest.mark.parametrize("answer", ["What will you use it for?", "How warm do you sleep?"])
def test_the_slot_has_to_be_one_the_label_accepts(answer: str) -> None:
    assert sa04_problems(USE, answer) == []
    assert sa04_problems(CL, answer) == ["no question about size or budget"]


@pytest.mark.req("SA-04")
@pytest.mark.parametrize(
    "answer",
    [
        "It depends on your size and budget.",  # says it, asks nothing
        "What colour do you like?",
        "Fits true to size. What colour do you like?",  # the word is outside the question
        "Is it oversized?",  # whole words
        "- size\n- budget",  # a list under no question
    ],
)
def test_no_question_about_the_slot_fails(answer: str) -> None:
    assert sa04_problems(CL, answer) == ["no question about size or budget"]


@pytest.mark.req("SA-04")
@pytest.mark.parametrize(
    "answer",
    [
        "Get the Tidewick Harbor Rain Jacket. What size do you need?",
        "What size do you need, for example the Tidewick Harbor?",
        "Is the **Tidewick Harbor** your size?",
        "Is the Tidewick **Harbor** your size?",
        "This one is waterproof [P0001]. What size do you need?",
        "This one is waterproof [P0001-BLK-REG-L; R00001]. What size do you need?",
    ],
)
def test_naming_or_citing_a_product_fails_even_in_the_question(answer: str) -> None:
    assert sa04_problems(CL, answer) == ["names or cites a product"]


@pytest.mark.req("SA-04")
def test_a_brand_alone_and_a_review_or_guide_citation_are_not_a_product() -> None:
    answer = "Tidewick has several [SG-OUTERWEAR][R00001]. What size do you need?"
    assert sa04_problems(CL, answer) == []


@pytest.mark.req("SA-03")
@pytest.mark.req("SA-04")
@pytest.mark.parametrize(
    "answer", ["", "  \n", FIXED_SENTENCES["stock"], f"{FIXED_SENTENCES['money']} \n"]
)
def test_an_empty_answer_or_fixed_sentences_alone_fail(answer: str) -> None:
    assert sa04_problems(CL, answer) == ["no answer"]
    assert sa03_problems(ic(), answer, (), {}, None) == ["no answer"]


@pytest.mark.req("SA-04")
def test_list_lines_under_a_question_are_part_of_it_and_later_text_is_not() -> None:
    text = "Could you tell me:\n- your size\n* your budget\n\nSizes run small."
    assert "your budget" in asking_text(text)
    assert "run small" not in asking_text(text)


# --- SA-03 ----------------------------------------------------------------------------------------


@pytest.mark.req("SA-03")
@pytest.mark.parametrize(
    ("text", "wording", "found"),
    [
        ("Rated to **20,000 mm**.", "20000 mm", True),
        ("Rated to 20000 mm.", "20,000 mm", True),
        ("It takes 4–7 business days.", "4-7 business days", True),
        ("It doesn’t come in King.", "doesn't come", True),
        ("Pockets: 18″ deep.", '18"', True),
        ("It is 2XL.", "XL", False),
        ("It is 2500 mm.", "500", False),
        ("It has\n  organic   cotton.", "organic cotton", True),
    ],
)
def test_wordings_are_whole_words_on_folded_text(text: str, wording: str, found: bool) -> None:
    assert has_wording(text, wording) is found


@pytest.mark.req("SA-03")
def test_every_cite_and_fact_group_is_needed_and_no_absent_wording() -> None:
    item = ic(cites=[["P0001", "CG-X"], ["P0002"]], facts=[["420 g"], ["wool"]], absent=["60 days"])
    good = "It weighs 420 g and is wool."
    assert sa03_problems(item, good, ("CG-X", "P0002"), {}, None) == []
    assert sa03_problems(item, good, ("CG-X",), {}, None) == ["cites none of P0002"]
    assert sa03_problems(item, "It weighs 420 g.", ("P0001", "P0002"), {}, None) == [
        "no fact: wool"
    ]
    assert sa03_problems(item, good + " You have 60 days.", ("P0001", "P0002"), {}, None) == [
        "says: 60 days"
    ]


@pytest.mark.req("SA-03")
def test_a_citation_is_the_labelled_id_itself() -> None:
    item = ic(cites=[["P0001"]])
    assert sa03_problems(item, "Yes.", ("P0001-BLK-REG-L",), {}, None) == ["cites none of P0001"]


@pytest.mark.req("SA-03")
def test_a_recommendation_that_only_asks_fails() -> None:
    item = ic(intent="recommendation", cites=[["P0001"]])
    assert sa03_problems(item, "Which colour?", ("P0001",), {}, None) == ["only a question"]
    assert sa03_problems(item, "Get this one. Which colour?", ("P0001",), {}, None) == []
    only = "Could you tell me:\n- your size"
    assert sa03_problems(item, only, ("P0001",), {}, None) == ["only a question"]


V = "P0001-FOR-REL-L"
AV = ic(intent="availability", variants=[V])


@pytest.mark.req("SA-03")
@pytest.mark.parametrize(
    "answer",
    [
        "Yes, it's in stock.",
        "It is available.",
        "There are 7 left.",
        "Yes, 7 units.",
        "It isn't cheap; it is in stock.",
    ],
)
def test_a_variant_in_stock_needs_an_in_stock_answer(answer: str) -> None:
    assert sa03_problems(AV, answer, (), {V: 7}, 7) == []
    assert sa03_problems(AV, answer, (), {V: 0}, 0) == ["does not say it is out of stock"]


@pytest.mark.req("SA-03")
@pytest.mark.parametrize(
    "answer",
    [
        "It is out of stock.",
        "Sold out, sorry.",
        "It's unavailable.",
        "It isn't in stock.",
        "It is not currently available.",
        "It's no longer in stock.",
    ],
)
def test_a_variant_at_zero_needs_an_out_of_stock_answer(answer: str) -> None:
    assert sa03_problems(AV, answer, (), {V: 0}, 0) == []
    assert sa03_problems(AV, answer, (), {V: 7}, 7) == ["does not say it is in stock"]


@pytest.mark.req("SA-03")
def test_a_wrong_count_is_not_the_live_one() -> None:
    assert sa03_problems(AV, "There are 70 left.", (), {V: 7}, 7) == ["does not say it is in stock"]


@pytest.mark.req("SA-03")
def test_availability_needs_check_stock_for_that_variant_this_turn() -> None:
    assert sa03_problems(AV, "It is in stock.", (), {}, 7) == [f"check_stock not called for {V}"]
    other = {"P0001-BLK-REL-L": 7}
    assert sa03_problems(AV, "It is in stock.", (), other, 7) == [f"check_stock not called for {V}"]


@pytest.mark.req("SA-03")
def test_a_combination_that_does_not_exist_fails_as_merely_out_of_stock() -> None:
    item = ic(
        intent="availability",
        cites=[["P0141"]],
        facts=[["doesn't come"]],
        absent=["out of stock"],
    )
    assert sa03_problems(item, "It doesn't come in Ivory King.", ("P0141",), {}, None) == []
    assert sa03_problems(item, "It's out of stock.", ("P0141",), {}, None) == [
        "no fact: doesn't come",
        "says: out of stock",
    ]
