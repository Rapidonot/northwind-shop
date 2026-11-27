"""SA-01 PR 2, condition (1) and the code half of condition (3) from the approved plan.

- `checkout.py` is imported only by `app.py`: no assistant, MCP, RAG, backend or other gateway
  module can reach the confirmation issuer.
- `checkout.py` never names a model entry point: not `run_order_placement` (which uses
  `ROUTINE_MODEL`), `run_subagent`, `anthropic_client`, `agent_options` or a model constant.
  (`orders_place` itself imports the agent runtime; the runtime guard in
  `test_sa01_pr2_checkout.py` proves no entry point is reached.)
- No agent's tool names a checkout, pay or confirmation action.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from northwind_common.tool_scopes import AGENT_TOOLS

pytestmark = pytest.mark.req("SA-01")

REPO = Path(__file__).resolve().parents[4]
CHECKOUT = REPO / "services/gateway/src/northwind_gateway/checkout.py"
SCANNED = (
    REPO / "services",
    REPO / "agents-external",
    REPO / "partner-agent",
    REPO / "evals",
    REPO / "data",
)
MODEL_NAMES = {
    "run_order_placement",
    "run_subagent",
    "anthropic_client",
    "agent_options",
    "agent_runtime",
    "ROUTINE_MODEL",
    "ASSISTANT_MODEL",
    "JUDGE_MODEL",
}


def importers_of_checkout() -> list[str]:
    found = []
    for root in SCANNED:
        for path in sorted(root.rglob("*.py")) if root.exists() else ():
            if "tests" in path.relative_to(REPO).parts:
                continue
            tree = ast.parse(path.read_text(), str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    names = {alias.name for alias in node.names}
                    if module == "northwind_gateway.checkout" or (
                        module == "northwind_gateway" and "checkout" in names
                    ):
                        found.append(path.relative_to(REPO).as_posix())
                elif isinstance(node, ast.Import) and any(
                    a.name == "northwind_gateway.checkout" for a in node.names
                ):
                    found.append(path.relative_to(REPO).as_posix())
    return found


def test_checkout_is_imported_only_by_the_app() -> None:
    assert importers_of_checkout() == ["services/gateway/src/northwind_gateway/app.py"]


def test_checkout_names_no_model_entry_point() -> None:
    tree = ast.parse(CHECKOUT.read_text())
    named = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    named |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    named |= {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not named & MODEL_NAMES
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not {
        m for m in modules if m and (m.startswith("northwind_common.llm") or m.endswith(".runtime"))
    }


def test_no_agent_has_a_checkout_pay_or_confirmation_tool() -> None:
    names = {name for row in AGENT_TOOLS.values() for tools in row.values() for name in tools}
    for name in names:
        assert not any(
            word in name for word in ("checkout", "pay", "confirm", "capture", "authorize")
        ), name
