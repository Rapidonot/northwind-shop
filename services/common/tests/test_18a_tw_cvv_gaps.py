"""Row 18a, part 2, gap tests (test-writer): a security code written as single digits joined by
comma, dot, semicolon or slash, next to a security-code name. Synthetic text only."""

from __future__ import annotations

import pytest

from northwind_common.cards import contains_card_data, contains_cvv, holds_card_data

pytestmark = pytest.mark.req("PAY-05")

NAMES = ["CVV", "cvc", "CVV2", "security code", "Security Code:", "card code"]
JOINERS = [",", ", ", ".", ". ", ";", "; ", "/"]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("joiner", JOINERS)
def test_three_single_digits_after_the_name_are_card_data(name: str, joiner: str) -> None:
    text = f"{name} 7{joiner}3{joiner}7"
    assert contains_cvv(text), text
    assert contains_card_data(text)


@pytest.mark.parametrize("joiner", [",", ".", ";", "/"])
def test_four_single_digits_are_card_data(joiner: str) -> None:
    assert contains_cvv(f"my cvv is 1{joiner}2{joiner}3{joiner}4")


@pytest.mark.parametrize("joiner", [",", ".", ";", "/"])
def test_the_name_may_come_after_the_digits(joiner: str) -> None:
    assert contains_cvv(f"7{joiner}3{joiner}7 is the security code")


def test_a_code_in_tool_arguments_is_card_data() -> None:
    assert holds_card_data({"note": "cvv 7, 3, 7"})
    assert holds_card_data({"items": ["hello", "security code 7/3/7"]})


@pytest.mark.parametrize("joiner", [",", ".", ";", "/"])
@pytest.mark.parametrize("name", ["CVV", "security code"])
def test_a_list_of_multi_digit_numbers_stays_clean(name: str, joiner: str) -> None:
    for numbers in ("10{j}12{j}14", "12{j}34{j}56", "30{j}32{j}34{j}36"):
        text = f"{name} {numbers.format(j=joiner)}"
        assert not contains_cvv(text), text


@pytest.mark.parametrize(
    "text",
    [
        "The CVV is on the back of the card.",
        "Sizes 1, 2 and 3 are listed.",
        "security code policy: 10, 12, 14 and 16",
        "CVV 1, 2",
    ],
)
def test_other_text_stays_clean(text: str) -> None:
    assert not contains_cvv(text)


@pytest.mark.xfail(
    strict=True,
    reason="18a tw: a slash with a space either side ('cvv 7 / 3 / 7') is not read as a code",
)
@pytest.mark.parametrize("name", ["CVV", "security code"])
def test_a_slash_with_spaces_either_side_is_card_data(name: str) -> None:
    assert contains_cvv(f"{name} 7 / 3 / 7")
