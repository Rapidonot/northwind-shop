"""OPS-1: the "Agent system" map, generated from the code on every read, never hand-written.

What each part comes from:
- MCP servers and their tools: `tool_scopes.AGENT_TOOLS` (the assistant's servers; query
  understanding's `eligible_products`; the portal's cart tools). A catalog's cart writes collapse
  to one line, "+ cart write tools", named from `catalog_tool.CART_TOOL_NAMES` less the read
  `get_cart`.
- Each AI agent's model: the `northwind_common.llm` constant its own module imports and uses (an AST
  read of the module's source), labelled by the constant's value: `claude-sonnet-5-5` is "Sonnet".
  The eval-only judge is `llm.JUDGE_MODEL`. The module itself is not run, but finding its source
  (`importlib.util.find_spec`) imports its parent packages, as the gateway's own imports already do
  for most of them (security review I2); the paths are constants, never request input.
- Every other edge: an import that must be in the code (`Evidence`). Remove the call from the code
  and its edge leaves the map, and the portal's layout test fails until the layout follows.
The text on each box (titles, one-line descriptions) is presentation; which boxes and lines exist,
their kinds, models and tools are the code's. The files this reads are frozen for RECORD-ALL: it
reads them and never changes them.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal

from northwind_common import catalog_tool, llm, tool_scopes

Kind = Literal["entry", "ai", "mcp", "code", "fake", "eval", "end"]
LEGEND_ORDER: tuple[Kind, ...] = ("ai", "mcp", "code", "fake", "eval")
MODEL_CONSTANTS = ("ASSISTANT_MODEL", "ROUTINE_MODEL", "JUDGE_MODEL")
LLM_MODULE = "northwind_common.llm"
CART_WRITES_LABEL = "cart write tools"
CODE_PORT = "code port"

# The servers' ids on the map are their tool-scope names.
CATALOG = "northwind-catalog"
SERVERS: Mapping[str, str] = {
    CATALOG: "",
    "northwind-rag": "Hybrid search: catalog, reviews and policies",
    "northwind-orders": "Signed-in only · order store + carrier (fake) tracking",
    "northwind-policy": "Signed-in only · returns & shipping policies",
}


@dataclass(frozen=True)
class Evidence:
    """`importer` imports from `imported`, and `names`, if any, are module-level names `imported`
    assigns (a tool name constant, say)."""

    importer: str
    imported: str
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class NodeSpec:
    id: str
    kind: Kind
    title: str
    subtitle: str = ""
    # ai: the module whose model constant labels it; anything else: a module that must exist.
    module: str = ""


@dataclass(frozen=True)
class EdgeSpec:
    source: str
    target: str
    evidence: tuple[Evidence, ...]
    label: str | None = None


@dataclass
class AgentMap:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    legend: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": self.nodes, "edges": self.edges, "legend": self.legend}


# -- reading the code -------------------------------------------------------------------------


@cache
def _tree(module: str) -> ast.Module | None:
    """The module's source, parsed, or None when it is not installed (the eval-only judge's module
    is not a gateway dependency)."""
    try:
        spec = importlib.util.find_spec(module)
    except ModuleNotFoundError:
        return None
    if spec is None or spec.origin is None or not spec.origin.endswith(".py"):
        return None
    with open(spec.origin, encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=spec.origin)


def module_exists(module: str) -> bool:
    return _tree(module) is not None


def imports(importer: str, imported: str) -> bool:
    tree = _tree(importer)
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == imported:
            return True
        if isinstance(node, ast.Import) and any(alias.name == imported for alias in node.names):
            return True
    return False


def assigns(module: str, names: Iterable[str]) -> bool:
    tree = _tree(module)
    if tree is None:
        return False
    assigned = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    return set(names) <= assigned


def holds(evidence: Evidence) -> bool:
    return imports(evidence.importer, evidence.imported) and assigns(
        evidence.imported, evidence.names
    )


def model_constants(module: str) -> tuple[str, ...]:
    """The model constants `module` imports from `northwind_common.llm` and also uses."""
    tree = _tree(module)
    if tree is None:
        return ()
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == LLM_MODULE
        for alias in node.names
        if alias.name in MODEL_CONSTANTS
    }
    used = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return tuple(name for name in MODEL_CONSTANTS if name in imported and name in used)


def model_label(model: str) -> str:
    """`claude-sonnet-5-5` -> "Sonnet"; anything not in that form is shown as it is."""
    found = re.fullmatch(r"claude-([a-z]+)-.*", model)
    return found.group(1).capitalize() if found else model


def models(module: str) -> list[str]:
    labels = [model_label(str(getattr(llm, name))) for name in model_constants(module)]
    return list(dict.fromkeys(labels))


# -- the map's parts ----------------------------------------------------------------------------

GW = "northwind_gateway"
AS = "northwind_assistant"
BE = "northwind_backend"

NODES: tuple[NodeSpec, ...] = (
    # The shopper's chat (gateway chat.py -> the assistant's turn).
    NodeSpec("shopper-chat", "entry", "Shopper chat", "Entry point", f"{GW}.chat"),
    NodeSpec(
        "query-understanding",
        "ai",
        "Query understanding",
        "Rules first; a model if unsure",
        f"{AS}.understand",
    ),
    NodeSpec("shopper-assistant", "ai", "Shopper Assistant", "Customer-facing agent", f"{AS}.turn"),
    NodeSpec(
        "output-check",
        "code",
        "Output check",
        "Removes unbacked price, stock and policy claims and planted promotions",
        f"{AS}.output_check",
    ),
    NodeSpec("answer", "end", "Answer", "Shopper reply", f"{AS}.turn"),
    # The handoff (gateway handoff.py -> backend handoff.py -> the Care agent).
    NodeSpec("talk-to-person", "entry", "Talk to a person", "Entry point", f"{GW}.handoff"),
    NodeSpec(
        "handoff-trigger",
        "code",
        "Handoff trigger",
        "Runs before any model call",
        f"{GW}.handoff_trigger",
    ),
    NodeSpec("handoff-desk", "code", "Handoff desk", "Builds the case", f"{BE}.handoff"),
    NodeSpec(
        "care-subagent", "ai", "Care agent", "Handoff and escalation summaries", f"{BE}.agents.care"
    ),
    NodeSpec("case-queue", "fake", "Case queue", "Staff queue", f"{BE}.fakes.handoffs"),
    # Pay (demo): gateway checkout.py runs orders_place's steps in code, on the fakes. No model.
    NodeSpec("pay-demo", "entry", "Pay (demo)", "Entry point", f"{GW}.checkout"),
    NodeSpec("checkout-validate", "code", "Validate", "", f"{BE}.agents.orders_place"),
    NodeSpec("checkout-hold", "code", "Inventory hold", "", f"{BE}.inventory.memory"),
    NodeSpec("checkout-fraud", "fake", "Fraud", "", f"{BE}.fakes.fraud"),
    NodeSpec("checkout-authorize", "fake", "Payment authorize", "", f"{BE}.fakes.payment"),
    NodeSpec("checkout-commit", "code", "Inventory commit", "", f"{BE}.inventory.memory"),
    NodeSpec("checkout-capture", "fake", "Payment capture", "", f"{BE}.fakes.payment"),
    NodeSpec("checkout-confirm", "code", "Confirm", "", f"{BE}.agents.orders_place"),
    # A staff request (python -m northwind_backend.orchestrator): read-only, nothing escalates.
    NodeSpec(
        "staff-request",
        "entry",
        "Staff request (command)",
        "Entry point",
        f"{BE}.orchestrator.__main__",
    ),
    NodeSpec(
        "orchestrator",
        "ai",
        "Orchestrator",
        "Planner; runs its subagents in parallel",
        f"{BE}.orchestrator.plan",
    ),
    NodeSpec(
        "orders-subagent",
        "ai",
        "Orders agent",
        "Read-only: fraud and payment",
        f"{BE}.agents.orders_check",
    ),
    NodeSpec("returns-subagent", "ai", "Returns agent", "", f"{BE}.agents.returns_track"),
    NodeSpec("order-fraud", "fake", "Fraud", "", f"{BE}.fakes.fraud"),
    NodeSpec("order-payment", "fake", "Payment", "", f"{BE}.fakes.payment"),
    NodeSpec("order-carrier", "fake", "Carrier", "", f"{BE}.fakes.carrier"),
    # An escalated order's case (python -m northwind_backend.care).
    NodeSpec(
        "escalated-order",
        "entry",
        "Escalated order (command)",
        "Entry point",
        f"{BE}.care.__main__",
    ),
    NodeSpec("escalation", "code", "Escalation", "Recommended action", f"{BE}.agents.escalation"),
)

FLOW: tuple[EdgeSpec, ...] = (
    EdgeSpec("shopper-chat", "query-understanding", (Evidence(f"{GW}.chat", f"{AS}.understand"),)),
    EdgeSpec(
        "query-understanding", "shopper-assistant", (Evidence(f"{AS}.turn", f"{AS}.understand"),)
    ),
    EdgeSpec("shopper-assistant", "output-check", (Evidence(f"{AS}.turn", f"{AS}.output_check"),)),
    EdgeSpec("output-check", "answer", (Evidence(f"{AS}.turn", f"{AS}.output_check"),)),
    # The button posts to the handoff route; a chat message asking for a person is matched by code.
    EdgeSpec(
        "talk-to-person",
        "handoff-trigger",
        (
            Evidence(f"{GW}.handoff", f"{GW}.session", ("HANDOFF_PATH",)),
            Evidence(f"{GW}.chat", f"{GW}.handoff_trigger"),
        ),
    ),
    EdgeSpec("handoff-trigger", "handoff-desk", (Evidence(f"{GW}.handoff", f"{BE}.handoff"),)),
    EdgeSpec("handoff-desk", "care-subagent", (Evidence(f"{BE}.handoff", f"{BE}.agents.care"),)),
    EdgeSpec("care-subagent", "case-queue", (Evidence(f"{GW}.handoff", f"{BE}.fakes.handoffs"),)),
    EdgeSpec(
        "pay-demo", "checkout-validate", (Evidence(f"{GW}.checkout", f"{BE}.agents.orders_place"),)
    ),
    EdgeSpec(
        "checkout-validate", "checkout-hold", (Evidence(f"{GW}.checkout", f"{BE}.inventory"),)
    ),
    EdgeSpec("checkout-hold", "checkout-fraud", (Evidence(f"{GW}.checkout", f"{BE}.fakes.fraud"),)),
    EdgeSpec(
        "checkout-fraud", "checkout-authorize", (Evidence(f"{GW}.checkout", f"{BE}.fakes.payment"),)
    ),
    EdgeSpec(
        "checkout-authorize", "checkout-commit", (Evidence(f"{GW}.checkout", f"{BE}.inventory"),)
    ),
    EdgeSpec(
        "checkout-commit", "checkout-capture", (Evidence(f"{GW}.checkout", f"{BE}.fakes.payment"),)
    ),
    EdgeSpec(
        "checkout-capture",
        "checkout-confirm",
        (Evidence(f"{GW}.checkout", f"{BE}.agents.orders_place"),),
    ),
    EdgeSpec(
        "staff-request",
        "orchestrator",
        (Evidence(f"{BE}.orchestrator.__main__", f"{BE}.orchestrator.run"),),
    ),
    EdgeSpec(
        "orchestrator",
        "orders-subagent",
        (Evidence(f"{BE}.orchestrator.run", f"{BE}.agents.orders_check"),),
    ),
    EdgeSpec(
        "orchestrator",
        "returns-subagent",
        (Evidence(f"{BE}.orchestrator.run", f"{BE}.agents.returns_track"),),
    ),
    EdgeSpec(
        "orders-subagent",
        "order-fraud",
        (
            Evidence(f"{BE}.orchestrator.run", f"{BE}.agents.orders_check", ("FRAUD_TOOL",)),
            Evidence(f"{BE}.orchestrator.__main__", f"{BE}.fakes.fraud"),
        ),
        CODE_PORT,
    ),
    EdgeSpec(
        "orders-subagent",
        "order-payment",
        (
            Evidence(f"{BE}.orchestrator.run", f"{BE}.agents.orders_check", ("PAYMENT_TOOL",)),
            Evidence(f"{BE}.orchestrator.__main__", f"{BE}.fakes.payment"),
        ),
        CODE_PORT,
    ),
    EdgeSpec(
        "returns-subagent",
        "order-carrier",
        (
            Evidence(f"{BE}.orchestrator.run", f"{BE}.agents.returns_track", ("TRACK_TOOL",)),
            Evidence(f"{BE}.orchestrator.__main__", f"{BE}.fakes.carrier"),
        ),
        CODE_PORT,
    ),
    EdgeSpec(
        "escalated-order", "escalation", (Evidence(f"{BE}.care.__main__", f"{BE}.agents.care"),)
    ),
    EdgeSpec(
        "escalation",
        "care-subagent",
        (Evidence(f"{BE}.agents.care", f"{BE}.agents.escalation"),),
        "escalation case",
    ),
)

# Who reaches an MCP server, and through which agent's tool scope: the assistant (every server it
# holds tools on), query understanding (`shopper-query`, dashed: one scoped tool) and the gateway's
# own portal reads (`shopper-portal`) where a module of this map calls them.
PORTAL_READS: tuple[tuple[str, tuple[str, ...], Evidence], ...] = (
    # gateway handoff.py: with no order, the case's amounts are the live cart (hard rule 2).
    ("handoff-trigger", (tool_scopes.GET_CART,), Evidence(f"{GW}.handoff", f"{AS}.catalog_client")),
    # gateway checkout.py: the bag read live, then emptied once the order is placed.
    (
        "pay-demo",
        (tool_scopes.GET_CART, "remove_from_cart"),
        Evidence(f"{GW}.checkout", f"{GW}.cart_writes"),
    ),
)
PORTAL_LABELS: Mapping[str, str] = {
    "handoff-trigger": "get_cart, no order",
    "pay-demo": "get_cart · remove_from_cart",
}


def cart_writes() -> frozenset[str]:
    prefix = f"mcp__{catalog_tool.SERVER_NAME}__"
    return frozenset(name.removeprefix(prefix) for name in catalog_tool.CART_TOOL_NAMES) - {
        tool_scopes.GET_CART
    }


def _server_tools(server: str) -> tuple[list[str], list[dict[str, Any]]]:
    shown = set(tool_scopes.allowed_tools("shopper-assistant", server)) | set(
        tool_scopes.allowed_tools("shopper-query", server)
    )
    writes = cart_writes() if server == CATALOG else frozenset()
    collapsed = (
        [{"label": CART_WRITES_LABEL, "tools": sorted(writes)}]
        if writes and writes <= shown
        else []
    )
    return sorted(shown - writes if collapsed else shown), collapsed


def _node(
    spec: NodeSpec,
    models_: list[str],
    tools: list[str] | None = None,
    collapsed: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": spec.id,
        "kind": spec.kind,
        "title": spec.title,
        "subtitle": spec.subtitle,
        "models": models_,
        "tools": tools or [],
        "collapsed": collapsed or [],
    }


def _edge(
    source: str, target: str, label: str | None = None, dashed: bool = False
) -> dict[str, Any]:
    return {"from": source, "to": target, "label": label, "dashed": dashed}


def generate() -> AgentMap:
    out = AgentMap()
    present: set[str] = set()
    for spec in NODES:
        if not module_exists(spec.module):
            continue
        found = models(spec.module) if spec.kind == "ai" else []
        if spec.kind == "ai" and not found:
            continue  # no model constant: not an AI agent, whatever the spec says
        out.nodes.append(_node(spec, found))
        present.add(spec.id)
        if spec.id == "answer":
            # The servers follow the chat lane, in the tool scopes' order of the assistant's row.
            for server, subtitle in SERVERS.items():
                if not tool_scopes.allowed_tools("shopper-assistant", server):
                    continue
                tools, collapsed = _server_tools(server)
                out.nodes.append(
                    _node(NodeSpec(server, "mcp", server, subtitle), [], tools, collapsed)
                )
                present.add(server)
    if hasattr(llm, "JUDGE_MODEL"):
        out.nodes.append(
            _node(
                NodeSpec("judge", "eval", "Judge", "Eval only: offline scoring, no live arrows"),
                [model_label(llm.JUDGE_MODEL)],
            )
        )
        present.add("judge")

    edges: list[dict[str, Any]] = []
    for flow in FLOW:
        if (
            flow.source in present
            and flow.target in present
            and all(holds(e) for e in flow.evidence)
        ):
            edges.append(_edge(flow.source, flow.target, flow.label))
    for server in SERVERS:
        if server not in present:
            continue
        if tool_scopes.allowed_tools("shopper-assistant", server):
            edges.append(_edge("shopper-assistant", server))
        scoped = tool_scopes.allowed_tools("shopper-query", server)
        if scoped and "query-understanding" in present:
            edges.append(
                _edge("query-understanding", server, ", ".join(sorted(scoped)), dashed=True)
            )
    portal = tool_scopes.allowed_tools("shopper-portal", CATALOG)
    for source, needed, evidence in PORTAL_READS:
        if source in present and CATALOG in present and set(needed) <= portal and holds(evidence):
            edges.append(_edge(source, CATALOG, PORTAL_LABELS[source]))
    order = {spec.source: i for i, spec in enumerate(FLOW)}
    out.edges = sorted(edges, key=lambda e: (order.get(e["from"], len(order)), e["to"]))
    kinds = {node["kind"] for node in out.nodes}
    out.legend = [kind for kind in LEGEND_ORDER if kind in kinds]
    return out
