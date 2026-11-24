"""What staff should do about an escalated order, per reason code (row #21; moved here by row #26).

The table is code, never model text: the Orders agent's escalation context and the Care agent's case
(BE-07) both take their recommended action from it, by the reason code the order store holds. The
first twelve entries are the ones row #21 wrote, byte-identical (pinned in
`tests/care/test_be07_units.py`); `not_escalated` is the Care agent's, for an order that was never
escalated."""

from __future__ import annotations

NOT_ESCALATED = "not_escalated"
UNKNOWN_CAUSE = "agent_requested"

RECOMMENDED: dict[str, str] = {
    "uncertain_write": "read the payment and order state, settle the uncertain action, then finish",
    "duplicate_in_progress": (
        "another run holds the payment claim: read the order's intent at the payment port, or the "
        "order's reconstruction (payment_label), before acting; `payment` is only what was "
        "last reported"
    ),
    "fraud_review": "review the fraud assessment; release or cancel the order",
    "out_of_stock": "offer a substitute or a back-order, or cancel (BE-04)",
    "not_sold_here": "correct the order lines or cancel",
    "address_unresolved": "contact the shopper for a deliverable address",
    "payment_declined": "ask the shopper for another payment method",
    "over_order_limit": "approve the amount by hand or ask for a smaller order",
    "confirmation_problem": "ask the shopper to confirm the order again in the portal",
    "port_failure": "retry the order later",
    "agent_requested": "check the order; the agent escalated without a known cause",
    # Not "rerun": a later run makes no write on an escalated order (security review F1).
    "not_finished": "read the order and payment state, then finish the order by hand",
    # Row #26 (BE-07): a case for an order nobody escalated.
    NOT_ESCALATED: "read the order and the case summary, then answer the shopper or escalate it",
}


def root_cause_of(*, escalated: bool, code: str | None) -> str:
    """The case's root cause, from the order store's status and reason code only. A code the table
    doesn't know is reported as `agent_requested`, never passed through."""
    if not escalated:
        return NOT_ESCALATED
    if code is None or code == NOT_ESCALATED or code not in RECOMMENDED:
        return UNKNOWN_CAUSE
    return code
