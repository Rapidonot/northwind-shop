"""OPS-1: the four sample traces on the "Agent system" view, one per kind of request. Sample
data, marked so, never a recorded run: no timings and no counts. Each step is a node a real request
of that
kind passes through, in order; a test walks every trace along the generated map's lines.

Their references are real: P0001 is a public product; O000800 is the demo shopper's (S0188) only
delivered order, so it can have arrived damaged and `get_order` answers for it once signed in;
O000002 is in the order store, read by the staff request's subagents; O900001 is the first number
the checkout gives a Pay (demo) order (`checkout.FIRST_ORDER_NUMBER`)."""

from __future__ import annotations

from typing import Any

TALK_TO_A_PERSON = "if the shopper presses Talk to a person"


def _trace(
    id: str,
    entry: str,
    kind: str,
    title: str,
    refs: list[str],
    outcome: str,
    no_ai: bool,
    steps: list[tuple[str, str]],
) -> dict[str, Any]:
    return {
        "id": id,
        "entry": entry,
        "kind": kind,
        "title": title,
        "refs": refs,
        "outcome": outcome,
        "no_ai": no_ai,
        "steps": [{"node": node, "label": label} for node, label in steps],
        "sample": True,
    }


TRACES: tuple[dict[str, Any], ...] = (
    _trace(
        "chat",
        "shopper-chat",
        "Chat question",
        "Is the Tidewick Harbor Rain Jacket waterproof, and is it in stock in Large?",
        ["P0001"],
        "ok",
        False,
        [
            ("shopper-chat", "The shopper asks"),
            ("query-understanding", "Rules read a size filter (L) and an availability filter"),
            ("northwind-catalog", "eligible_products: which products have L in stock, IDs only"),
            ("shopper-assistant", "Plans its tool calls"),
            ("northwind-rag", "retrieve_context: the jacket's specs and care guide"),
            ("northwind-catalog", "check_stock: live stock for size L"),
            ("output-check", "Every claim backed by a source or a live tool"),
            ("answer", "The cited answer"),
        ],
    ),
    _trace(
        "handoff",
        "shopper-chat",
        "Damaged parcel + handoff",
        "My parcel for order O000800 arrived damaged. → Talk to a person",
        ["O000800"],
        "escalated",
        False,
        [
            ("shopper-chat", "The signed-in shopper: my parcel for order O000800 arrived damaged"),
            ("query-understanding", "No product filters: rules only"),
            ("shopper-assistant", "Looks the order up"),
            ("northwind-orders", "get_order: O000800, the shopper's own order, delivered"),
            ("northwind-policy", "evaluate_return_eligibility: can the damaged line be returned"),
            ("output-check", "Order and policy claims backed by the tools"),
            ("answer", "The options, and the offer of a person"),
            ("talk-to-person", TALK_TO_A_PERSON),
            ("handoff-trigger", "Matched before any model call"),
            ("handoff-desk", "Intent, root cause and amounts from the order store, by code"),
            ("care-subagent", "One-line summary, labelled model-written"),
            ("case-queue", "Case queued for staff"),
        ],
    ),
    _trace(
        "checkout",
        "pay-demo",
        "Web checkout",
        "Pay (demo) on a 2-line bag → order shown in history",
        ["O900001"],
        "ok",
        True,
        [
            ("pay-demo", "The signed-in shopper presses Pay (demo)"),
            ("northwind-catalog", "get_cart: the bag read live"),
            ("checkout-validate", "Digest matches; total under the order limit"),
            ("checkout-hold", "Stock held"),
            ("checkout-fraud", "Low risk"),
            ("checkout-authorize", "Sandbox token authorized"),
            ("checkout-commit", "Stock committed"),
            ("checkout-capture", "Captured"),
            ("checkout-confirm", "Order confirmed"),
            ("northwind-catalog", "remove_from_cart: the bag emptied"),
        ],
    ),
    _trace(
        "staff",
        "staff-request",
        "Backend staff request",
        "O000002: was it charged, and where is it? → one answer from two subagents",
        ["O000002"],
        "ok",
        False,
        [
            ("staff-request", "Staff run the orchestrator command with the request"),
            ("orchestrator", "The planner splits it: payment and fraud, and tracking"),
            ("orders-subagent", "payment_fraud, read-only"),
            ("order-payment", "get_payment_status: no payment intent yet, a valid empty answer"),
            ("order-fraud", "get_fraud_assessments: none yet, a valid empty answer"),
            ("returns-subagent", "tracking, read-only"),
            ("order-carrier", "The carrier's scans for O000002"),
            ("orchestrator", "One answer from both sections"),
        ],
    ),
)
