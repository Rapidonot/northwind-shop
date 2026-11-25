"""RAG-04 (row #11, decision 0022 section 3), hard rules 1, 6 and 7: `validate_plan` is the only way
a model's reply becomes a plan. It cannot be talked into widening a search or carrying card data.

Written from the decision: allow-listed fields, exact types, the catalog's own colours and sizes,
prices as integer minor units in range and in the region's own currency, at most 3 parts of at most
200 characters, none card-shaped. Synthetic card numbers only. No database, no model.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from northwind_common.cards import contains_card_data
from northwind_rag.query import (
    MAX_MINOR,
    MAX_PART_CHARS,
    MAX_PARTS,
    REGION_CURRENCY,
    Filters,
    Vocabulary,
    validate_plan,
)

pytestmark = pytest.mark.req("RAG-04")

VOCAB = Vocabulary.load()


def part(text: str = "black fleece", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "text": text,
        "colours": [],
        "sizes": [],
        "price_kind": "none",
        "amount": "",
        "amount_to": "",
        "currency": "none",
        "in_stock_only": False,
    }
    base.update(over)
    return base


def plan(*parts: dict[str, Any], **top: Any) -> dict[str, Any]:
    return {"parts": list(parts) or [part()], **top}


def test_the_clean_plan_is_the_positive_control() -> None:
    result = validate_plan(plan(part(colours=["Black"], sizes=["M"])), "US")
    assert result is not None and result.source == "model"
    [only] = result.parts
    assert only.text == "black fleece"
    assert only.filters == Filters(colour=("Black",), size=("M",))


# ---- fields: allow-listed, exact ---------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"visibility": "internal"},
        {"audience": "staff"},
        {"aud": "staff"},
        {"region": "EU"},
        {"agent": "catalog-subagent"},
        {"products": ["P0001"]},
        {"filters": {"visibility": "internal"}},
        {"price_max": 0},
        {"availability": "any"},
        {"qf": {"parts": []}},
    ],
)
def test_an_unknown_key_in_a_plan_refuses_the_whole_plan(extra: dict[str, Any]) -> None:
    assert validate_plan(plan(**extra), "US") is None


@pytest.mark.parametrize(
    "extra",
    [
        {"visibility": "internal"},
        {"audience": "staff"},
        {"aud": "staff"},
        {"region": "UK"},
        {"agent": "catalog-subagent"},
        {"product_ids": ["P0001"]},
        {"colour": ["Black"]},
        {"availability": "in_stock"},
    ],
)
def test_an_unknown_key_in_a_part_refuses_the_whole_plan(extra: dict[str, Any]) -> None:
    assert validate_plan(plan(part(), part("duvet", **extra)), "US") is None


@pytest.mark.parametrize("missing", sorted(part()))
def test_a_part_missing_any_field_is_refused(missing: str) -> None:
    broken = part()
    del broken[missing]
    assert validate_plan(plan(broken), "US") is None


@pytest.mark.parametrize(
    "raw",
    [None, [], "parts", 7, {}, {"parts": None}, {"parts": {}}, {"parts": []}, {"parts": "x"}],
)
def test_a_reply_that_is_not_the_shape_is_refused(raw: Any) -> None:
    assert validate_plan(raw, "US") is None


def test_a_plan_cannot_carry_audience_region_or_visibility_in_its_filters() -> None:
    """Even a plan that passes holds only these four filters; there is nowhere to put the rest."""
    names = {f.name for f in dataclasses.fields(Filters)}
    assert names == {"colour", "size", "price", "availability"}
    assert names.isdisjoint({"audience", "region", "visibility", "agent", "products", "aud"})


# ---- parts: count, size, content ----------------------------------------------------------------


def test_at_most_three_parts() -> None:
    parts = [part(f"ask {i}") for i in range(MAX_PARTS)]
    assert validate_plan(plan(*parts), "US") is not None
    assert validate_plan(plan(*parts, part("a fourth")), "US") is None


def test_a_part_is_at_most_200_characters() -> None:
    assert validate_plan(plan(part("x" * MAX_PART_CHARS)), "US") is not None
    assert validate_plan(plan(part("x" * (MAX_PART_CHARS + 1))), "US") is None


@pytest.mark.parametrize("text", ["", "   ", "\n", "a\x00b", None, 5, ["x"], {"t": "x"}])
def test_a_part_text_that_is_not_plain_text_is_refused(text: Any) -> None:
    assert validate_plan(plan(part(text)), "US") is None


@pytest.mark.parametrize(
    "card",
    ["4111111111111111", "4111 1111 1111 1111", "4111-1111-1111-1111", "5555 5555 5555 4444"],
)
def test_a_part_with_long_digit_card_text_is_refused(card: str) -> None:
    assert contains_card_data(card)
    assert validate_plan(plan(part("fleece"), part(f"pay with {card}")), "US") is None
    assert validate_plan(plan(part(card)), "US") is None


def test_a_part_with_card_text_in_any_form_is_never_in_a_validated_plan() -> None:
    """Decision 0022 section 3: none card-shaped. Every form a shopper could type, including the
    ones a long-digit pattern misses (dotted, slashed, split by newlines, a spoken CVV). The
    assistant adds its own check on top (`_parts_safe`), but this function alone is the
    documented validator. Offenders are listed."""
    forms = {
        "dotted": "4111.1111.1111.1111",
        "slashed": "4111/1111/1111/1111",
        "newlines": "4111\n1111\n1111\n1111",
        "fullwidth": "４１１１１１１１１１１１１１１１",
        "amex": "3782 822463 10005",
        "cvv_named": "cvv 737",
        "cvv_after": "737 is the security code",
        "cvv_spaced": "cvc 7 3 7",
    }
    offenders = []
    for name, card in forms.items():
        assert contains_card_data(card), name
        result = validate_plan(plan(part(f"queen duvet {card}")), "US")
        if result is not None and any(contains_card_data(p.text) for p in result.parts):
            offenders.append(name)
    assert offenders == []


# ---- vocabulary ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "colour", ["Chartreuse", "BLACK FAMILY", "", "Black; DROP", "internal", 3, None]
)
def test_a_colour_outside_the_catalog_refuses_the_plan(colour: Any) -> None:
    assert validate_plan(plan(part(colours=[colour])), "US") is None


@pytest.mark.parametrize("size", ["XXXXL", "m", "", "6 x 9 ft", 4, None])
def test_a_size_outside_the_catalog_refuses_the_plan(size: Any) -> None:
    assert validate_plan(plan(part(sizes=[size])), "US") is None


def test_colours_and_sizes_that_pass_are_the_catalogs_own_names() -> None:
    some_colour = sorted(VOCAB.colours)[0]
    some_size = sorted(VOCAB.sizes)[0]
    result = validate_plan(plan(part(colours=[some_colour], sizes=[some_size])), "US")
    assert result is not None
    [only] = result.parts
    assert only.filters.colour == (some_colour,) and only.filters.size == (some_size,)


def test_a_colour_family_expands_to_catalog_colours_only() -> None:
    family = sorted(VOCAB.families)[0]
    result = validate_plan(plan(part(colours=[family.capitalize()])), "US")
    assert result is not None
    colours = result.parts[0].filters.colour
    assert colours is not None and set(colours) <= VOCAB.colours


def test_a_list_that_is_too_long_or_not_a_list_is_refused() -> None:
    assert validate_plan(plan(part(colours=["Black"] * 17)), "US") is None
    assert validate_plan(plan(part(colours="Black")), "US") is None
    assert validate_plan(plan(part(sizes="M")), "US") is None


# ---- price: integer minor units, in range, the region's own currency ----------------------------


@pytest.mark.parametrize(("region", "currency"), sorted(REGION_CURRENCY.items()))
def test_a_price_in_the_regions_currency_is_integer_minor_units(region: str, currency: str) -> None:
    result = validate_plan(plan(part(price_kind="max", amount="49.99", currency=currency)), region)
    assert result is not None
    price = result.parts[0].filters.price
    assert price is not None and price.currency == currency
    assert price.max == 4999 and price.min is None
    assert isinstance(price.max, int) and not isinstance(price.max, bool)


@pytest.mark.parametrize(("region", "currency"), sorted(REGION_CURRENCY.items()))
def test_a_price_in_another_currency_is_never_a_filter(region: str, currency: str) -> None:
    """No conversion: a bound in a currency that is not the region's does not become a filter."""
    for other in sorted(set(REGION_CURRENCY.values()) - {currency}):
        result = validate_plan(plan(part(price_kind="max", amount="50", currency=other)), region)
        assert result is None or result.parts[0].filters.price is None, (region, other)


@pytest.mark.parametrize(
    "amount",
    ["0", "-5", "-0.01", "10000.01", "99999999", "1e3", "5.999", "NaN", "inf", " 50", "50 ", "5,0"]
    + ["５０", "٥٠", "50\n", "$50", "50.", ".5", "0x32"],
)
def test_a_bad_amount_refuses_the_plan(amount: str) -> None:
    raw = plan(part(price_kind="max", amount=amount, currency="USD"))
    assert validate_plan(raw, "US") is None


@pytest.mark.parametrize("amount", [50, 50.0, True, None, ["50"], {"v": 50}])
def test_an_amount_that_is_not_a_string_refuses_the_plan(amount: Any) -> None:
    raw = plan(part(price_kind="max", amount=amount, currency="USD"))
    assert validate_plan(raw, "US") is None


def test_the_largest_amount_is_ten_thousand_in_minor_units() -> None:
    assert MAX_MINOR == 1_000_000
    top = validate_plan(plan(part(price_kind="max", amount="10000", currency="USD")), "US")
    assert top is not None and top.parts[0].filters.price is not None
    assert top.parts[0].filters.price.max == MAX_MINOR
    over = validate_plan(plan(part(price_kind="max", amount="10000.01", currency="USD")), "US")
    assert over is None


def test_a_range_needs_two_amounts_in_order() -> None:
    ok = plan(part(price_kind="range", amount="20", amount_to="80", currency="USD"))
    result = validate_plan(ok, "US")
    assert result is not None and result.parts[0].filters.price is not None
    price = result.parts[0].filters.price
    assert (price.min, price.max) == (2000, 8000)
    backwards = plan(part(price_kind="range", amount="80", amount_to="20", currency="USD"))
    assert validate_plan(backwards, "US") is None
    one = plan(part(price_kind="range", amount="20", currency="USD"))
    assert validate_plan(one, "US") is None
    stray = plan(part(price_kind="max", amount="20", amount_to="80", currency="USD"))
    assert validate_plan(stray, "US") is None


@pytest.mark.parametrize("kind", ["cheap", "MAX", "", None, 1, "none; max"])
def test_an_unknown_price_kind_refuses_the_plan(kind: Any) -> None:
    assert validate_plan(plan(part(price_kind=kind, amount="5", currency="USD")), "US") is None


@pytest.mark.parametrize("currency", ["usd", "BTC", "$", "USD ", 5, None, ["USD"]])
def test_an_unknown_currency_refuses_the_plan(currency: Any) -> None:
    raw = plan(part(price_kind="max", amount="50", currency=currency))
    assert validate_plan(raw, "US") is None


# ---- availability -------------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["true", "yes", 1, 0, None, "in_stock", [True]])
def test_availability_is_a_real_boolean(flag: Any) -> None:
    assert validate_plan(plan(part(in_stock_only=flag)), "US") is None


def test_availability_is_in_stock_or_nothing() -> None:
    on = validate_plan(plan(part(in_stock_only=True)), "US")
    off = validate_plan(plan(part(in_stock_only=False)), "US")
    assert on is not None and on.parts[0].filters.availability == "in_stock"
    assert off is not None and off.parts[0].filters.availability is None


def test_a_validated_plan_is_immutable() -> None:
    result = validate_plan(plan(part(colours=["Black"])), "US")
    assert result is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.parts[0].filters.colour = ("White",)  # type: ignore[misc]
