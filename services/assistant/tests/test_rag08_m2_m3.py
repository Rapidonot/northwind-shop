"""Row #13, the user's decisions on security findings M2 and M3 (2026-11-11), part of the class
freeze. M2: a policy duration passes only if a sentence of a public document holds the same
duration and the same policy stem; the policy context takes in the preceding heading or parent
bullet line. M3: the added wordings. No model call, no spend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_assistant.output_check import (
    CONFIRMATION_SENTENCE,
    FIXED_SENTENCES,
    OutputCheck,
    Source,
    check_text,
)

pytestmark = pytest.mark.req("NFR-03")

FIXTURES = Path(__file__).resolve().parents[3] / "data" / "fixtures"
PROMO = FIXED_SENTENCES["promotion"]
STOCK = FIXED_SENTENCES["stock"]
POLICY = FIXED_SENTENCES["false_policy"]


def doc(doc_id: str) -> Source:
    for line in (FIXTURES / "documents.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["doc_id"] == doc_id:
            return Source(doc_id, "document", record["body"])
    raise KeyError(doc_id)


# ---- M2 ------------------------------------------------------------------------------------------


def test_m2_a_repair_duration_does_not_vouch_for_a_returns_window() -> None:
    assert "two years" in doc("FAQ-012").text
    assert check_text("You can return any item within two years.", [doc("FAQ-012")]) == POLICY


def test_m2_a_heading_line_puts_the_next_line_in_policy_context() -> None:
    assert check_text("**Return window**\n365 days") == "**Return window**\n" + POLICY


def test_m2_a_parent_bullet_puts_its_children_in_policy_context() -> None:
    text = "Returns:\n- a full year\n- free of charge"
    assert check_text(text) == "Returns:\n- " + POLICY + "\n- free of charge"


@pytest.mark.parametrize(
    ("answer", "source"),
    [
        ("You can return unworn items within 30 days of delivery.", "POL-RETURNS-US"),
        ("In the United States you can return unworn items within 30 days.", "FAQ-019"),
        ("You can return unworn, unwashed items within 45 days of delivery.", "POL-RETURNS-CA"),
        ("Standard delivery takes 3–5 business days after dispatch.", "POL-SHIPPING-US"),
        ("You can change or cancel an order within one hour of placing it.", "FAQ-007"),
    ],
)
def test_m2_a_correct_policy_duration_still_passes(answer: str, source: str) -> None:
    assert check_text(answer, [doc(source)]) == answer


def test_m2_the_same_duration_with_another_stem_does_not_vouch() -> None:
    answer = "Refunds for any item are given within one hour."
    assert check_text(answer, [doc("FAQ-007")]) == POLICY  # FAQ-007: cancel, not refund


def test_m2_a_long_policy_sentence_keeps_the_stems_read_up_to_the_bound() -> None:
    """FAQ-017's stems come more than L after its 14 days: the sentence fails closed as policy,
    but with the stems read so far, so its own FAQ still vouches for it."""
    faq = doc("FAQ-017")
    answer = faq.text.split("\n\n", 1)[1].strip()
    assert check_text(answer, [faq]) == answer
    long_tail = "Returns take 14 days" + " and then quite a lot more words follow here" * 3 + "."
    assert check_text(long_tail, [faq]) == POLICY  # no shared stem within the bound


def test_m2_a_documents_heading_vouches_for_a_bullet_with_no_stem_of_its_own() -> None:
    shipping = Source(
        "POL-SHIPPING-XX", "document", "## Delivery times\n\n- **Express:** 1–2 days."
    )
    answer = "Express delivery takes 1–2 days."
    assert check_text(answer, [shipping]) == answer
    bare = Source("POL-SHIPPING-XX", "document", "- **Express:** 1–2 days.")
    assert check_text(answer, [bare]) == POLICY


@pytest.mark.parametrize("size", [3, 50, 100000])
def test_m2_a_vouched_duration_passes_however_much_text_follows(size: int) -> None:
    """The decision must not depend on how the text was split: fed whole, a long answer after a
    vouched duration once failed closed (found by the red-team replay, RT-023)."""
    head = "- **Return window:** You have 45 days from delivery to return unworn, unwashed items "
    head += "for a refund to your original payment method [POL-RETURNS-CA].\n"
    text = head + "- Soft and warm, and the colours hold well in the wash.\n" * 25
    check = OutputCheck({"POL-RETURNS-CA"}, [doc("POL-RETURNS-CA")])
    for i in range(0, len(text), size):
        check.feed(text[i : i + size])
    check.finish()
    assert check.shown == text


def test_m2_an_internal_document_never_vouches() -> None:
    internal = Source("INT-009", "document", "You can return any item within two years.")
    assert check_text("You can return any item within two years.", [internal]) == POLICY


# ---- M3 ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "These towels are 90 percent off.",
        "These towels are 90 per cent off.",
        "Get it at half price.",
        "Join the clearance sale.",
        "It has a clearance price.",
        "Come to the clearance event.",
    ],
)
def test_m3_promotion_wordings_are_replaced(payload: str) -> None:
    assert check_text(payload) == PROMO


@pytest.mark.parametrize(
    "payload",
    [
        "Your order is now confirmed.",
        "Your payment has been already processed.",
        "Your refund was fully approved.",
        "We've processed your payment.",
        "We have placed your order.",
        "We've confirmed it.",
        "We have issued the refund.",
    ],
)
def test_m3_confirmation_wordings_are_replaced(payload: str) -> None:
    assert check_text(payload).startswith(CONFIRMATION_SENTENCE)


def test_m3_a_negation_is_still_not_an_adverb() -> None:
    assert check_text("Your order is not confirmed until checkout.") == (
        "Your order is not confirmed until checkout."
    )


@pytest.mark.parametrize(
    "payload", ["There are just 2 left.", "Stock is running low.", "Stock is low."]
)
def test_m3_stock_wordings_are_replaced(payload: str) -> None:
    assert check_text(payload) == STOCK


def test_m3_clearance_policy_wording_still_passes() -> None:
    text = "Clearance items are final sale and cannot be returned."
    assert check_text(text, [doc("POL-RETURNS-US")]) == text


@pytest.mark.parametrize("size", [1, 4])
def test_m3_wordings_are_never_sent_however_they_are_split(size: int) -> None:
    text = "Great news. We've processed your payment, and towels are 90 percent off. Bye."
    check = OutputCheck(set(), [])
    sent: list[str] = []
    for i in range(0, len(text), size):
        sent += [p for p in check.feed(text[i : i + size]) if p is not None]
    sent += [p for p in check.finish() if p is not None]
    joined = "".join(sent).lower()
    assert "processed" not in joined and "percent off" not in joined
