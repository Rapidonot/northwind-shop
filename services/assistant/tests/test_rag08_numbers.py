"""Row #13, the user's fail-closed rule for numbers (2026-11-11, before the last security pass): in
a policy sentence, a duration span holding a number word or numeral form the parser does not
consume exactly is never vouched, so the sentence is replaced. Outside a policy sentence the
same text passes (the policy-word gate)."""

from __future__ import annotations

import pytest

from northwind_assistant.output_check import FIXED_SENTENCES, OutputCheck, Source, check_text

pytestmark = pytest.mark.req("NFR-03")

POLICY = FIXED_SENTENCES["false_policy"]
DOCS = [
    Source(
        "POL-RETURNS-US",
        "document",
        "You can return unworn items within 30 days of delivery. Refunds are issued within 5 "
        "business days. A care associate replies within one business day.",
    ),
    Source("POL-SHIPPING-US", "document", "Standard: 3–5 business days. Express: 1–2 days."),
]


@pytest.mark.parametrize(
    "answer",
    [
        "You can return any item within a dozen days.",
        "You can return any item within a fortnight.",
        "You can return any item within twenty-something days.",
        "You can return any item within 365-ish days.",
        "You can return any item within 1.5 days.",
        "You can return any item within 30-odd days.",
    ],
)
def test_an_unparsed_number_in_a_policy_duration_is_replaced(answer: str) -> None:
    assert check_text(answer, DOCS) == POLICY


@pytest.mark.parametrize(
    "text",
    [
        "The fleece takes a fortnight to break in.",
        "It stays warm for a dozen days on the trail.",
        "Reviewers wore it for twenty-something days straight.",
    ],
)
def test_outside_a_policy_sentence_the_same_forms_pass(text: str) -> None:
    assert check_text(text, DOCS) == text


@pytest.mark.parametrize(
    "answer",
    [
        "You can return unworn items within 30 days of delivery.",
        "Standard delivery takes 3–5 business days.",
        "Refunds are issued within 5 business days.",
        "A care associate replies within one business day about returns.",
    ],
)
def test_a_number_the_parser_reads_exactly_still_passes(answer: str) -> None:
    assert check_text(answer, DOCS) == answer


@pytest.mark.parametrize("size", [1, 3, 7])
def test_an_unparsed_number_is_never_sent_however_it_is_split(size: int) -> None:
    text = "Thanks for asking. You can return any item within a fortnight. Bye."
    check = OutputCheck(set(), DOCS)
    sent: list[str] = []
    for i in range(0, len(text), size):
        sent += [p for p in check.feed(text[i : i + size]) if p is not None]
    sent += [p for p in check.finish() if p is not None]
    assert "fortnight" not in "".join(sent) and POLICY in check.shown


def test_three_tokens_back_are_three_real_tokens() -> None:
    """A number three words before the unit is inside the span (no empty tokens in between)."""
    assert check_text("Returns: within twenty or so business days.", DOCS) == POLICY


def test_a_bold_number_the_parser_reads_still_passes() -> None:
    answer = "You can return unworn items within **30 days** of delivery."
    assert check_text(answer, DOCS) == answer


def test_an_earlier_unrelated_number_is_not_in_the_span() -> None:
    answer = "Return 2 items within 30 days of delivery."
    assert check_text(answer, DOCS) == answer


def test_a_citation_in_the_span_is_not_a_number() -> None:
    answer = "Returns [FAQ-019] take 30 days from delivery."
    assert check_text(answer, DOCS, {"FAQ-019"}) == answer


def test_a_number_before_the_sentence_is_not_in_the_span() -> None:
    answer = "See item 4. Takes 30 days to return."  # "4" is within three words of "days"
    assert check_text(answer, DOCS) == answer
