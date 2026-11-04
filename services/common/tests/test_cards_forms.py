"""Other ways a shopper might write a Luhn-valid card number (SA-02; hard rule 1)."""

from __future__ import annotations

import pytest

from northwind_common.cards import MAX_PAN_DIGITS, MIN_PAN_DIGITS, contains_pan, luhn_ok

pytestmark = pytest.mark.req("SA-02")

CARD = "4242" * 4  # the sandbox test card
GROUPS = [CARD[i : i + 4] for i in range(0, 16, 4)]


def with_check_digit(body: str) -> str:
    """A synthetic number of the right length that passes Luhn."""
    for check in "0123456789":
        if luhn_ok(body + check):
            return body + check
    raise AssertionError("unreachable")


@pytest.mark.parametrize("length", range(MIN_PAN_DIGITS, MAX_PAN_DIGITS + 1))
def test_every_card_length_is_found(length: int) -> None:
    number = with_check_digit("4" * (length - 1))
    assert len(number) == length and contains_pan(number)


@pytest.mark.parametrize(
    "separator",
    [".", "/", "  ", "\t", "\n", "\u00a0", " - ", "\u2013"],
    ids=["dot", "slash", "two-spaces", "tab", "newline", "nbsp", "spaced-dash", "en-dash"],
)
def test_a_card_is_found_with_any_separator(separator: str) -> None:
    assert contains_pan(separator.join(GROUPS))


@pytest.mark.parametrize(
    "digits",
    ["４２４２" * 4, "٤٢٤٢" * 4],
    ids=["fullwidth", "arabic-indic"],
)
def test_a_card_in_other_scripts_is_found(digits: str) -> None:
    assert contains_pan(digits)


@pytest.mark.parametrize(
    "text",
    [f"card: {CARD}.", f"({CARD})", f"#{CARD}", f"a{CARD}b", f"{CARD}0000000000", f"9{CARD}"],
)
def test_a_card_next_to_other_characters_is_found(text: str) -> None:
    assert contains_pan(text)


def test_a_card_in_a_long_message_is_found() -> None:
    assert contains_pan("x " * 900 + CARD)


@pytest.mark.parametrize(
    "text",
    ["4" * 12, "1234 5678 9012", "2026-11-04 15:51:45", "P0011 P0012 P0013", "+1 555 0100 0199"],
)
def test_short_or_ordinary_digits_pass(text: str) -> None:
    assert not contains_pan(text)
