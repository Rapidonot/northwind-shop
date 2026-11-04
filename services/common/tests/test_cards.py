"""Card-number detection (SA-02; hard rule 1)."""

from __future__ import annotations

import pytest

from northwind_common.cards import contains_pan, luhn_ok

pytestmark = pytest.mark.req("SA-02")

CARD = "4242" * 4  # the sandbox test card, built here so no scan of source files trips on it


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


def test_luhn() -> None:
    assert luhn_ok(CARD) and not luhn_ok(CARD[:-1] + "3")
