"""RAG-04: a model-written plan is checked by code before anything reaches search (row #11).

The shopper's message can carry injected text, and so can the model's reply to it. `validate_plan`
takes the parsed JSON and returns a plan only if every field is allow-listed, typed and in range;
otherwise None, and the caller keeps the rules plan. Filters can only narrow a search, and audience,
region and visibility are never read from a plan.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from northwind_rag.query import (
    MAX_PART_CHARS,
    MAX_PARTS,
    Filters,
    Price,
    validate_plan,
)

pytestmark = pytest.mark.req("RAG-04")


def part(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "text": "navy fleece",
        "colours": ["Navy"],
        "sizes": ["M"],
        "price_kind": "max",
        "amount": "80",
        "amount_to": "",
        "currency": "USD",
        "in_stock_only": True,
    }
    base.update(over)
    return base


def plan(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"parts": list(parts) or [part()]}


def test_a_good_plan_passes() -> None:
    checked = validate_plan(plan(), "US")
    assert checked is not None
    [only] = checked.parts
    assert only.text == "navy fleece"
    assert only.filters == Filters(
        colour=("Navy",), size=("M",), price=Price("USD", None, 8000), availability="in_stock"
    )
    assert checked.source == "model"


def test_family_words_expand_and_lists_sort() -> None:
    checked = validate_plan(plan(part(colours=["blue", "Black"], sizes=["XL", "2XL"])), "US")
    assert checked is not None
    assert checked.parts[0].filters.colour == ("Black", "Cobalt", "Glacier Blue", "Navy")
    assert checked.parts[0].filters.size == ("2XL", "XL")


@pytest.mark.parametrize(
    ("kind", "amount", "amount_to", "price"),
    [
        ("max", "50", "", Price("USD", None, 5000)),
        ("min", "79.99", "", Price("USD", 7999, None)),
        ("range", "25", "40", Price("USD", 2500, 4000)),
        ("around", "90", "", Price("USD", 7200, 10800)),
        ("around", "125", "", Price("USD", 10000, 15000)),
        ("none", "", "", None),
    ],
)
def test_price_kinds(kind: str, amount: str, amount_to: str, price: Price | None) -> None:
    checked = validate_plan(plan(part(price_kind=kind, amount=amount, amount_to=amount_to)), "US")
    assert checked is not None
    assert checked.parts[0].filters.price == price


def test_a_foreign_currency_gives_no_price() -> None:
    checked = validate_plan(plan(part(currency="GBP")), "US")
    assert checked is not None and checked.parts[0].filters.price is None


def test_empty_lists_mean_no_filter() -> None:
    checked = validate_plan(
        plan(part(colours=[], sizes=[], price_kind="none", in_stock_only=False)), "US"
    )
    assert checked is not None and checked.parts[0].filters == Filters()


def hostile() -> list[Any]:
    """Each is refused whole: the caller falls back to the rules plan."""
    cases: list[Any] = [
        {"parts": [part()], "visibility": "internal"},  # an unknown top-level key
        {"parts": [part(region="UK")]},  # an unknown part key
        {"parts": [part(audience="staff")]},
        {"parts": [part(visibility="internal")]},
        {"parts": []},
        {"parts": [part()] * (MAX_PARTS + 1)},  # a fourth part
        {"parts": [part(text="x" * (MAX_PART_CHARS + 1))]},
        {"parts": [part(text="")]},
        {"parts": [part(text="fleece\x00")]},
        {"parts": [part(text="pay with 4111 1111 1111 1111")]},  # card data in a part
        {"parts": [part(colours=["Pink"])]},  # off-vocabulary
        {"parts": [part(sizes=["W32"])]},
        {"parts": [part(colours="Navy")]},  # wrong type
        {"parts": [part(in_stock_only="yes")]},
        {"parts": [part(amount="-1")]},  # negative price
        {"parts": [part(amount="0")]},
        {"parts": [part(amount="10000.01")]},  # over the range
        {"parts": [part(amount="1e3")]},
        {"parts": [part(amount="80.001")]},
        {"parts": [part(price_kind="range", amount="80", amount_to="50")]},  # min > max
        {"parts": [part(price_kind="free")]},
        {"parts": [part(currency="BTC")]},
        {"parts": [part(colours=["Navy"] * 40)]},
        "not an object",
        None,
        [],
    ]
    return cases


@pytest.mark.parametrize("raw", hostile())
def test_hostile_plans_are_refused(raw: Any) -> None:
    assert validate_plan(copy.deepcopy(raw), "US") is None


def test_an_unknown_region_is_refused() -> None:
    with pytest.raises(ValueError):
        validate_plan(plan(), "XX")
