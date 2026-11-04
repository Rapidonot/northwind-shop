"""Card-number detection, shared by the chat endpoint and the cassette scanner (hard rule 1)."""

from __future__ import annotations

import re

MIN_PAN_DIGITS = 13
MAX_PAN_DIGITS = 19

# Digits joined by at most one space or dash, as a shopper might paste them.
_DIGIT_GROUP = re.compile(r"\d(?:[ -]?\d)*")


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def contains_pan(text: str) -> bool:
    """True if any 13-19 digit stretch in `text` passes Luhn, whatever its grouping. Fails closed:
    a long digit string can be refused by chance, which costs a retry, not a card number."""
    for group in _DIGIT_GROUP.findall(text):
        digits = re.sub(r"\D", "", group)
        for size in range(MIN_PAN_DIGITS, min(MAX_PAN_DIGITS, len(digits)) + 1):
            for start in range(len(digits) - size + 1):
                if luhn_ok(digits[start : start + size]):
                    return True
    return False
