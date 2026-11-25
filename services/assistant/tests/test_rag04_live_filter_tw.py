"""RAG-04 (row #11, decision 0022 section 6), hard rule 2, assistant side: a price or availability
filter becomes a product allow-list only from mcp-catalog's `eligible_products`, as `shopper-query`.

`plan_claim` is the seam: a filter whose live answer is missing is dropped (a filter can only
narrow), never answered from anywhere else. `LiveFilter` is checked against a real mcp-catalog over
HTTP on the verified fixtures. No database, no model.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.understand import QUERY_AGENT, LiveFilter, plan_claim
from northwind_common.mcp_token import bitmap_products, product_bitmap
from northwind_common.tool_scopes import AGENT_AUDIENCE, REGION_CURRENCY
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog, eligible_products
from northwind_mcp_catalog.testing import KEY
from northwind_rag.query import Filters, Part, Price, QueryPlan

pytestmark = pytest.mark.req("RAG-04")

US = Caller(region="US")
UK = Caller(region="UK")
MESSAGE = "Pants under $50"


@dataclass(frozen=True)
class FakeLive(LiveFilter):
    """Answers with a fixed set (or None) and records what it was asked."""

    answer: frozenset[str] | None = None
    seen: list[tuple[str | None, Filters]] = field(default_factory=list)

    async def eligible(self, caller: Caller, filters: Filters) -> frozenset[str] | None:
        self.seen.append((caller.region, filters))
        return self.answer


def fake(answer: frozenset[str] | None) -> FakeLive:
    return FakeLive("http://unused.invalid", b"k" * 40, answer=answer)


def under_fifty() -> QueryPlan:
    price = Price("USD", None, 5000)
    return QueryPlan(parts=(Part(MESSAGE, Filters(price=price)),))


def claim_of(plan: QueryPlan, live: LiveFilter | None, caller: Caller = US) -> Any:
    return asyncio.run(plan_claim(plan, MESSAGE, caller, live))


# ---- the claim: a live answer or nothing --------------------------------------------------------


def test_with_no_live_filter_a_price_filter_is_dropped_not_guessed() -> None:
    """No catalog behind the turn: the price filter has no source, so it does not apply. Nothing
    in the claim is derived from the text, a band or the index."""
    assert claim_of(under_fifty(), None) is None


def test_a_live_answer_becomes_the_products_bitmap_and_nothing_else() -> None:
    answer = frozenset({"P0011", "P0012", "P0101"})
    live = fake(answer)
    claim = claim_of(under_fifty(), live)
    assert claim == {"parts": [{"p": product_bitmap(answer)}]}
    assert bitmap_products(claim["parts"][0]["p"]) == answer
    assert "50" not in str(claim) and "5000" not in str(claim)  # the bound itself is not in it


def test_the_live_filter_is_asked_for_the_parts_own_filters_and_the_callers_region() -> None:
    live = fake(frozenset({"P0011"}))
    claim_of(under_fifty(), live, UK)
    assert live.seen == [("UK", Filters(price=Price("USD", None, 5000)))]


def test_each_part_is_asked_separately_and_keeps_its_own_allow_list() -> None:
    plan = QueryPlan(
        parts=(
            Part("pants", Filters(price=Price("USD", None, 5000))),
            Part("fleece", Filters(colour=("Black",), availability="in_stock")),
        )
    )

    @dataclass(frozen=True)
    class PerPart(FakeLive):
        async def eligible(self, caller: Caller, filters: Filters) -> frozenset[str] | None:
            self.seen.append((caller.region, filters))
            return frozenset({"P0011"}) if filters.price else frozenset({"P0200"})

    live = PerPart("http://unused.invalid", b"k" * 40)
    claim = claim_of(plan, live)
    [first, second] = claim["parts"]
    assert bitmap_products(first["p"]) == {"P0011"}
    assert bitmap_products(second["p"]) == {"P0200"}
    assert second["c"] == ["Black"] and first["q"] == "pants" and second["q"] == "fleece"


def test_a_failed_live_lookup_drops_the_filter_and_keeps_the_rest() -> None:
    plan = QueryPlan(
        parts=(Part(MESSAGE, Filters(colour=("Black",), price=Price("USD", None, 5000))),)
    )
    claim = claim_of(plan, fake(None))
    assert claim == {"parts": [{"c": ["Black"]}]}  # colour still narrows; price was not guessed


def test_an_empty_live_answer_is_kept_it_means_no_product_matches() -> None:
    """Hard rule 10: an empty answer is a real answer, not a failure. The filter stays."""
    claim = claim_of(under_fifty(), fake(frozenset()))
    assert claim == {"parts": [{"p": "0"}]}
    assert bitmap_products("0") == frozenset()


@pytest.mark.parametrize("bad", ["P9999", "P0000", "P0257", "X0011", "p0011", "P011"])
def test_a_product_outside_the_bitmap_drops_the_filter(bad: str) -> None:
    plan = QueryPlan(
        parts=(Part(MESSAGE, Filters(colour=("Black",), price=Price("USD", None, 5000))),)
    )
    claim = claim_of(plan, fake(frozenset({"P0011", bad})))
    assert claim == {"parts": [{"c": ["Black"]}]}


def test_colour_and_size_need_no_live_answer() -> None:
    plan = QueryPlan(parts=(Part(MESSAGE, Filters(colour=("Navy",), size=("M",))),))
    claim = claim_of(plan, fake(None))  # the real LiveFilter answers None for these (tested below)
    assert claim == {"parts": [{"c": ["Navy"], "s": ["M"]}]}  # no "p": nothing price-shaped asked


# ---- LiveFilter, against a real mcp-catalog -----------------------------------------------------


def run_live(store: FixtureCatalog, caller: Caller, filters: Filters) -> frozenset[str] | None:
    with catalog_testing.running(catalog_testing.server(store, key=KEY)) as url:
        return asyncio.run(LiveFilter(url, KEY).eligible(caller, filters))


def test_no_price_or_stock_filter_makes_no_call_at_all() -> None:
    """Nothing is listening here: a call would fail. None means no filter, and no attempt."""
    dead = LiveFilter("http://127.0.0.1:9/mcp", KEY)
    assert asyncio.run(dead.eligible(US, Filters())) is None
    assert asyncio.run(dead.eligible(US, Filters(colour=("Black",), size=("M",)))) is None


@pytest.mark.parametrize("region", sorted(REGION_CURRENCY))
def test_the_answer_is_the_live_stores_for_the_callers_region_and_currency(region: str) -> None:
    store = FixtureCatalog()
    caller = Caller(region=region)  # type: ignore[arg-type]
    filters = Filters(price=Price(REGION_CURRENCY[region], 2000, 9000))
    found = run_live(store, caller, filters)
    want = eligible_products(
        store, region, currency=REGION_CURRENCY[region], price_min=2000, price_max=9000
    )
    assert found == frozenset(want) and found


def test_an_availability_filter_reads_live_stock() -> None:
    store = FixtureCatalog()
    for v in store.variants_of("P0011"):
        store.set_stock(v.variant_id, "US", 0)
    found = run_live(store, US, Filters(availability="in_stock"))
    assert found is not None and "P0011" not in found and found


def test_a_live_change_is_seen_by_the_next_filter_with_no_reingest() -> None:
    store = FixtureCatalog()
    filters = Filters(price=Price("USD", None, 100))
    with catalog_testing.running(catalog_testing.server(store, key=KEY)) as url:
        live = LiveFilter(url, KEY)
        before = asyncio.run(live.eligible(US, filters))
        [variant, *_] = store.variants_of("P0011")
        store.set_price(variant.variant_id, "US", "0.99", "USD")
        after = asyncio.run(live.eligible(US, filters))
    assert before is not None and "P0011" not in before
    assert after is not None and "P0011" in after


def test_the_filter_is_dropped_when_the_server_is_unreachable() -> None:
    dead = LiveFilter("http://127.0.0.1:9/mcp", KEY)
    assert asyncio.run(dead.eligible(US, Filters(price=Price("USD", None, 5000)))) is None


def test_the_filter_is_dropped_when_the_key_is_wrong() -> None:
    with catalog_testing.running(catalog_testing.server(key=KEY)) as url:
        wrong = LiveFilter(url, b"w" * 40)
        assert asyncio.run(wrong.eligible(US, Filters(price=Price("USD", None, 5000)))) is None


def test_the_filter_asks_as_the_query_agent_which_is_never_the_assistants_model() -> None:
    assert QUERY_AGENT == "shopper-query"
    assert QUERY_AGENT != "shopper-assistant"
    assert AGENT_AUDIENCE[QUERY_AGENT] == "shopper"
