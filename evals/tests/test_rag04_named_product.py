"""Row #11 (RAG-04, decision 0022's named-product exception; W5 `variant` regression).

A message that names a catalog product runs the plain first search: no colour, size or live-filter
narrowing, on either path (the gateway's `plan_claim` and the eval's `shopper_parts`). Narrowing a
named product's variant question dropped the very variant chunks that answer it (EV-042: "Is the
Corvane Ridge Fleece Jacket in Glacier Blue available in 4XL?" lost every variant). Detection is
code against the catalog's own product names, never Haiku. Filters are still extracted and reported.
A browsing query still narrows.
"""

from __future__ import annotations

import asyncio

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.understand import plan_claim
from northwind_evals.query_plans import shopper_parts
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_rag.query import Filters, names_catalog_product, rules_plan
from northwind_rag.retrieval import SearchFilter

pytestmark = pytest.mark.req("RAG-04")

NAMED = [
    ("Is the Corvane Ridge Fleece Jacket in Glacier Blue available in 4XL?", "US"),  # EV-042
    ("Does the Tidewick Harbor Rain Jacket in Forest come in 3XL in the relaxed fit?", "US"),
    ("Is the Larchpell Larch Area Rug available in Ochre in 8 x 10 ft?", "US"),
    ("Do the Sand Larchpell Organic Percale sheets come in Twin?", "US"),
]
BROWSING = [
    ("Queen sheet sets in white, please.", "US"),
    ("Show me Tidewick rain jackets in M under $150", "US"),  # a brand alone names no product
    ("Pants under $50", "US"),
]


@pytest.mark.parametrize(("message", "region"), NAMED)
def test_a_named_product_is_detected_and_its_filters_are_still_extracted(
    message: str, region: str
) -> None:
    assert names_catalog_product(message)
    assert rules_plan(message, region).parts[0].filters != Filters()


@pytest.mark.parametrize(("message", "region"), BROWSING)
def test_a_browsing_query_names_no_product(message: str, region: str) -> None:
    assert not names_catalog_product(message)


@pytest.mark.parametrize(("message", "region"), NAMED)
def test_the_gateway_runs_the_plain_search_for_a_named_product(message: str, region: str) -> None:
    plan = rules_plan(message, region)
    assert asyncio.run(plan_claim(plan, message, Caller(region="US"), None)) is None


@pytest.mark.parametrize(("message", "region"), NAMED)
def test_the_eval_runs_the_plain_search_for_a_named_product(message: str, region: str) -> None:
    plan = rules_plan(message, region)
    assert shopper_parts(plan, message, region, FixtureCatalog()) == [(message, SearchFilter())]


def test_a_browsing_query_still_narrows_on_both_paths() -> None:
    message, region = "Queen sheet sets in white, please.", "US"
    plan = rules_plan(message, region)
    claim = asyncio.run(plan_claim(plan, message, Caller(region="US"), None))
    assert claim == {"parts": [{"c": ["White"], "s": ["Queen"]}]}
    narrowed = SearchFilter(colours=("White",), sizes=("Queen",))
    assert shopper_parts(plan, message, region, FixtureCatalog()) == [(message, narrowed)]


def test_an_internal_products_name_is_not_a_key() -> None:
    """Security H2-L1 (hard rule 7): only products a shopper can see are keys, so the exception is
    not an existence oracle for an internal, unreleased name (P0009, "Corvane Ascent Pro Shell").
    Such a message narrows like any browsing query."""
    import json
    from pathlib import Path

    products = Path(__file__).resolve().parents[2] / "data/fixtures/products.jsonl"
    internal = [
        p
        for p in map(json.loads, products.read_text("utf-8").splitlines())
        if p["visibility"] != "public"
    ]
    assert internal  # the fixtures hold at least P0009
    for product in internal:
        assert not names_catalog_product(f"Tell me about the {product['name']}")
    message = "Corvane Ascent jackets in Black in M"
    plan = rules_plan(message, "US")
    assert asyncio.run(plan_claim(plan, message, Caller(region="US"), None)) is not None
    assert shopper_parts(plan, message, "US", FixtureCatalog()) != [(message, SearchFilter())]
