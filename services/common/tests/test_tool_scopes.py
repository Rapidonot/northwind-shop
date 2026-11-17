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
    }
    assert tool_scopes.allowed_resources("shopper-assistant", "northwind-rag") == frozenset()
    assert tool_scopes.AGENT_AUDIENCE["shopper-assistant"] == "shopper"


def test_no_agent_has_a_tool_its_prd_row_forbids() -> None:
    for agent, fragments in tool_scopes.FORBIDDEN_FRAGMENTS.items():
        names = {t for tools in tool_scopes.AGENT_TOOLS[agent].values() for t in tools}
        assert not [n for n in names if any(f in n.lower() for f in fragments)]


def test_an_unknown_agent_or_server_gets_nothing() -> None:
    assert tool_scopes.allowed_tools("refund-bot", "northwind-rag") == frozenset()
    assert tool_scopes.allowed_tools("shopper-assistant", "northwind-orders") == frozenset()
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
