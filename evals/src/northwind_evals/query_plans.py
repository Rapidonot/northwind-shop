"""The planned first search for eval items, as a shopper gets it (RAG-04, row #11, decision 0022).

The gate and `make eval-snapshot` score what a shopper's first search returns; since row #11 that
search runs the message's query plan. Plans come from the same code the assistant runs: the rules,
and for a message they mark `needs_model`, Haiku's reply replayed from the `query-understanding`
suite. Replay only: a cassette miss stops the run (it never falls back to the rules plan quietly).
Price and availability filters are resolved against the fixtures' live store, the same values
mcp-catalog serves at rest (hard rule 2).

Every other eval path keeps the plain hybrid search: RAG-03's arm comparison and the enrichment
report's E1 rule were fixed on it before measuring.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from northwind_assistant.understand import Understander
from northwind_llm_replay.proxy import RunningProxy, encode_test_id
from northwind_llm_replay.runner import proxy_config
from northwind_mcp_catalog.live import FixtureCatalog, eligible_products
from northwind_rag.query import REGION_CURRENCY, QueryPlan, rules_plan
from northwind_rag.retrieval import SearchFilter

SUITE = "query-understanding"
TEST_ID = "query-understanding"

Parts = list[tuple[str, SearchFilter]]


def search_parts(plan: QueryPlan, region: str, store: FixtureCatalog) -> Parts:
    currency = REGION_CURRENCY[region]
    parts: Parts = []
    for part in plan.parts:
        f = part.filters
        products = None
        if f.price is not None or f.availability == "in_stock":
            products = frozenset(
                eligible_products(
                    store,
                    region,
                    currency=currency,
                    price_min=None if f.price is None else f.price.min,
                    price_max=None if f.price is None else f.price.max,
                    in_stock=f.availability == "in_stock",
                )
            )
        parts.append((part.text, SearchFilter(colours=f.colour, sizes=f.size, products=products)))
    return parts


@contextmanager
def planner(
    messages: Sequence[tuple[str, str]], cassettes: Path = Path("cassettes")
) -> Iterator[Callable[[str, str], Parts]]:
    """`parts(message, region)` for each eval message. A replay proxy runs only if a message needs
    the model; any miss raises SystemExit once the plans are made."""
    store = FixtureCatalog()
    needed = [(m, r) for m, r in messages if rules_plan(m, r).needs_model]
    plans: dict[tuple[str, str], QueryPlan] = {}
    if needed:
        config = proxy_config("replay", SUITE, cassette_root=cassettes, log_path=None)
        with RunningProxy(config) as running:
            understand = Understander(live=False)
            base_url = f"{running.url}/_t/{encode_test_id(TEST_ID)}"
            for message, region in needed:
                understood = asyncio.run(understand.plan(message, region, base_url))
                plans[(message, region)] = understood.plan
            if running.proxy.state.misses:
                raise SystemExit(
                    f"{len(running.proxy.state.misses)} query-understanding replay misses: "
                    f"re-record with `make record SUITE={SUITE}`"
                )

    def parts(message: str, region: str) -> Parts:
        plan = plans.get((message, region)) or rules_plan(message, region)
        return search_parts(plan, region, store)

    yield parts
