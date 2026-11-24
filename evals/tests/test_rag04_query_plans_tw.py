"""RAG-04 (row #11, decision 0022 section 9): the eval harness change is minimal and pinned.

From the decision: the gate, `make eval-snapshot` and the stale check score the planned first
search (`_live`); RAG-03's arm comparison (`_arms`) and the enrichment report's E1 rule keep the
plain search they were fixed on (`_live_plain`). Haiku's plans replay from the `query-understanding`
suite and a replay miss stops the run (never a quiet fall-back to the rules plan). Price and
availability filters resolve through `eligible_products` on the fixtures' live store (hard rule 2).

No database and no LLM: retrievers and the planner are faked, and the replay test uses an empty
cassette directory, so every model call misses and nothing is sent anywhere.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from northwind_evals import cli, query_plans
from northwind_evals.harness import RETRIEVAL_K
from northwind_evals.query_plans import SUITE, planner, search_parts
from northwind_mcp_catalog.live import FixtureCatalog, eligible_products
from northwind_rag.query import (
    REGION_CURRENCY,
    Filters,
    Part,
    Price,
    QueryPlan,
    rules_plan,
)
from northwind_rag.retrieval import NO_FILTER, RetrievedChunk, SearchFilter

pytestmark = pytest.mark.req("RAG-04")

NEEDS_MODEL = "A black fleece in M and a queen duvet under $200"
RULES_ONLY = "Pants under $50"


def item(item_id: str, query: str, region: str = "US") -> Any:
    return SimpleNamespace(id=item_id, query=query, region=region)


def chunk(source: str) -> RetrievedChunk:
    return RetrievedChunk(f"{source}#0", "product", source, None, "t")


# ---- which call sites use which search ----------------------------------------------------------


def callees() -> dict[str, set[str]]:
    """For each function in the harness CLI, the plain names it calls."""
    tree = ast.parse(Path(inspect.getfile(cli)).read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            found[node.name] = {
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }
    return found


def callers_of(name: str) -> set[str]:
    return {fn for fn, called in callees().items() if name in called and fn != name}


def test_the_planned_search_is_used_by_the_gate_the_snapshot_and_the_stale_check_only() -> None:
    assert callers_of("_live") == {"cmd_snapshot", "_build_candidate", "cmd_gate"}


def test_the_plain_search_is_used_by_the_arm_comparison_and_the_enrichment_report_only() -> None:
    assert callers_of("_live_plain") == {"_arms", "cmd_enrichment_report"}


@pytest.mark.parametrize("name", ["cmd_snapshot", "_build_candidate", "cmd_gate"])
def test_the_gate_side_never_scores_the_plain_search(name: str) -> None:
    called = callees()[name]
    assert "_live" in called and "_live_plain" not in called


@pytest.mark.parametrize("name", ["_arms", "cmd_enrichment_report"])
def test_the_frozen_side_never_scores_the_planned_search(name: str) -> None:
    called = callees()[name]
    assert "_live_plain" in called
    assert "_live" not in called and "retrieve_planned" not in called


def test_the_stale_check_compares_against_the_planned_search() -> None:
    called = callees()["_build_candidate"]
    assert "_live" in called and "_stale_ids" in called


def test_the_two_searches_are_different_functions_with_the_same_shape() -> None:
    assert cli._live is not cli._live_plain
    assert list(inspect.signature(cli._live).parameters) == list(
        inspect.signature(cli._live_plain).parameters
    )


# ---- the two searches, behaviourally ------------------------------------------------------------


@contextmanager
def fake_planner(log: list[Any]) -> Iterator[Any]:
    def make(messages: Any, cassettes: Any = None) -> Any:
        log.append(("planner", list(messages)))

        @contextmanager
        def ctx() -> Iterator[Any]:
            def parts(message: str, region: str) -> Any:
                return [(f"planned:{message}", SearchFilter(colours=("Black",)))]

            yield parts

        return ctx()

    yield make


def test_live_runs_the_planned_search_for_each_item_as_a_shopper_in_its_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[Any] = []
    with fake_planner(log) as make:
        monkeypatch.setattr(query_plans, "planner", make)
        monkeypatch.setattr(cli, "cached", lambda e: e)

        def planned(conn: Any, emb: Any, parts: Any, *, region: Any, audience: Any, k: int) -> Any:
            log.append(("planned", list(parts), region, audience, k))
            return [chunk("P1")]

        def plain(*_: Any, **__: Any) -> Any:
            raise AssertionError("the planned search must not run the plain one")

        monkeypatch.setattr(cli, "retrieve_planned", planned)
        monkeypatch.setattr(cli, "retrieve_context", plain)
        items = [item("EV-1", "pants", "UK"), item("EV-2", "duvet", "EU")]
        found = cli._live(None, None, items)  # type: ignore[arg-type]
    assert list(found) == ["EV-1", "EV-2"]
    assert log[0] == ("planner", [("pants", "UK"), ("duvet", "EU")])
    assert log[1] == (
        "planned",
        [("planned:pants", SearchFilter(colours=("Black",)))],
        "UK",
        "shopper",
        RETRIEVAL_K,
    )
    assert log[2][2:] == ("EU", "shopper", RETRIEVAL_K)


def test_live_plain_is_the_raw_query_search_and_never_plans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[Any] = []

    def plain(conn: Any, emb: Any, query: str, *, region: Any, audience: Any, k: int) -> Any:
        log.append((query, region, audience, k))
        return [chunk("P1")]

    def boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("the plain search must not plan")

    monkeypatch.setattr(cli, "cached", lambda e: e)
    monkeypatch.setattr(cli, "retrieve_context", plain)
    monkeypatch.setattr(cli, "retrieve_planned", boom)
    monkeypatch.setattr(query_plans, "planner", boom)
    found = cli._live_plain(None, None, [item("EV-1", "pants under $50", "UK")])  # type: ignore[arg-type]
    assert found == {"EV-1": [chunk("P1")]}
    assert log == [("pants under $50", "UK", "shopper", RETRIEVAL_K)]


def test_the_arm_comparison_hybrid_arm_is_the_plain_search(monkeypatch: pytest.MonkeyPatch) -> None:
    def other(*_: Any, **__: Any) -> Any:
        return [chunk("P9")]

    def plain(conn: Any, emb: Any, query: str, *, region: Any, audience: Any, k: int) -> Any:
        return [chunk("P3")]

    def boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("the arm comparison must not plan")

    monkeypatch.setattr(cli, "cached", lambda e: e)
    monkeypatch.setattr(cli, "dense_search", other)
    monkeypatch.setattr(cli, "keyword_search", other)
    monkeypatch.setattr(cli, "retrieve_context", plain)
    monkeypatch.setattr(cli, "retrieve_planned", boom)
    monkeypatch.setattr(query_plans, "planner", boom)
    arms = cli._arms(None, None, [item("EV-1", "pants under $50")])  # type: ignore[arg-type]
    assert arms["hybrid"] == {"EV-1": ["P3"]}


# ---- a replay miss stops the run ----------------------------------------------------------------


def test_a_replay_miss_raises_system_exit_and_never_uses_the_rules_plan(tmp_path: Path) -> None:
    assert rules_plan(NEEDS_MODEL, "US").needs_model
    with pytest.raises(SystemExit) as stop, planner([(NEEDS_MODEL, "US")], tmp_path):
        pytest.fail("the planner must not yield after a miss")
    assert "replay misses" in str(stop.value)
    assert f"make record SUITE={SUITE}" in str(stop.value)


def test_one_miss_among_rules_messages_still_stops_the_run(tmp_path: Path) -> None:
    messages = [(RULES_ONLY, "US"), (NEEDS_MODEL, "US"), ("Queen sheet sets in white", "US")]
    with pytest.raises(SystemExit), planner(messages, tmp_path):
        pytest.fail("the planner must not yield after a miss")


def test_the_suite_is_the_recorded_one() -> None:
    assert SUITE == "query-understanding"


def test_a_run_of_rules_messages_needs_no_cassette_and_no_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class NoProxy:
        def __init__(self, *_: Any, **__: Any) -> None:
            raise AssertionError("no message needs the model: no proxy may start")

    monkeypatch.setattr(query_plans, "RunningProxy", NoProxy)
    messages = [(RULES_ONLY, "US"), ("Is it machine washable?", "UK")]
    with planner(messages, tmp_path / "missing") as parts:
        got = parts(RULES_ONLY, "US")
        plain = parts("Is it machine washable?", "UK")
    expected = eligible_products(FixtureCatalog(), "US", currency="USD", price_max=5000)
    assert got == [(RULES_ONLY, SearchFilter(products=frozenset(expected)))]
    assert plain == [("Is it machine washable?", NO_FILTER)]


def test_the_planner_yields_the_rules_plan_search_for_a_message_without_a_model_need(
    tmp_path: Path,
) -> None:
    with planner([("Queen sheet sets in white, please.", "US")], tmp_path) as parts:
        found = parts("Queen sheet sets in white, please.", "US")
    assert found == [
        ("Queen sheet sets in white, please.", SearchFilter(colours=("White",), sizes=("Queen",)))
    ]


# ---- search_parts: price and availability from the live store ------------------------------------


def test_a_price_bound_is_resolved_through_eligible_products() -> None:
    store = FixtureCatalog()
    plan = rules_plan("Pants under $50", "US")
    [(text, narrow)] = search_parts(plan, "US", store)
    assert text == "Pants under $50"
    want = frozenset(eligible_products(store, "US", currency="USD", price_max=5000))
    assert narrow == SearchFilter(products=want) and want


@pytest.mark.parametrize(
    ("price", "kwargs"),
    [
        (Price("USD", 2000, None), {"price_min": 2000}),
        (Price("USD", None, 9000), {"price_max": 9000}),
        (Price("USD", 2000, 9000), {"price_min": 2000, "price_max": 9000}),
    ],
)
def test_each_bound_shape_is_passed_through(price: Price, kwargs: dict[str, Any]) -> None:
    store = FixtureCatalog()
    plan = QueryPlan(parts=(Part("pants", Filters(price=price)),))
    [(_, narrow)] = search_parts(plan, "US", store)
    assert narrow.products == frozenset(eligible_products(store, "US", currency="USD", **kwargs))


def test_availability_is_resolved_through_eligible_products() -> None:
    store = FixtureCatalog()
    for v in store.variants_of("P0011"):
        store.set_stock(v.variant_id, "US", 0)
    plan = QueryPlan(parts=(Part("fleece", Filters(availability="in_stock")),))
    [(_, narrow)] = search_parts(plan, "US", store)
    assert narrow.products == frozenset(
        eligible_products(store, "US", currency="USD", in_stock=True)
    )
    assert narrow.products is not None and "P0011" not in narrow.products


def test_price_and_availability_are_combined_for_the_same_variant() -> None:
    store = FixtureCatalog()
    plan = QueryPlan(
        parts=(Part("pants", Filters(price=Price("USD", None, 5000), availability="in_stock")),)
    )
    [(_, narrow)] = search_parts(plan, "US", store)
    want = eligible_products(store, "US", currency="USD", price_max=5000, in_stock=True)
    assert narrow.products == frozenset(want)


@pytest.mark.parametrize("region", sorted(REGION_CURRENCY))
def test_the_regions_own_currency_and_stock_are_used(region: str) -> None:
    store = FixtureCatalog()
    plan = QueryPlan(
        parts=(Part("pants", Filters(price=Price(REGION_CURRENCY[region], None, 6000))),)
    )
    [(_, narrow)] = search_parts(plan, region, store)
    want = eligible_products(store, region, currency=REGION_CURRENCY[region], price_max=6000)
    assert narrow.products == frozenset(want)


def test_a_bound_is_never_converted_between_currencies() -> None:
    """A plan's price stated in another currency is looked up in the region's own, as stated."""
    store = FixtureCatalog()
    plan = QueryPlan(parts=(Part("pants", Filters(price=Price("USD", None, 5000))),))
    [(_, narrow)] = search_parts(plan, "UK", store)
    want = eligible_products(store, "UK", currency="GBP", price_max=5000)
    assert narrow.products == frozenset(want)


def test_a_live_change_is_seen_by_the_next_call() -> None:
    store = FixtureCatalog()
    plan = QueryPlan(parts=(Part("pants", Filters(price=Price("USD", None, 100))),))
    [(_, before)] = search_parts(plan, "US", store)
    [variant, *_] = store.variants_of("P0011")
    store.set_price(variant.variant_id, "US", "0.99", "USD")
    [(_, after)] = search_parts(plan, "US", store)
    assert before.products is not None and "P0011" not in before.products
    assert after.products is not None and "P0011" in after.products


def test_no_price_or_availability_means_no_allow_list_and_colour_and_size_pass_through() -> None:
    store = FixtureCatalog()
    plan = QueryPlan(parts=(Part("navy fleece", Filters(colour=("Navy",), size=("M", "L"))),))
    [(text, narrow)] = search_parts(plan, "US", store)
    assert text == "navy fleece"
    assert narrow == SearchFilter(colours=("Navy",), sizes=("M", "L"), products=None)


def test_each_part_gets_its_own_allow_list_in_order() -> None:
    store = FixtureCatalog()
    plan = QueryPlan(
        parts=(
            Part("pants", Filters(price=Price("USD", None, 5000))),
            Part("fleece", Filters(colour=("Black",))),
            Part("duvet", Filters(availability="in_stock")),
        )
    )
    found = search_parts(plan, "US", store)
    assert [text for text, _ in found] == ["pants", "fleece", "duvet"]
    assert found[0][1].products is not None
    assert found[1][1] == SearchFilter(colours=("Black",))
    assert found[2][1].products is not None
    assert found[0][1].products != found[2][1].products


def test_search_parts_takes_a_plan_a_region_and_the_store_and_nothing_to_read_an_index() -> None:
    assert list(inspect.signature(search_parts).parameters) == ["plan", "region", "store"]
