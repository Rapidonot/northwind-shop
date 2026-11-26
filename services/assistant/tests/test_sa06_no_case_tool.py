"""Row #23 (SA-06), the user's hard constraint (PRD §7, hard rule 8, NFR-01): the Shopper Assistant
gets no case-creating or case-assigning tool. It never requests the handoff either: the gateway
does, from the portal's button or the deterministic trigger, and code in the backend queues the
case. These tests read code and change nothing."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.turn import assistant_options
from northwind_common import tool_scopes
from northwind_common.llm import agent_runtime

pytestmark = [pytest.mark.req("SA-06"), pytest.mark.req("NFR-01")]

ROOT = Path(__file__).resolve().parents[3]
ASSISTANT_SRC = ROOT / "services" / "assistant" / "src" / "northwind_assistant"
GATEWAY_SRC = ROOT / "services" / "gateway" / "src" / "northwind_gateway"
CARE = ROOT / "services" / "backend" / "src" / "northwind_backend" / "agents" / "care.py"
HANDOFF = ROOT / "services" / "backend" / "src" / "northwind_backend" / "handoff.py"
FORBIDDEN = ("case", "handoff", "escalat", "assign", "ticket", "staff")
US = Caller(region="US", session="0" * 32)


def _names(options: Any) -> list[str]:
    return [*options.mcp_servers, *options.allowed_tools]


@pytest.mark.parametrize("signed_in", [False, True])
def test_the_assistants_turn_offers_no_case_or_handoff_tool(
    signed_in: bool, tmp_path: Path
) -> None:
    caller = Caller(region="US", session="0" * 32, shopper_id="S0103" if signed_in else None)
    options = assistant_options(
        caller,
        "http://127.0.0.1:9/mcp",
        "rag-token",
        agent_runtime(base_url="http://127.0.0.1:1", root=tmp_path),
        ("http://127.0.0.1:8/mcp", "catalog-token"),
        ("http://127.0.0.1:7/mcp", "orders-token") if signed_in else None,
    )
    names = _names(options)
    assert names, "the turn must list its tools for this pin to mean anything"
    bad = [n for n in names if any(f in n.casefold() for f in FORBIDDEN)]
    assert bad == []
    assert options.tools == []  # no built-in tool either


def test_no_shopper_audience_agent_is_scoped_to_a_case_tool() -> None:
    for agent, audience in tool_scopes.AGENT_AUDIENCE.items():
        if audience != "shopper":
            continue
        for server, tools in tool_scopes.AGENT_TOOLS.get(agent, {}).items():
            assert not any(f in server for f in ("case", "handoff")), (agent, server)
            bad = [t for t in tools if any(f in t.casefold() for f in FORBIDDEN)]
            assert bad == [], (agent, server, bad)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_assistant_cannot_reach_the_handoff_desk() -> None:
    """No module of the assistant imports the backend or the gateway's handoff."""
    for path in sorted(ASSISTANT_SRC.rglob("*.py")):
        reached = {
            m for m in _imports(path) if m.startswith(("northwind_backend", "northwind_gateway"))
        }
        assert reached == set(), (path.name, reached)


@pytest.mark.parametrize("name", ["handoff.py", "handoff_trigger.py"])
def test_the_gateways_handoff_runs_no_model_of_its_own(name: str) -> None:
    path = GATEWAY_SRC / name
    imported = _imports(path)
    assert not any(m.startswith("claude_agent_sdk") for m in imported), imported
    assert "agent_options" not in path.read_text(encoding="utf-8")


def test_the_care_agent_never_gets_a_write_tool() -> None:
    """Both Care variants list their allowed tools literally; neither holds `create_case` or any
    queue write. Code calls the queue (BE-07; row #23's desk)."""
    source = CARE.read_text(encoding="utf-8")
    lists = re.findall(r"allowed_tools=\[([^\]]*)\]", source)
    assert sorted(lists) == ["ORDER_TOOL_NAME, SUMMARY_TOOL_NAME", "SUMMARY_TOOL_NAME"], lists
    desk = HANDOFF.read_text(encoding="utf-8")
    assert "allowed_tools" not in desk and "@tool(" not in desk
