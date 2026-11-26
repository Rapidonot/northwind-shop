"""Row 18a, part 2 (hard rule 1, PAY-05): a security code written digit by digit with punctuation
("1, 2, 3") passed `contains_cvv` (triage BE-07 F3). Failing first. A widening only: every code
found before is found, and a list of sizes near the word stays clean. Row #27 PR 2's security L4 (a
card split between a well-formed order number and an all-digit key) waits on the user's decision
D2 (`docs/reviews/2026-11-26-18a-decisions.md`) and is pinned here as a strict xfail."""

from __future__ import annotations

import pytest

from northwind_common.cards import contains_card_data, contains_cvv, holds_card_data

pytestmark = pytest.mark.req("PAY-05")


@pytest.mark.parametrize(
    "text",
    [
        "CVV 1, 2, 3",
        "My CVV is 1, 2, 3.",
        "security code: 1,2,3",
        "cvc 1. 2. 3",
        "The card code is 1/2/3/4",
        "1; 2; 3 is the CVV",
        "CVV 1, 2, 3, thanks",
    ],
)
def test_a_code_written_digit_by_digit_with_punctuation_is_card_data(text: str) -> None:
    assert contains_cvv(text)
    assert contains_card_data(text)
    assert holds_card_data({"note": text})


@pytest.mark.parametrize(
    "text",
    [
        "Where is the CVV? I take a 10, 12 or 14.",
        "CVV: sizes 1, 2",
        "CVV 1, 2, 3, 4, 5",
        "CVV 1, 2, 34",
        "CVV 12, 3, 4",
        "The CVV, 1.5 m long",
        "Wash at 1, 2 or 3 bars of pressure.",  # no security-code name
    ],
)
def test_a_near_miss_is_no_card_data(text: str) -> None:
    assert not contains_cvv(text)


@pytest.mark.parametrize("text", ["CVV 737", "cvv: 7 3 7", "CVC 1234", "7-3-7 is the CVV"])
def test_the_codes_found_before_are_still_found(text: str) -> None:
    assert contains_cvv(text)


@pytest.mark.xfail(
    strict=True,
    reason="Row #27 PR 2 security L4: waits on the user's decision D2 (the pair check's pin)",
)
def test_a_card_split_between_an_order_number_and_a_digit_key_is_card_data() -> None:
    assert holds_card_data({"order_id": "O411111", "idempotency_key": "1111111111"})
