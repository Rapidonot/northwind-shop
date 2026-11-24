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
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from northwind_assistant.understand import Understander
from northwind_common.mcp_token import RAG_SERVER, mint, product_bitmap
from northwind_llm_replay.proxy import RunningProxy, encode_test_id
from northwind_llm_replay.runner import proxy_config
from northwind_mcp_catalog.live import FixtureCatalog, eligible_products
from northwind_rag.query import REGION_CURRENCY, QueryPlan, rules_plan
from northwind_rag.retrieval import SearchFilter

SUITE = "query-understanding"
TEST_ID = "query-understanding"

Parts = list[tuple[str, SearchFilter]]
# A plan from a replayed reply: Haiku's, or a refused reply's rules plan, which replays the same.
REPLAYED = frozenset({"model", "invalid"})


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


_CHECK_KEY = b"query-plan-size-check-only-never-a-real-key!"


def shopper_parts(plan: QueryPlan, message: str, region: str, store: FixtureCatalog) -> Parts:
    """`search_parts`, then the gateway's own fallbacks (security review L4), so the gate scores the
    search a shopper gets: a plan that narrows nothing is the plain search; a product list outside
    the bitmap drops that part's live filter; a claim the token cannot hold is the plain search."""
    if plan.is_plain(message):
        return [(message, SearchFilter())]
    parts = search_parts(plan, region, store)
    claim_parts: list[dict[str, object]] = []
    kept: Parts = []
    for text, narrow in parts:
        claim: dict[str, object] = {}
        if text != message:
            claim["q"] = text
        if narrow.colours:
            claim["c"] = list(narrow.colours)
        if narrow.sizes:
            claim["s"] = list(narrow.sizes)
        if narrow.products is not None:
            try:
                claim["p"] = product_bitmap(narrow.products)
            except ValueError:
                narrow = SearchFilter(colours=narrow.colours, sizes=narrow.sizes)
        claim_parts.append(claim)
        kept.append((text, narrow))
    if claim_parts == [{}]:
        return [(message, SearchFilter())]
    try:
        mint(
            audience="shopper",
            region=region,
            agent="shopper-assistant",
            key=_CHECK_KEY,
            server=RAG_SERVER,
            plan={"parts": claim_parts},
        )
    except ValueError:
        return [(message, SearchFilter())]  # `mint_planned` falls back the same way
    return kept


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
            failed: Counter[str] = Counter()
            for message, region in needed:
                understood = asyncio.run(understand.plan(message, region, base_url))
                plans[(message, region)] = understood.plan
                if understood.path not in REPLAYED:
                    failed[understood.path] += 1
            state = running.proxy.state
            if state.misses:
                raise SystemExit(
                    f"{len(state.misses)} query-understanding replay misses: "
                    f"re-record with `make record SUITE={SUITE}`"
                )
            if failed or state.fallback_attempts:
                # Security review M1: any other failure (a refused request, a proxy that did not
                # answer) would quietly score the rules plan, a search no shopper gets.
                raise SystemExit(
                    f"query-understanding replay did not answer every plan: {dict(failed)}"
                    f"{' and a fallback beta' if state.fallback_attempts else ''}"
                )

    def parts(message: str, region: str) -> Parts:
        plan = plans.get((message, region)) or rules_plan(message, region)
        return shopper_parts(plan, message, region, store)

    yield parts


def part_texts(
    messages: Sequence[tuple[str, str]], cassettes: Path = Path("cassettes")
) -> list[str]:
    """Row #11 (security review M2): the rewritten part texts the planned first search embeds, from
    the replayed plans, so they get frozen vectors like every other query (decision 0012). A part
    that searches the raw message needs none: the message is frozen already."""
    texts: list[str] = []
    with planner(messages, cassettes) as parts:
        for message, region in messages:
            if rules_plan(message, region).needs_model:
                texts += [text for text, _ in parts(message, region) if text != message]
    return list(dict.fromkeys(texts))
