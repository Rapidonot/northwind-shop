"""Card-data detection for free text (hard rule 1, PAY-05).

This is a net for mistakes, not the control: card entry belongs in the provider's hosted fields. It
fails closed, so a long digit string or a run of numbers can be refused by chance, which costs a
retry, not a card number. It cannot catch digits spelled out or broken up by letters.

The cassette scanner shares only `luhn_ok`: its own patterns are narrower, because recordings are
full of numbers that aren't cards.
"""

from __future__ import annotations

import re
import unicodedata

MIN_PAN_DIGITS = 13
MAX_PAN_DIGITS = 19
MIN_GROUP_DIGITS = 3  # cards print in groups of 3 or more, which keeps "10, 12, 14" out
MAX_GROUP_GAP = 12

# Digits joined by at most one non-alphanumeric character: a dot, slash, any dash, tab, newline,
# no-break or zero-width space. Wider gaps go through `_wide_groups_pass`, or lists like
# "10, 12, 14" would join up here and get many chances to pass Luhn by accident.
_DIGIT_GROUP = re.compile(r"\d(?:[\W_]?\d)*")
# A digit run with no letter or digit touching it, so P0011 is a product and not a card group.
_STANDALONE_RUN = re.compile(r"(?<![^\W_])\d+(?![^\W_])")

_SEP = r"[\s_-]*"
_CVV_NAME = (
    r"(?<![^\W_])(?:cvv2?|cvc2?|cv2|cvn|cid|csc"
    rf"|security{_SEP}code|card{_SEP}(?:code|verification{_SEP}(?:code|value)))(?![^\W_])"
)
# A code may be spaced out, like "7 3 7".
_CVV_DIGITS = r"(?<!\d)\d(?:[ -]?\d){2,3}(?!\d)"
MAX_CVV_GAP = 40
_CVV = re.compile(
    rf"{_CVV_NAME}[\s\S]{{0,{MAX_CVV_GAP}}}?{_CVV_DIGITS}"
    rf"|{_CVV_DIGITS}[\s\S]{{0,{MAX_CVV_GAP}}}?{_CVV_NAME}",
    re.IGNORECASE,
)


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


def _any_window_passes(digits: str) -> bool:
    for size in range(MIN_PAN_DIGITS, min(MAX_PAN_DIGITS, len(digits)) + 1):
        for start in range(len(digits) - size + 1):
            if luhn_ok(digits[start : start + size]):
                return True
    return False


def _wide_groups_pass(text: str) -> bool:
    """Standalone groups further apart, such as `4242 then 4242 then 4242 then 4242`. Only whole
    groups count here, since these spans are loose enough to join unrelated numbers."""
    runs = [m for m in _STANDALONE_RUN.finditer(text) if len(m.group()) >= MIN_GROUP_DIGITS]
    for first, run in enumerate(runs):
        digits = run.group()
        for previous, following in zip(runs[first:], runs[first + 1 :], strict=False):
            if following.start() - previous.end() > MAX_GROUP_GAP:
                break
            digits += following.group()
            if len(digits) > MAX_PAN_DIGITS:
                break
            if len(digits) >= MIN_PAN_DIGITS and luhn_ok(digits):
                return True
    return False


def contains_pan(text: str) -> bool:
    """True if `text` holds a Luhn-valid 13-19 digit number, however it is grouped. NFKC turns
    fullwidth, circled and superscript digits into plain ones first."""
    normal = unicodedata.normalize("NFKC", text)
    groups = (re.sub(r"\D", "", group) for group in _DIGIT_GROUP.findall(normal))
    return any(_any_window_passes(digits) for digits in groups) or _wide_groups_pass(normal)


def contains_cvv(text: str) -> bool:
    """True if a 3 or 4 digit number sits next to a security-code name. A bare number can't be told
    from any other, and a question like "where is the CVV?" holds no card data."""
    return _CVV.search(unicodedata.normalize("NFKC", text)) is not None


def contains_card_data(text: str) -> bool:
    return contains_pan(text) or contains_cvv(text)
