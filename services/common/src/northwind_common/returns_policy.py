"""Return eligibility, decided from the public returns policies (row #27, CHK-07; hard rules 2, 5).

A pure function over facts, shared by northwind-policy (`evaluate_return_eligibility`) and
northwind-orders (`request_return`, which decides again itself). Each fact holds the document it
comes from and that document's exact sentence, white space folded, so an outcome is explained by its
source and nothing else; `missing_facts` is how a server refuses to start on documents that no
longer say it.

Outcomes: `eligible`; `ineligible`, with the reason; `ambiguous`, where the policy text does not
settle it or something is missing, so the shopper is asked or a person decides. Nothing here
approves a return, a refund or a label.

The date is fixed (`AS_OF`, decision D5 of the row #27 PR 2 plan), never the wall clock and never an
argument a caller can set, so recordings replay after the credit ends. The day of delivery is day 0
and the window's last day is in.

The "pillows and duvet inserts" rule is applied to subcategory `duvet`: every one of them is a
filled duvet or comforter, and data v3 has no pillow (D11). No product in data v3 is marked
clearance, so that rule is reached only by tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

Outcome = Literal["eligible", "ineligible", "ambiguous"]

AS_OF = date(2026, 11, 15)
WINDOW_DAYS: Mapping[str, int] = {"US": 30, "CA": 45, "UK": 28, "EU": 60}
REGIONS = tuple(WINDOW_DAYS)
STATUTORY_DAYS = 14
STATUTORY_REGIONS = frozenset({"UK", "EU"})
INSERT_SUBCATEGORIES = frozenset({"duvet"})

CONDITIONS = (
    "sealed",  # unused, packaging unopened
    "opened_unused",
    "unused",  # unused, packaging not stated
    "worn_or_washed",
    "damaged_on_arrival",
    "wear_and_tear",
    "not_stated",
)


@dataclass(frozen=True)
class Fact:
    document_id: str
    key: str
    sentence: str

    def as_dict(self) -> dict[str, str]:
        return {"document_id": self.document_id, "sentence": self.sentence}


def _facts(region: str, **sentences: str) -> dict[str, Fact]:
    return {key: Fact(f"POL-RETURNS-{region}", key, text) for key, text in sentences.items()}


def _window(days: int) -> str:
    return (
        f"You can return unworn, unwashed items within **{days} days** of delivery for a refund to "
        "the original payment method."
    )


_WEAR = "Items damaged by wear and tear are covered by our repair program, not by returns."
_SEALED = "Pillows and duvet inserts can be returned only if still sealed."
_FINAL_SALE = "Clearance items are final sale and cannot be returned."

FACTS: Mapping[str, Mapping[str, Fact]] = {
    "US": _facts(
        "US",
        window=_window(30),
        wear=_WEAR,
        clearance=_FINAL_SALE,
        insert="Pillows and duvet inserts cannot be returned once removed from their packaging.",
    ),
    "CA": _facts(
        "CA",
        window=_window(45),
        wear=_WEAR,
        clearance=_FINAL_SALE,
        insert="Pillows and duvet inserts can be returned within the return window if unused.",
    ),
    "UK": _facts(
        "UK",
        window=_window(28),
        wear=_WEAR,
        clearance="Clearance items can be returned within the 14-day cancellation period.",
        insert=_SEALED,
        statutory=(
            "You have a 14-day right to cancel from delivery, in addition to our own return "
            "window. You do not need to give a reason."
        ),
    ),
    "EU": _facts(
        "EU",
        window=_window(60),
        wear=_WEAR,
        clearance="Clearance items can be returned within the 14-day withdrawal period.",
        insert=_SEALED,
        statutory=(
            "You have a 14-day right of withdrawal from delivery, in addition to our own return "
            "window. You do not need to give a reason."
        ),
    ),
}
ALL_FACTS: tuple[Fact, ...] = tuple(f for facts in FACTS.values() for f in facts.values())


def missing_facts(bodies: Mapping[str, str]) -> list[Fact]:
    """The facts whose sentence is not in its document (`bodies`: document ID to text)."""
    return [
        fact
        for fact in ALL_FACTS
        if fact.sentence not in " ".join(bodies.get(fact.document_id, "").split())
    ]


# reason -> (outcome, explanation, next step, the facts that explain it). Fixed text: no figure and
# no promise, because the figures are in the facts (hard rules 2 and 5).
REASONS: Mapping[str, tuple[Outcome, str, str, tuple[str, ...]]] = {
    "within_window": (
        "eligible",
        "The item is inside the return window and in a condition the policy accepts.",
        "The shopper can ask for a return request for this item.",
        ("window",),
    ),
    "not_delivered": (
        "ineligible",
        "The order has not been delivered, and the return window starts at delivery.",
        "The shopper can ask again after delivery.",
        ("window",),
    ),
    "out_of_window": (
        "ineligible",
        "The return window for this item has ended.",
        "No return request can be made for this item.",
        ("window",),
    ),
    "worn_or_washed": (
        "ineligible",
        "The policy accepts unworn, unwashed items only.",
        "No return request can be made for this item.",
        ("window",),
    ),
    "wear_and_tear": (
        "ineligible",
        "Wear and tear is covered by the repair program, not by returns.",
        "The shopper can use the repair program.",
        ("wear",),
    ),
    "opened_insert": (
        "ineligible",
        "The policy does not accept this kind of item once its packaging has been opened.",
        "No return request can be made for this item.",
        ("insert",),
    ),
    "clearance_final_sale": (
        "ineligible",
        "Clearance items are final sale.",
        "No return request can be made for this item.",
        ("clearance",),
    ),
    "clearance_after_14_days": (
        "ineligible",
        "Clearance items can be returned only inside the period the policy names, which has ended.",
        "No return request can be made for this item.",
        ("clearance",),
    ),
    "condition_not_stated": (
        "ambiguous",
        "The item's condition has not been stated.",
        "Ask the shopper for the item's condition.",
        ("window",),
    ),
    "packaging_not_stated": (
        "ambiguous",
        "Whether this item can be returned depends on its packaging, which has not been stated.",
        "Ask the shopper whether the packaging is still sealed.",
        ("insert",),
    ),
    "damaged_on_arrival": (
        "ambiguous",
        "The returns policy does not say what happens to an item damaged on arrival.",
        "A person needs to review it.",
        ("wear",),
    ),
    "worn_in_cancellation_period": (
        "ambiguous",
        "The item is worn or washed, but the order is inside the period in which no reason is "
        "needed.",
        "A person needs to review it.",
        ("window", "statutory"),
    ),
    "wear_in_cancellation_period": (
        "ambiguous",
        "Wear and tear is covered by the repair program, but the order is inside the period in "
        "which no reason is needed.",
        "A person needs to review it.",
        ("wear", "statutory"),
    ),
    "opened_insert_in_cancellation_period": (
        "ambiguous",
        "The packaging has been opened, but the order is inside the period in which no reason is "
        "needed.",
        "A person needs to review it.",
        ("insert", "statutory"),
    ),
    "more_than_one_line": (
        "ambiguous",
        "The order has this item on more than one line.",
        "A person needs to review it.",
        ("window",),
    ),
}


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str
    explanation: str
    next_step: str
    facts: tuple[Fact, ...]


def _decision(region: str, reason: str) -> Decision:
    outcome, explanation, next_step, keys = REASONS[reason]
    return Decision(
        outcome, reason, explanation, next_step, tuple(FACTS[region][key] for key in keys)
    )


def _reason(
    region: str,
    insert: bool,
    days: int | None,
    condition: str,
    clearance: bool,
    lines: int,
) -> str:
    """The ordered rules; the first that applies decides."""
    if days is None:
        return "not_delivered"
    statutory = region in STATUTORY_REGIONS and days <= STATUTORY_DAYS
    if condition == "wear_and_tear":
        return "wear_in_cancellation_period" if statutory else "wear_and_tear"
    if condition == "damaged_on_arrival":
        return "damaged_on_arrival"  # before the window: no policy text settles it at any age
    if days > WINDOW_DAYS[region]:
        return "out_of_window"
    if lines > 1:
        return "more_than_one_line"  # after the window: both lines have the same delivery date
    if clearance and region not in STATUTORY_REGIONS:
        return "clearance_final_sale"
    if clearance and not statutory:
        return "clearance_after_14_days"
    if condition == "not_stated":
        return "condition_not_stated"
    if condition == "worn_or_washed":
        return "worn_in_cancellation_period" if statutory else "worn_or_washed"
    if insert and region != "CA" and condition == "unused":
        return "packaging_not_stated"
    if insert and region != "CA" and condition == "opened_unused":
        return "opened_insert_in_cancellation_period" if statutory else "opened_insert"
    return "within_window"


def evaluate(
    *,
    region: str,
    subcategory: str,
    delivered_on: date | None,
    condition: str,
    clearance: bool = False,
    lines: int = 1,
    as_of: date = AS_OF,
) -> Decision:
    """`delivered_on` is the delivered scan's date, or None. `lines` is how many lines of the order
    hold the variant. An unknown value is a ValueError: never guessed."""
    if region not in WINDOW_DAYS:
        raise ValueError("unknown region")
    if condition not in CONDITIONS:
        raise ValueError("unknown condition")
    if lines < 1:
        raise ValueError("lines must be at least 1")
    # A scan dated after the fixed date counts as delivered on it.
    days = None if delivered_on is None else max(0, (as_of - delivered_on).days)
    insert = subcategory in INSERT_SUBCATEGORIES
    return _decision(region, _reason(region, insert, days, condition, clearance, lines))
