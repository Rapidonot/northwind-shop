"""Row #23 (SA-06, hard rule 8) phase B: every shopper-audience agent forbids "case", and none of
its tools, on any server, has a name containing a forbidden fragment."""

from __future__ import annotations

import pytest

from northwind_common import tool_scopes as scopes

pytestmark = pytest.mark.req("SA-06")

SHOPPER_AGENTS = sorted(a for a, aud in scopes.AGENT_AUDIENCE.items() if aud == "shopper")


def test_there_are_shopper_agents_to_check() -> None:
    assert {"shopper-assistant", "shopper-portal", "shopper-query"} <= set(SHOPPER_AGENTS)


@pytest.mark.parametrize("agent", SHOPPER_AGENTS)
def test_case_is_a_forbidden_fragment_for_every_shopper_agent(agent: str) -> None:
    assert "case" in scopes.FORBIDDEN_FRAGMENTS[agent]


@pytest.mark.parametrize("agent", SHOPPER_AGENTS)
def test_the_earlier_fragments_are_still_forbidden(agent: str) -> None:
    assert {"refund", "capture", "assign"} <= set(scopes.FORBIDDEN_FRAGMENTS[agent])


@pytest.mark.parametrize("agent", SHOPPER_AGENTS)
def test_no_shopper_tool_name_contains_a_forbidden_fragment(agent: str) -> None:
    for server, tools in scopes.AGENT_TOOLS[agent].items():
        for tool in tools:
            for fragment in scopes.FORBIDDEN_FRAGMENTS[agent]:
                assert fragment not in tool.lower(), (agent, server, tool)
            for extra in ("handoff", "escalat", "ticket", "staff"):
                assert extra not in tool.lower(), (agent, server, tool)


@pytest.mark.parametrize("agent", SHOPPER_AGENTS)
def test_allowed_tools_never_returns_a_case_tool_for_a_shopper(agent: str) -> None:
    for server in scopes.AGENT_TOOLS[agent]:
        assert not [t for t in scopes.allowed_tools(agent, server) if "case" in t.lower()]
    assert not scopes.allowed_tools(agent, "northwind-cases")
