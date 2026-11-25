"""Tool scoping by agent (PRD §7; NFR-01, hard rule 8)."""

from __future__ import annotations

import pytest

from northwind_common import tool_scopes

pytestmark = [pytest.mark.req("NFR-01"), pytest.mark.req("CC-10")]


def test_the_assistant_gets_exactly_retrieve_context_and_no_resource() -> None:
    # #10b, decision D1 (the user, 2026-11-13): the five read-only catalog tools join, as PRD §7
    # lists them; attached at #12. Still exact: nothing else, no resource.
    assert tool_scopes.AGENT_TOOLS["shopper-assistant"] == {
        "northwind-rag": frozenset({"retrieve_context"}),
        # Row #16 (CHK-01): the four cart tools join, as PRD §7 lists "cart tools". Still exact.
        "northwind-catalog": frozenset(
            {
                "search_products",
                "get_product",
                "get_variant",
                "check_stock",
                "get_price",
                "get_cart",
                "add_to_cart",
                "remove_from_cart",
                "set_cart_quantity",
            }
        ),
        # Row #27 (CHK-06): `get_order` (own orders), as PRD §7 lists it. Still exact.
        "northwind-orders": frozenset({"get_order"}),
        # Row #27 PR 2 (CHK-07): as PRD §7 lists it. Still exact.
        "northwind-policy": frozenset({"evaluate_return_eligibility"}),
    }
    assert tool_scopes.AGENT_RESOURCES["shopper-assistant"] == {"northwind-rag": frozenset()}
    assert tool_scopes.allowed_resources("shopper-assistant", "northwind-policy") == frozenset()
    assert tool_scopes.AGENT_AUDIENCE["shopper-assistant"] == "shopper"


def test_no_agent_has_a_tool_its_prd_row_forbids() -> None:
    for agent, fragments in tool_scopes.FORBIDDEN_FRAGMENTS.items():
        names = {t for tools in tool_scopes.AGENT_TOOLS[agent].values() for t in tools}
        assert not [n for n in names if any(f in n.lower() for f in fragments)]


def test_an_unknown_agent_or_server_gets_nothing() -> None:
    assert tool_scopes.allowed_tools("refund-bot", "northwind-rag") == frozenset()
    assert tool_scopes.allowed_tools("shopper-assistant", "northwind-unknown") == frozenset()
    assert tool_scopes.allowed_resources("nobody", "northwind-rag") == frozenset()


def test_every_agent_has_one_audience_and_rows_in_both_tables() -> None:
    assert set(tool_scopes.AGENT_TOOLS) == set(tool_scopes.AGENT_AUDIENCE)
    assert set(tool_scopes.AGENT_RESOURCES) == set(tool_scopes.AGENT_AUDIENCE)
    assert set(tool_scopes.AGENT_AUDIENCE.values()) <= {"shopper", "staff"}


def test_only_staff_may_read_the_index_manifest() -> None:
    readers = {
        agent
        for agent in tool_scopes.agents()
        if "northwind-rag://index/manifest" in tool_scopes.allowed_resources(agent, "northwind-rag")
    }
    assert readers and all(tool_scopes.AGENT_AUDIENCE[a] == "staff" for a in readers)


@pytest.mark.req("CHK-06")
def test_get_order_is_the_assistants_alone() -> None:
    """Row #27: no other agent has `get_order` or any northwind-orders tool, and no agent has an
    order write: no list, refund, cancel or return tool exists on the server."""
    holders = {
        agent
        for agent in tool_scopes.agents()
        if tool_scopes.allowed_tools(agent, "northwind-orders")
    }
    assert holders == {"shopper-assistant"}
    assert tool_scopes.ORDER_TOOLS == frozenset({"get_order"})
    for agent in tool_scopes.agents():
        for server, tools in tool_scopes.AGENT_TOOLS[agent].items():
            if server != "northwind-orders":
                assert "get_order" not in tools


@pytest.mark.req("CHK-07")
def test_the_policy_server_has_one_tool_holder_and_one_resource_reader() -> None:
    """Row #27 PR 2 (D14): the tool is the assistant's alone, the resources the gateway's agent's,
    which no model holds. Exactly the returns and shipping policies of the four regions."""
    agents = tool_scopes.agents()
    assert {a for a in agents if tool_scopes.allowed_tools(a, "northwind-policy")} == {
        "shopper-assistant"
    }
    assert {a for a in agents if tool_scopes.allowed_resources(a, "northwind-policy")} == {
        "shopper-portal"
    }
    assert tool_scopes.allowed_resources("shopper-portal", "northwind-policy") == {
        f"northwind-policy://{kind}/{region}"
        for kind in ("returns", "shipping")
        for region in ("US", "CA", "UK", "EU")
    }
    for agent in agents:
        for server, tools in tool_scopes.AGENT_TOOLS[agent].items():
            if server != "northwind-policy":
                assert "evaluate_return_eligibility" not in tools
