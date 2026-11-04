"""Card-number detection (SA-02; hard rule 1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_common.cards import contains_card_data, contains_cvv, contains_pan, luhn_ok

pytestmark = pytest.mark.req("SA-02")

CARD = "4242" * 4  # the sandbox test card, built here so no scan of source files trips on it
REPO = Path(__file__).resolve().parents[3]
PRD_EXAMPLE = "a waterproof jacket for light spring hiking under $150 that fits a wide frame"


@pytest.mark.parametrize(
    "text",
    [
        CARD,
        f"my card is {CARD} thanks",
        " ".join(CARD[i : i + 4] for i in range(0, 16, 4)),
        "-".join(CARD[i : i + 4] for i in range(0, 16, 4)),
        f"{CARD[:4]}-{CARD[4:8]} {CARD[8:12]}-{CARD[12:]}",
        f"{CARD[:4]} {CARD[4:10]} {CARD[10:]}",
        f"1 {CARD}",
        f"12345 {CARD} 678",
    ],
)
def test_a_card_number_is_found_however_it_is_written(text: str) -> None:
    assert contains_pan(text)


@pytest.mark.parametrize(
    "text",
    ["", "Do you have the P0011 jacket in size 42?", "order 12345", "1234567890", "call 555 0100"],
)
def test_ordinary_text_passes(text: str) -> None:
    assert not contains_pan(text)


@pytest.mark.parametrize(
    "gap", ["    ", "\r\n\r\n", " then ", " and then ", ", next: ", "\t\t\t\t"]
)
def test_a_card_is_found_with_wide_gaps_between_its_groups(gap: str) -> None:
    assert contains_pan(gap.join(CARD[i : i + 4] for i in range(0, 16, 4)))


@pytest.mark.parametrize("digit_map", [str.maketrans("24", "②④"), str.maketrans("24", "²⁴")])
def test_a_card_in_other_digit_forms_is_found(digit_map: dict[int, str]) -> None:
    assert contains_pan(CARD.translate(digit_map))


@pytest.mark.parametrize(
    "text",
    [
        "Compare P0011, P0012, P0013 and P0014",
        "sizes 10, 12, 14, 16, 18, 20, 22",
        "R00056 and R00057 and R00059",
    ],
)
def test_product_ids_and_size_lists_are_not_cards(text: str) -> None:
    assert not contains_pan(text)


@pytest.mark.parametrize(
    "text",
    [
        "card ending 4242, CVV 737, exp 12/28",
        "cvv: 737",
        "CVC2 1234",
        "my security code is 737",
        "737 is my cvv",
        "the card verification value is 737",
        "CVV ７３７",
        "CVV 7 3 7",
        "CVV 7-3-7",
        "CV2 737",
        "CID 1234",
        "CVN 737",
        "security-code 737",
        "my cvv for this card is printed as 737",
        "cvv on card 2 is 737",
        "cvv_737",
    ],
)
def test_a_security_code_is_found(text: str) -> None:
    assert contains_cvv(text) and contains_card_data(text)


@pytest.mark.parametrize(
    "text",
    [
        "Where do I find the CVV?",
        "What's a security code for?",
        "737 boots",
        "CVV 12345",
        "size 42",
    ],
)
def test_talk_about_security_codes_is_not_card_data(text: str) -> None:
    assert not contains_cvv(text)


def test_no_real_shopper_question_is_refused() -> None:
    """The wider net is only worth having if it leaves real questions alone."""
    lines = (REPO / "evals/fixtures/starter_set.jsonl").read_text(encoding="utf-8").splitlines()
    items = [json.loads(line) for line in lines]
    assert len(items) >= 100
    refused = [item["id"] for item in items if contains_card_data(item["query"])]
    assert refused == []


def test_the_prd_example_is_not_refused() -> None:
    assert PRD_EXAMPLE in (REPO / "docs/PRD.md").read_text(encoding="utf-8")
    assert not contains_card_data(PRD_EXAMPLE)


def test_luhn() -> None:
    assert luhn_ok(CARD) and not luhn_ok(CARD[:-1] + "3")
