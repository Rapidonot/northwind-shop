"""RAG-04, row #11: test-writer pass over the code added after the security review (finding H2).

Written from decision 0022's "Exception" section and the public interfaces: the named-product
exception on both paths, the frozen part texts and vouch list, and the search ledger's cap.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.understand import Understander, Understood, plan_claim
from northwind_evals import query_plans
from northwind_evals.cli import ITEMS, RED_TEAM, _frozen_queries
from northwind_evals.items import load_items
from northwind_evals.query_plans import part_texts, shopper_parts
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_rag.ledger import MAX_SEARCHES, SearchLedger
from northwind_rag.embedding import frozen_key
from northwind_rag.query import (
    Filters,
    Part,
    QueryPlan,
    names_catalog_product,
    rules_plan,
)
from northwind_rag.retrieval import SearchFilter

pytestmark = pytest.mark.req("RAG-04")

REPO = Path(__file__).resolve().parents[2]
VECTORS = Path("data/embeddings/fastembed-BAAI-bge-small-en-v1.5-384.jsonl")

NAMED = "Is the Corvane Ridge Fleece Jacket in Glacier Blue available in 4XL?"
NAMED_LOWER = NAMED.lower()
BRAND_ONLY = "Show me Tidewick rain jackets in M"
BROWSING = "Queen sheet sets in white, please."


# 1. The named-product exception


def test_a_named_product_is_detected_case_insensitively() -> None:
    assert names_catalog_product(NAMED)
    assert names_catalog_product(NAMED_LOWER)
    assert names_catalog_product(NAMED.upper())


@pytest.mark.parametrize("message", [BRAND_ONLY, "Corvane jackets please", BROWSING, ""])
def test_a_brand_alone_or_no_brand_names_no_product(message: str) -> None:
    assert not names_catalog_product(message)


def test_detection_consults_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_: Any, **__: Any) -> Understood:
        raise AssertionError("a model was consulted")

    monkeypatch.setattr(Understander, "plan", boom)
    assert names_catalog_product(NAMED)
    assert not names_catalog_product(BRAND_ONLY)


def _model_plan(message: str) -> QueryPlan:
    """A model-sourced plan with rewritten parts, each narrowing."""
    return QueryPlan(
        parts=(
            Part("fleece jacket glacier blue", Filters(colour=("Blue",))),
            Part("fleece jacket 4XL", Filters(size=("4XL",))),
        ),
        source="model",
    )


@pytest.mark.parametrize("message", [NAMED, NAMED_LOWER])
def test_both_paths_give_the_plain_search_for_a_rules_plan(message: str) -> None:
    plan = rules_plan(message, "US")
    assert asyncio.run(plan_claim(plan, message, Caller(region="US"), None)) is None
    assert shopper_parts(plan, message, "US", FixtureCatalog()) == [(message, SearchFilter())]


def test_both_paths_give_the_plain_search_for_a_model_plan_with_rewritten_parts() -> None:
    plan = _model_plan(NAMED)
    assert asyncio.run(plan_claim(plan, NAMED, Caller(region="US"), None)) is None
    assert shopper_parts(plan, NAMED, "US", FixtureCatalog()) == [(NAMED, SearchFilter())]


def test_a_browsing_query_still_narrows_on_both_paths() -> None:
    plan = rules_plan(BROWSING, "US")
    assert asyncio.run(plan_claim(plan, BROWSING, Caller(region="US"), None)) is not None
    assert shopper_parts(plan, BROWSING, "US", FixtureCatalog()) != [(BROWSING, SearchFilter())]


def test_a_brand_only_query_still_narrows_on_both_paths() -> None:
    plan = rules_plan(BRAND_ONLY, "US")
    assert not plan.parts[0].filters.is_empty()
    assert asyncio.run(plan_claim(plan, BRAND_ONLY, Caller(region="US"), None)) is not None
    assert shopper_parts(plan, BRAND_ONLY, "US", FixtureCatalog()) != [(BRAND_ONLY, SearchFilter())]


def test_extraction_is_unchanged_for_a_named_message() -> None:
    filters = rules_plan(NAMED, "US").parts[0].filters
    assert filters != Filters()
    assert filters.colour and filters.size


def test_the_two_paths_agree_on_every_eval_item() -> None:
    items = load_items(REPO / ITEMS)
    store = FixtureCatalog()
    caller = Caller(region="US")
    compared = 0
    for item in items:
        plan = rules_plan(item.query, item.region)
        # plan_claim has no live filter here, so skip the plans with a price or stock filter.
        if any(p.filters.price is not None or p.filters.availability for p in plan.parts):
            continue
        claim = asyncio.run(
            plan_claim(plan, item.query, Caller(region=cast(Any, item.region)), None)
        )
        parts = shopper_parts(plan, item.query, item.region, store)
        plain = parts == [(item.query, SearchFilter())]
        assert (claim is None) == plain, item.id
        compared += 1
    assert caller and compared > 200


# 2. The vouch list and frozen part texts


def _needs_model_named(items: list[Any]) -> Any:
    for item in items:
        if rules_plan(item.query, item.region).needs_model and names_catalog_product(item.query):
            return item
    raise AssertionError("no routed eval item names a product")


def _patch_plan(monkeypatch: pytest.MonkeyPatch, texts: list[str]) -> None:
    async def plan(self: Any, message: str, region: str, base_url: str) -> Understood:
        parts = tuple(Part(t, Filters(colour=("Blue",))) for t in texts)
        return Understood(QueryPlan(parts=parts, source="model"), "model", 1)

    monkeypatch.setattr(Understander, "plan", plan)


def test_part_texts_lists_a_rewritten_part_and_not_the_raw_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = load_items(REPO / ITEMS)
    routed = next(
        i
        for i in items
        if rules_plan(i.query, i.region).needs_model and not names_catalog_product(i.query)
    )
    _patch_plan(monkeypatch, ["a rewritten part", routed.query])
    assert part_texts([(routed.query, routed.region)], tmp_path) == ["a rewritten part"]


def test_part_texts_skips_a_message_that_names_a_product(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    named = _needs_model_named(load_items(REPO / ITEMS))
    _patch_plan(monkeypatch, ["a rewritten part"])
    assert part_texts([(named.query, named.region)], tmp_path) == []


def test_part_texts_skips_a_message_that_needs_no_model(tmp_path: Path) -> None:
    # No replay proxy and no cassette: a message the rules settle has no part text.
    assert part_texts([(BROWSING, "US")], tmp_path) == []


def test_part_texts_exits_on_a_replay_miss(tmp_path: Path) -> None:
    items = load_items(REPO / ITEMS)
    routed = next(
        i
        for i in items
        if rules_plan(i.query, i.region).needs_model and not names_catalog_product(i.query)
    )
    with pytest.raises(SystemExit):
        part_texts([(routed.query, routed.region)], tmp_path)
    with pytest.raises(SystemExit):
        part_texts([(routed.query, routed.region)], tmp_path, suite="assistant-redteam")
    with pytest.raises(SystemExit):
        with query_plans.planner([(routed.query, routed.region)], tmp_path):
            pass


def test_the_committed_plans_give_part_texts_that_differ_from_their_messages() -> None:
    items = load_items(REPO / ITEMS)
    texts = part_texts([(i.query, i.region) for i in items], REPO / "cassettes")
    assert texts
    assert not set(texts) & {i.query for i in items}


def test_frozen_queries_include_the_eval_and_red_team_part_texts() -> None:
    items = load_items(REPO / ITEMS)
    frozen = set(_frozen_queries(items))
    eval_texts = part_texts([(i.query, i.region) for i in items], REPO / "cassettes")
    red = [json.loads(line) for line in (REPO / RED_TEAM).read_text("utf-8").splitlines()]
    red_texts = part_texts(
        [(r["query"], r["region"]) for r in red], REPO / "cassettes", suite="assistant-redteam"
    )
    assert eval_texts and red_texts
    assert set(eval_texts) <= frozen
    assert set(red_texts) <= frozen


def intent_keys() -> set[str]:
    """Rows #30 and #22: exactly the 70 labelled messages and the part texts Haiku rewrote the
    routed ones into (the user's stop 5, 2026-11-25). Nothing else is exempt from the cap below."""
    asked = [
        (str(row[key]), str(row["region"]))
        for name, key in (("sa04_prompts", "prompt"), ("sa03_conversations", "query"))
        for row in map(
            json.loads, (REPO / f"evals/fixtures/{name}.jsonl").read_text("utf-8").splitlines()
        )
    ]
    parts = part_texts(asked, REPO / "cassettes", suite="assistant-intents")
    return {frozen_key(text) for text in [*(message for message, _ in asked), *parts]}


def new_vector_problems(new: set[str], wanted: set[str], exempt: set[str]) -> list[str]:
    problems = [f"stray vector {key}" for key in sorted(new - wanted)]
    others = new - exempt
    if len(others) > 5:  # row #11's own count (3 eval part texts, 2 red-team)
        problems.append(f"{len(others)} new vectors beyond the exempt ones")
    return problems


def test_a_stray_vector_is_a_problem_even_beside_exempt_ones() -> None:
    exempt = {"m1", "m2"}
    assert new_vector_problems({"m1", "m2"}, exempt, exempt) == []
    assert new_vector_problems({"m1", "stray"}, exempt, exempt) == ["stray vector stray"]
    # exempt only names keys: one that is not a frozen query is still a stray
    assert new_vector_problems({"m1"}, set(), {"m1"}) == ["stray vector m1"]


def test_at_most_five_new_vectors_beyond_the_exempt_ones() -> None:
    exempt = {f"m{n}" for n in range(75)}
    five, six = {f"q{n}" for n in range(5)}, {f"q{n}" for n in range(6)}
    assert new_vector_problems(exempt | five, exempt | five, exempt) == []
    problems = new_vector_problems(exempt | six, exempt | six, exempt)
    assert problems == ["6 new vectors beyond the exempt ones"]


def test_the_exempt_keys_are_this_rows_75_texts() -> None:
    assert len(intent_keys()) == 75


def test_every_new_frozen_vector_is_the_key_of_a_frozen_query() -> None:
    shown = subprocess.run(  # noqa: S603 - read-only git, fixed arguments
        ["git", "show", f"origin/main:{VECTORS}"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if shown.returncode != 0:
        # The repo's convention (test_rag08b_citations.py): GitHub's python job has no origin/main;
        # only the integrity jobs fetch history. There a related, weaker check runs against main:
        # `Guard / eval-integrity-main` (main's code) refuses a changed or removed frozen vector,
        # and `CI / eval-gate`'s "Eval integrity" step vouches that every added one is the vector
        # of a known chunk or frozen query. The strict form (only frozen queries, at most 5 new)
        # runs wherever origin/main exists, as in a local `make ci`.
        pytest.skip("origin/main is not available in this checkout")
    base = shown.stdout
    old = {json.loads(line)["key"] for line in base.splitlines() if line.strip()}
    now = [
        json.loads(line)["key"]
        for line in (REPO / VECTORS).read_text("utf-8").splitlines()
        if line.strip()
    ]
    new = set(now) - old
    wanted = {frozen_key(t) for t in _frozen_queries(load_items(REPO / ITEMS))}
    # Not an exact count: once a row merges, origin/main holds its keys and they are not `new`.
    assert new_vector_problems(new, wanted, intent_keys()) == []


# 3. The ledger (RAG-10 cap)


def _clock(now: list[float]) -> Callable[[], float]:
    return lambda: now[0]


def test_one_turns_count_outlives_the_earlier_expiring_of_two_tokens() -> None:
    for first, second in [(1050, 1100), (1100, 1050)]:
        now = [1000.0]
        ledger = SearchLedger(clock=_clock(now))
        assert asyncio.run(ledger.take("t", first))
        assert asyncio.run(ledger.take("t", second))
        now[0] = 1060.0  # the earlier token has expired, the later one has not
        assert ledger.used("t") == 2
        assert asyncio.run(ledger.take("t", 1100))
        assert not asyncio.run(ledger.take("t", 1100))  # the cap held across the first expiry


def test_a_refused_take_records_the_later_expiry() -> None:
    now = [1000.0]
    ledger = SearchLedger(clock=lambda: now[0])
    for _ in range(MAX_SEARCHES):
        assert asyncio.run(ledger.take("t", 1050))
    assert not asyncio.run(ledger.take("t", 1100))
    now[0] = 1060.0
    assert ledger.used("t") == MAX_SEARCHES
    assert not asyncio.run(ledger.take("t", 1100))


def test_the_cap_is_never_raised() -> None:
    now = [1000.0]
    ledger = SearchLedger(clock=lambda: now[0])
    granted = 0
    for exp in [1050, 1100, 1200, 1050, 1300, 1100, 1400, 1050]:
        granted += asyncio.run(ledger.take("t", exp))
    assert granted == MAX_SEARCHES == 3
    assert ledger.used("t") == MAX_SEARCHES
