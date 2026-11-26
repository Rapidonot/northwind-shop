"""CHK-07 stage B, gap tests (test-writer): who gets mcp-policy, its token, the prompt, the damaged
wording and the status matcher's edges. Written from the criteria and the public interfaces. No
cassette, no spend, no database: the real CLI against a scripted model, or options only."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CatalogEndpoint
from northwind_assistant.orders_client import OrdersEndpoint, PolicyEndpoint
from northwind_assistant.prompt import ORDERS_RULES, system_prompt
from northwind_assistant.rag_client import AGENT, RagEndpoint
from northwind_assistant.tools import ALLOWED_TOOL
from northwind_assistant.turn import TurnEvent, assistant_options, stream_answer
from northwind_common import catalog_tool, order_tool, policy_tool
from northwind_common.llm import agent_runtime
from northwind_common.mcp_token import (
    CATALOG_SERVER,
    ORDERS_SERVER,
    POLICY_SERVER,
    RAG_SERVER,
    TTL_SECONDS,
    TokenError,
    verify_for,
)
from northwind_common.returns_policy import CONDITIONS, REGIONS, evaluate
from northwind_common.tool_scopes import AGENT_TOOLS, FORBIDDEN_FRAGMENTS
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_llm_replay.scrub import canonical, scrub
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_orders import testing as orders_testing
from northwind_mcp_policy import testing as policy_testing
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("CHK-07")

SESSION = "d" * 32
DEAD = "http://127.0.0.1:9/mcp"  # nothing listens: a server that is attached would be missed
SOURCE = RetrievedChunk("c1", "document", "POL-001", None, "Orders ship within 2 business days.")
RETURN_TOOLS = {*order_tool.ALLOWED_TOOLS, *policy_tool.ALLOWED_TOOLS}


def caller(region: str, shopper: str | None = None) -> Caller:
    return Caller(region=region, session=SESSION, shopper_id=shopper)  # type: ignore[arg-type]


# ---- the status matcher's own file, loaded by path (importlib mode) ----


def _load_matcher() -> ModuleType:
    path = Path(__file__).with_name("test_chk07_recorded_status.py")
    spec = importlib.util.spec_from_file_location("chk07_recorded_status_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MATCHER = _load_matcher()
problems = MATCHER.problems
codes_in = MATCHER.codes_in


# ---- options: who gets the policy server ----


def _options(region: str, *, orders: bool, policy: bool, catalog: bool = True, tmp: Path) -> Any:
    who = caller(region, "S0188")
    return assistant_options(
        who,
        DEAD,
        RagEndpoint(DEAD, KEY).mint(who),
        agent_runtime(base_url="http://127.0.0.1:1", root=tmp),
        (DEAD, CatalogEndpoint(DEAD, KEY).mint(who)) if catalog else None,
        (DEAD, OrdersEndpoint(DEAD, KEY).mint(who) or "") if orders else None,
        (DEAD, PolicyEndpoint(DEAD, KEY).mint(who) or "") if policy else None,
    )


@pytest.mark.parametrize("region", REGIONS)
def test_options_without_orders_or_policy_hold_neither_server_nor_rules(
    region: str, tmp_path: Path
) -> None:
    options = _options(region, orders=False, policy=False, tmp=tmp_path)
    assert set(options.mcp_servers) == {RAG_SERVER, CATALOG_SERVER}
    assert not set(options.allowed_tools) & RETURN_TOOLS
    assert ORDERS_RULES not in options.system_prompt
    assert options.system_prompt == system_prompt(region)  # type: ignore[arg-type]


@pytest.mark.parametrize("region", REGIONS)
def test_options_with_orders_but_no_policy_have_no_policy_server_or_tool(
    region: str, tmp_path: Path
) -> None:
    options = _options(region, orders=True, policy=False, tmp=tmp_path)
    assert set(options.mcp_servers) == {RAG_SERVER, CATALOG_SERVER, ORDERS_SERVER}
    assert not set(options.allowed_tools) & set(policy_tool.ALLOWED_TOOLS)
    assert set(order_tool.ALLOWED_TOOLS) <= set(options.allowed_tools)


@pytest.mark.parametrize("region", REGIONS)
def test_a_policy_server_without_orders_is_refused_with_or_without_a_catalog(
    region: str, tmp_path: Path
) -> None:
    for catalog in (True, False):
        with pytest.raises(ValueError, match="policy"):
            _options(region, orders=False, policy=True, catalog=catalog, tmp=tmp_path)


@pytest.mark.parametrize("region", REGIONS)
def test_the_signed_in_options_are_exactly_the_assistants_scope_and_no_money_tool(
    region: str, tmp_path: Path
) -> None:
    """Hard rule 8: refund, capture, assign (and label) never reach the assistant."""
    options = _options(region, orders=True, policy=True, tmp=tmp_path)
    offered: dict[str, set[str]] = {}
    for name in options.allowed_tools:
        prefix, server, tool = name.split("__")
        assert prefix == "mcp"
        offered.setdefault(server, set()).add(tool)
    assert offered == {s: set(t) for s, t in AGENT_TOOLS[AGENT].items() if t}
    names = [t for tools in offered.values() for t in tools]
    for fragment in (*FORBIDDEN_FRAGMENTS[AGENT], "label", "payment", "cancel", "ship"):
        assert not [n for n in names if fragment in n], fragment
    assert options.tools == [] and options.permission_mode == "dontAsk"
    assert set(options.mcp_servers) == {RAG_SERVER, CATALOG_SERVER, ORDERS_SERVER, POLICY_SERVER}


@pytest.mark.parametrize("region", REGIONS)
def test_the_return_rules_are_in_the_signed_in_prompt_only_in_every_region(region: Any) -> None:
    signed = system_prompt(region, signed_in=True)
    anonymous = system_prompt(region)
    assert ORDERS_RULES in signed and ORDERS_RULES not in anonymous
    assert signed.count(ORDERS_RULES) == 1
    assert "write nothing before or between tool calls" in signed
    for word in ("evaluate_return_eligibility", "request_return", "get_order", "policy_data"):
        assert word in signed and word not in anonymous
    assert signed.endswith(f"The shopper is in the {region} region.")
    assert anonymous.endswith(f"The shopper is in the {region} region.")


# ---- the endpoint and its token ----


@pytest.mark.parametrize("region", REGIONS)
def test_the_policy_token_names_agent_audience_server_region_and_shopper(region: str) -> None:
    endpoint = PolicyEndpoint(DEAD, KEY)
    assert endpoint.mint(caller(region)) is None
    token = endpoint.mint(caller(region, "S0188"))
    assert token is not None
    claims = verify_for(token, key=KEY, server=POLICY_SERVER)
    assert (claims.agent, claims.aud, claims.srv) == (AGENT, "shopper", POLICY_SERVER)
    assert (claims.region, claims.shp) == (region, "S0188")
    assert 0 < claims.exp - _now() <= TTL_SECONDS
    for server in (RAG_SERVER, CATALOG_SERVER, ORDERS_SERVER):
        with pytest.raises(TokenError):
            verify_for(token, key=KEY, server=server)
    with pytest.raises(TokenError):
        verify_for(token, key=b"x" * 40, server=POLICY_SERVER)


def _now() -> int:
    import time

    return int(time.time())


def test_each_mint_is_a_fresh_turn_and_the_two_endpoints_differ() -> None:
    who = caller("US", "S0188")
    first = PolicyEndpoint(DEAD, KEY).mint(who)
    second = PolicyEndpoint(DEAD, KEY).mint(who)
    assert first is not None and second is not None and first != second
    orders = OrdersEndpoint(DEAD, KEY).mint(who)
    assert orders is not None
    with pytest.raises(TokenError):
        verify_for(orders, key=KEY, server=POLICY_SERVER)
    turns = {verify_for(t, key=KEY, server=POLICY_SERVER).turn for t in (first, second)}
    assert len(turns) == 2


def test_the_policy_endpoint_is_frozen_and_keeps_its_key_out_of_its_repr() -> None:
    endpoint = PolicyEndpoint(DEAD, b"k" * 40)
    with pytest.raises(dataclasses.FrozenInstanceError):
        endpoint.url = "x"  # type: ignore[misc]
    assert "kkkk" not in repr(endpoint) and "kkkk" not in str(endpoint)


# ---- stream_answer: anonymous and half-wired turns ----


class OneSource:
    def search(self, query: str, who: Caller) -> list[RetrievedChunk]:
        return [SOURCE]


MINTED: list[tuple[str, str | None]] = []


class SpyPolicy(PolicyEndpoint):
    def mint(self, caller: Caller) -> str | None:
        token = super().mint(caller)
        MINTED.append(("policy", token))
        return token


class SpyOrders(OrdersEndpoint):
    def mint(self, caller: Caller) -> str | None:
        token = super().mint(caller)
        MINTED.append(("orders", token))
        return token


@pytest.fixture(autouse=True)
def _clear_minted() -> Iterator[None]:
    MINTED.clear()
    yield
    MINTED.clear()


def ask(
    who: Caller,
    tmp: Path,
    llm: ScriptedLLM,
    *,
    orders: str | None,
    policy: str | None,
) -> list[TurnEvent]:
    runtime = agent_runtime(base_url=llm.url, root=tmp)
    catalog = catalog_testing.server(key=KEY)

    async def collect(rag: RagEndpoint, catalog_url: str) -> list[TurnEvent]:
        return [
            e
            async for e in stream_answer(
                "When do orders ship?",
                who,
                rag,
                runtime,
                CatalogEndpoint(catalog_url, KEY),
                orders=None if orders is None else SpyOrders(orders, KEY),
                policy=None if policy is None else SpyPolicy(policy, KEY),
            )
        ]

    with (
        serve_retriever(OneSource(), Caller, key=KEY) as (url, _),
        catalog_testing.running(catalog) as catalog_url,
    ):
        return asyncio.run(collect(RagEndpoint(url, KEY), catalog_url))


def tool_names(request: dict[str, Any]) -> set[str]:
    return {tool["name"] for tool in request["body"]["tools"]}


def system_of(request: dict[str, Any]) -> str:
    system = request["body"]["system"]
    return system if isinstance(system, str) else "".join(part["text"] for part in system)


@pytest.mark.parametrize("region", REGIONS)
def test_an_anonymous_turn_in_every_region_gets_no_policy_server_tool_token_or_rules(
    region: str, tmp_path: Path
) -> None:
    bodies = []
    for n, (orders, policy) in enumerate(((None, None), (DEAD, DEAD))):
        with ScriptedLLM() as llm:
            llm.reply_stream("Orders ship within 2 business days [POL-001].")
            ask(caller(region), tmp_path / str(n), llm, orders=orders, policy=policy)
        bodies.append(llm.requests[0])
    # What the spies saw: no token of any kind for an anonymous caller, and the policy endpoint
    # was never even asked.
    assert all(token is None for _, token in MINTED)
    assert "policy" not in [kind for kind, _ in MINTED]
    assert canonical(scrub(bodies[0]["body"])) == canonical(scrub(bodies[1]["body"]))
    assert not tool_names(bodies[1]) & RETURN_TOOLS
    assert ORDERS_RULES not in system_of(bodies[1])
    assert "policy_data" not in system_of(bodies[1])
    assert tool_names(bodies[1]) == {ALLOWED_TOOL, *catalog_tool.ALLOWED_TOOLS}


@pytest.mark.parametrize("region", REGIONS)
def test_a_signed_in_turn_with_a_policy_endpoint_but_no_orders_gets_no_policy(
    region: str, tmp_path: Path
) -> None:
    with ScriptedLLM() as llm:
        llm.reply_stream("Orders ship within 2 business days [POL-001].")
        events = ask(caller(region, "S0188"), tmp_path, llm, orders=None, policy=DEAD)
    from northwind_assistant.turn import TurnError

    assert not [e for e in events if isinstance(e, TurnError)]  # a quiet turn, not a crash
    assert "policy" not in [kind for kind, _ in MINTED]  # never asked without orders
    assert all(token is None for _, token in MINTED)
    request = llm.requests[0]
    assert not tool_names(request) & RETURN_TOOLS
    assert ORDERS_RULES not in system_of(request)


@pytest.mark.parametrize("region", REGIONS)
def test_a_signed_in_turn_in_every_region_mints_one_policy_token_for_that_region(
    region: str, tmp_path: Path
) -> None:
    with (
        ScriptedLLM() as llm,
        orders_testing.running(orders_testing.server(key=KEY)) as orders,
        policy_testing.running(policy_testing.server(key=KEY)) as policy,
    ):
        llm.reply_stream("Which order number is it?")
        ask(caller(region, "S0188"), tmp_path, llm, orders=orders, policy=policy)
    request = llm.requests[0]
    assert RETURN_TOOLS <= tool_names(request)
    assert ORDERS_RULES in system_of(request)
    assert [k for k, _ in MINTED] == ["orders", "policy"]  # once each, orders first
    policy_token = dict(MINTED)["policy"]
    assert policy_token is not None
    claims = verify_for(policy_token, key=KEY, server=POLICY_SERVER)
    assert (claims.region, claims.shp, claims.agent) == (region, "S0188", AGENT)
    assert not tool_names(request) & {n for n in tool_names(request) if "refund" in n}


# ---- the damaged_on_arrival wording ----


@pytest.mark.parametrize("region", REGIONS)
def test_damaged_on_arrival_is_ambiguous_for_a_person_at_any_age_and_in_every_region(
    region: str,
) -> None:
    from datetime import timedelta

    from northwind_common.returns_policy import AS_OF, WINDOW_DAYS

    last = WINDOW_DAYS[region]
    for days in (0, 1, last, last + 1, 400):
        decision = evaluate(
            region=region,
            subcategory="jacket",
            delivered_on=AS_OF - timedelta(days=days),
            condition="damaged_on_arrival",
        )
        assert (decision.outcome, decision.reason) == ("ambiguous", "damaged_on_arrival"), days
        assert decision.next_step == "A person needs to review it."
        assert [f.document_id for f in decision.facts] == [f"POL-RETURNS-{region}"]
    text = decision.explanation
    assert "damaged on arrival" in text and text.endswith(".")
    assert not re.search(r"\d", text)  # no figure (hard rules 2 and 5)
    assert not re.search(r"approv|refund|will be|accepted|eligible", text, re.I)
    assert (
        text
        != evaluate(
            region=region, subcategory="jacket", delivered_on=AS_OF, condition="not_stated"
        ).explanation
    )


def test_damaged_on_arrival_is_a_condition_the_evaluator_knows_and_not_delivered_wins() -> None:
    from northwind_common.returns_policy import AS_OF

    assert "damaged_on_arrival" in CONDITIONS
    never = evaluate(
        region="US", subcategory="jacket", delivered_on=None, condition="damaged_on_arrival"
    )
    assert (never.outcome, never.reason) == ("ineligible", "not_delivered")
    on_the_day_after = evaluate(
        region="US", subcategory="jacket", delivered_on=AS_OF, condition="damaged_on_arrival"
    )
    assert on_the_day_after.outcome == "ambiguous"


# ---- the status matcher at its edges ----

MATCHER_RIGHT = [
    # a condition, then a true status in the same sentence
    "O000800 arrived damaged and was delivered on 2026-10-26.",
    "The jacket in O000800 Arrived Damaged, so a person will review the return.",
    "The boots in O000800 arrived  broken.",
    # "not yet shipped", true with no carrier scan
    "O000406 has not yet shipped.",
    "Order O000406 hasn't been dispatched yet.",
    "O000406 was never shipped.",
    "O000406 has no tracking scans because it has not shipped.",
    "Once O000406 has shipped, I can show its scans.",
    "O000517 has not been shipped yet.",
    "O000418 hasn't shipped, and a person is looking at it.",
]
MATCHER_WRONG = [
    # a condition does not hide a false status beside it
    "O000928 arrived damaged and is delivered.",
    "O000800 arrived damaged and is in transit.",
    "O000800 arrived damaged and hasn't shipped.",
    # a plain "arrived", also with the condition only after a comma or a full stop
    "O000800 arrived, damaged.",
    "O000928 arrived. It was damaged.",
    # the carrier has the parcel
    "O000267 has been dispatched.",
    "O000267 has not been dispatched.",
    "O000267 hasn't shipped yet.",
    "O000928 was never shipped.",
    "O000898 has not yet shipped.",
    "O000800 has not yet shipped.",
    "O000406 will be shipped soon.",
    # two orders in one sentence: the second claim is false
    "O000418 hasn't shipped, but O000800 hasn't shipped.",
    # another shopper's order, and one that does not exist
    "O000001 has not shipped.",
    "O000999 has not shipped.",
    # "confirmed" for a label_created order that has not been scanned
    "Order O000406 is processing.",
]


@pytest.mark.parametrize("answer", MATCHER_RIGHT)
def test_the_matcher_accepts_the_new_rules_true_edges(answer: str) -> None:
    assert problems(answer) == [], answer
    assert codes_in(answer) == []


@pytest.mark.parametrize("answer", MATCHER_WRONG)
def test_the_matcher_finds_the_new_rules_false_edges(answer: str) -> None:
    assert problems(answer), answer


@pytest.mark.parametrize(
    "answer",
    [
        "The return is needs_review.",
        "Reason: damaged_on_arrival.",
        "Outcome: condition_not_stated.",
        "Its status is `label_created`.",
    ],
)
def test_a_return_code_in_an_answer_is_seen_as_a_code(answer: str) -> None:
    assert codes_in(answer)


@pytest.mark.parametrize(
    "answer",
    [
        "A person needs to review it, because the item arrived damaged.",
        "Your return is requested and needs review.",
        "The returns policy does not say what happens to an item damaged on arrival.",
    ],
)
def test_plain_return_words_are_not_a_code(answer: str) -> None:
    assert codes_in(answer) == []
