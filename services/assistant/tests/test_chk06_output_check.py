"""Row #27 (CHK-06; decision 0015's row #27 amendment): the output check lets "your order was
placed" or "order confirmed" through only for an order this turn's `get_order` returned with a
confirmed status, named in the same sentence before the claim. Everything else stays as it was:
with no order read this turn, the check is unchanged; refund authority and "I've confirmed" are
always blocked."""

from __future__ import annotations

import pytest

from northwind_assistant.output_check import LiveValues, hits, live_backed

pytestmark = pytest.mark.req("CHK-06")

DELIVERED = LiveValues(orders={"O000064": "delivered"})
MIXED = LiveValues(orders={"O000064": "delivered", "O000113": "on_hold", "O000999": "processing"})


@pytest.mark.parametrize(
    "text",
    [
        "O000064: your order was placed on 16 October and was delivered on 22 October.",
        "Order O000064 - your order has been placed and is delivered.",
        "For o000064, your order was placed on 16 October.",
        "O000064: order confirmed, and it was delivered on 22 October.",
    ],
)
def test_a_vouched_order_may_be_called_placed_or_confirmed(text: str) -> None:
    assert hits(text, live=DELIVERED).get("confirmation", 0) == 0
    assert hits(text).get("confirmation", 0) == 1  # the same text with no order read: blocked


@pytest.mark.parametrize(
    "text",
    [
        "O000113: your order has been confirmed.",  # on hold
        "O000999: your order was placed.",  # still processing
        "O000123: your order was placed.",  # not read this turn
        "Your order was placed on 16 October.",  # no order named
        "O000064 was delivered. Your order was placed on 16 October.",  # another sentence
        "O000064 and O000113: the order was placed.",  # one of two is on hold
        "Your order was placed (O000064).",  # named only after the claim
        "Order confirmed: O000064.",
        "O000064: your payment was processed.",  # a payment, not the order
    ],
)
def test_anything_else_stays_blocked(text: str) -> None:
    assert hits(text, live=MIXED).get("confirmation", 0) == 1


@pytest.mark.parametrize(
    "text",
    [
        "O000064: I've confirmed your order.",
        "O000064: we have processed your order.",
    ],
)
def test_the_assistant_never_claims_to_have_acted(text: str) -> None:
    assert hits(text, live=DELIVERED).get("confirmation", 0) == 1


@pytest.mark.parametrize(
    "text",
    [
        "O000064: your refund has been approved.",
        "O000064 was delivered, so a refund is approved.",
    ],
)
def test_refund_authority_is_blocked_whatever_the_order(text: str) -> None:
    counts = hits(text, live=DELIVERED)
    assert counts.get("refund_authority", 0) + counts.get("confirmation", 0) >= 1


def test_a_conditional_still_passes_with_no_order_read() -> None:
    assert hits("If your order is confirmed today, it ships tomorrow.").get("confirmation", 0) == 0


def test_a_vouched_order_sentence_counts_as_tool_backed_not_sourced() -> None:
    assert live_backed("O000064: order confirmed.", DELIVERED) == 1
    assert live_backed("O000064: order confirmed.", LiveValues()) == 0


# ---- security review pass 1 (docs/reviews/2026-11-24-27-security-review.raw.md) -----------------

BOTH_READ = LiveValues(orders={"O000064": "delivered", "O000113": "on_hold"})


@pytest.mark.parametrize(
    "text",
    [
        # M1: the claim's subject is another order than the one named.
        "O000064 arrived on 22 October, and your other order was placed and is being prepared.",
        "O000064 is delivered, and the second order has been confirmed.",
        "O000064 shipped first; the order was placed later.",
    ],
)
def test_m1_a_named_order_does_not_vouch_for_another_order_in_its_sentence(text: str) -> None:
    assert hits(text, live=BOTH_READ).get("confirmation", 0) == 1


@pytest.mark.parametrize(
    "verb", ["approved", "authorised", "authorized", "processed", "applied", "issued"]
)
def test_l1_only_placed_or_confirmed_is_vouched(verb: str) -> None:
    assert hits(f"O000064: your order has been {verb}.", live=DELIVERED).get("confirmation") == 1


@pytest.mark.parametrize("cut", range(1, 7))
def test_l2_an_order_cut_by_the_windows_left_edge_still_counts(cut: int) -> None:
    """An on-hold order straddling the 64-character look-back edge, in the same sentence as a
    vouched claim, must still be seen, so the sentence stays blocked."""
    tail = " and O000064: your order was placed."
    claim_at = tail.index("order was placed")
    # Put O000113 so that `cut` of its 7 characters fall before the window's start.
    gap = 64 - claim_at - (7 - cut)
    text = "O000113 " + "x" * (gap - 1) + tail
    start = text.index("order was placed")
    assert text.index("O000113") == start - 64 - cut
    assert hits(text, live=BOTH_READ).get("confirmation", 0) == 1, text


# ---- scoped re-review (docs/reviews/2026-11-24-27-security-rereview.raw.md), N1 ------------------


@pytest.mark.parametrize(
    "text",
    [
        "Unlike O000064, your order has been confirmed but not yet shipped.",
        "Besides O000064, your order was placed.",
        "As with O000064, the order was placed.",
        "Apart from O000064 your order has been confirmed.",
        "Like O000064, your order was placed on time.",
        "You asked about O000113 and O000064, your order was placed.",
    ],
)
def test_n1_an_id_ending_a_lead_in_clause_does_not_bind(text: str) -> None:
    assert hits(text, live=BOTH_READ).get("confirmation", 0) == 1


@pytest.mark.parametrize(
    "text",
    [
        "O000064: your order was placed on 16 October.",
        "- O000064: your order was placed on 16 October.",
        "Your order O000064: the order was placed on 16 October.",
        "Thanks. For your order O000064, the order was placed on 16 October.",
    ],
)
def test_n1_a_sentence_that_starts_with_its_order_still_binds(text: str) -> None:
    assert hits(text, live=BOTH_READ).get("confirmation", 0) == 0
