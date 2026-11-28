"""OPS-1 C3, C4, C5: the agent map is generated from the code and drawn from the portal's layout
table.

- Generated nodes <-> the layout table's boxes, and generated edges <-> its routes, both ways (the
  table is apps/portal/src/staff/layout.json; the portal's own test checks the geometry).
- The portal's snapshot (system.json) is exactly what `GET /api/staff/system` serves.
- MCP edges and tools are `AGENT_TOOLS`'; cart writes are CART_TOOL_NAMES less get_cart.
- Each AI node's model is the llm.py constant its module uses, re-read here independently; a
  changed constant changes the label.
- The assistant never reaches the orchestrator; Pay (demo)'s path has no AI.
- Each flow edge is there only while its import is: take the evidence away and the edge goes.
- Every sample trace is marked sample, walks the map's lines from its entry, and has no timing.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from northwind_common import catalog_tool, llm, tool_scopes
from northwind_gateway.staff import agent_map, traces, views
from northwind_gateway.staff.screen import screen

pytestmark = pytest.mark.req("OPS-1")

REPO = Path(__file__).resolve().parents[3]
PORTAL = REPO / "apps" / "portal" / "src" / "staff"
LAYOUT = json.loads((PORTAL / "layout.json").read_text(encoding="utf-8"))


@pytest.fixture
def generated() -> dict[str, Any]:
    return agent_map.generate().to_dict()


def _keys(m: dict[str, Any]) -> set[str]:
    return {f"{e['from']}->{e['to']}" for e in m["edges"]}


def test_every_generated_node_has_a_box_and_every_box_a_node(generated: dict[str, Any]) -> None:
    assert {n["id"] for n in generated["nodes"]} == set(LAYOUT["slots"])


def test_every_generated_edge_has_a_route_and_every_route_an_edge(
    generated: dict[str, Any],
) -> None:
    assert _keys(generated) == set(LAYOUT["routes"])


def test_the_portal_snapshot_is_what_the_gateway_serves() -> None:
    snapshot = json.loads((PORTAL / "system.json").read_text(encoding="utf-8"))
    assert snapshot == screen(views.system_view(None))  # type: ignore[arg-type]


def test_mcp_edges_and_tools_are_the_tool_scopes(generated: dict[str, Any]) -> None:
    servers = {n["id"]: n for n in generated["nodes"] if n["kind"] == "mcp"}
    held = {s for s, tools in tool_scopes.AGENT_TOOLS["shopper-assistant"].items() if tools}
    assert set(servers) == held
    edges = {(e["from"], e["to"]) for e in generated["edges"]}
    assert {to for frm, to in edges if frm == "shopper-assistant" and to in servers} == held
    for server, node in servers.items():
        shown = set(node["tools"]) | {t for g in node["collapsed"] for t in g["tools"]}
        expected = set(tool_scopes.allowed_tools("shopper-assistant", server)) | set(
            tool_scopes.allowed_tools("shopper-query", server)
        )
        assert shown == expected, server
    dashed = [e for e in generated["edges"] if e["dashed"]]
    assert [(e["from"], e["to"], e["label"]) for e in dashed] == [
        (
            "query-understanding",
            "northwind-catalog",
            ", ".join(sorted(tool_scopes.allowed_tools("shopper-query", "northwind-catalog"))),
        )
    ]


def test_cart_writes_collapse_to_one_line_from_cart_tool_names(generated: dict[str, Any]) -> None:
    catalog = next(n for n in generated["nodes"] if n["id"] == "northwind-catalog")
    prefix = f"mcp__{catalog_tool.SERVER_NAME}__"
    writes = {n.removeprefix(prefix) for n in catalog_tool.CART_TOOL_NAMES} - {tool_scopes.GET_CART}
    assert writes == set(tool_scopes.CART_WRITE_TOOLS)
    assert catalog["collapsed"] == [{"label": "cart write tools", "tools": sorted(writes)}]
    assert not writes & set(catalog["tools"])
    assert tool_scopes.GET_CART in catalog["tools"]


def _constants_by_regex(module: str) -> set[str]:
    """An independent read: the model constants named in a `from northwind_common.llm import`."""
    spec = importlib.util.find_spec(module)
    assert spec and spec.origin
    source = Path(spec.origin).read_text(encoding="utf-8")
    found: set[str] = set()
    for block in re.findall(
        r"from northwind_common\.llm import \(?([^)\n]*(?:\n[^)]*)?)\)?", source
    ):
        found |= {name for name in agent_map.MODEL_CONSTANTS if re.search(rf"\b{name}\b", block)}
    return found


def test_each_ai_node_carries_the_model_its_module_uses(generated: dict[str, Any]) -> None:
    by_id = {n["id"]: n for n in generated["nodes"]}
    for spec in agent_map.NODES:
        if spec.kind != "ai":
            continue
        constants = _constants_by_regex(spec.module)
        assert constants, spec.id
        labels = sorted({agent_map.model_label(getattr(llm, c)) for c in constants})
        assert sorted(by_id[spec.id]["models"]) == labels, spec.id
    assert by_id["shopper-assistant"]["models"] == ["Sonnet"]
    assert by_id["query-understanding"]["models"] == ["Haiku"]
    assert by_id["orchestrator"]["models"] == ["Sonnet"]
    assert by_id["judge"]["models"] == [agent_map.model_label(llm.JUDGE_MODEL)]


def test_the_judge_module_uses_the_judge_constant() -> None:
    assert agent_map.model_constants("northwind_evals.judge") == ("JUDGE_MODEL",)


def test_a_changed_model_constant_changes_the_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "ROUTINE_MODEL", "claude-opus-5-5")
    nodes = {n["id"]: n for n in agent_map.generate().nodes}
    assert nodes["care-subagent"]["models"] == ["Opus"]
    assert nodes["shopper-assistant"]["models"] == ["Sonnet"]


def test_a_module_without_a_model_constant_is_not_drawn_as_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_map,
        "model_constants",
        lambda module: () if module.endswith("returns_track") else ("ROUTINE_MODEL",),
    )
    ids = {n["id"] for n in agent_map.generate().nodes}
    assert "returns-subagent" not in ids


def test_the_assistant_never_reaches_the_orchestrator(generated: dict[str, Any]) -> None:
    edges: dict[str, set[str]] = {}
    for e in generated["edges"]:
        edges.setdefault(e["from"], set()).add(e["to"])
    seen, todo = set(), ["shopper-assistant"]
    while todo:
        node = todo.pop()
        if node in seen:
            continue
        seen.add(node)
        todo.extend(edges.get(node, ()))
    assert "orchestrator" not in seen
    # And in the code: no assistant module imports the orchestrator.
    root = Path(importlib.util.find_spec("northwind_assistant").origin or "").parent  # type: ignore[union-attr]
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for item in ast.walk(tree):
            if isinstance(item, ast.ImportFrom) and item.module:
                assert not item.module.startswith("northwind_backend.orchestrator"), path
            if isinstance(item, ast.Import):
                assert not any(
                    a.name.startswith("northwind_backend.orchestrator") for a in item.names
                ), path


def test_pay_demo_has_no_ai_on_its_path(generated: dict[str, Any]) -> None:
    kinds = {n["id"]: n["kind"] for n in generated["nodes"]}
    edges: dict[str, set[str]] = {}
    for e in generated["edges"]:
        edges.setdefault(e["from"], set()).add(e["to"])
    seen, todo = set(), ["pay-demo"]
    while todo:
        node = todo.pop()
        if node in seen:
            continue
        seen.add(node)
        todo.extend(edges.get(node, ()))
    assert {kinds[n] for n in seen} & {"ai", "eval"} == set()
    assert "checkout-confirm" in seen and "northwind-catalog" in seen


@pytest.fixture
def no_imports(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    original = agent_map.imports
    monkeypatch.setattr(
        agent_map,
        "imports",
        lambda a, b: (
            False
            if (a, b) == ("northwind_backend.agents.care", "northwind_backend.agents.escalation")
            else original(a, b)
        ),
    )
    yield


def test_an_edge_goes_when_its_import_goes(no_imports: None) -> None:
    keys = _keys(agent_map.generate().to_dict())
    assert "escalation->care-subagent" not in keys
    assert "handoff-desk->care-subagent" in keys


def test_every_flow_edge_drawn_has_evidence_that_holds(generated: dict[str, Any]) -> None:
    drawn = _keys(generated)
    for flow in agent_map.FLOW:
        key = f"{flow.source}->{flow.target}"
        assert key in drawn, key
        for evidence in flow.evidence:
            assert agent_map.holds(evidence), (key, evidence)


def test_the_legend_lists_the_kinds_drawn(generated: dict[str, Any]) -> None:
    kinds = {n["kind"] for n in generated["nodes"]}
    assert generated["legend"] == [k for k in agent_map.LEGEND_ORDER if k in kinds]
    assert generated["legend"] == ["ai", "mcp", "code", "fake", "eval"]


# -- the sample traces (C5) ------------------------------------------------------------------------


def test_every_trace_is_sample_untimed_and_walks_the_map(generated: dict[str, Any]) -> None:
    ids = {n["id"] for n in generated["nodes"]}
    entries = {n["id"] for n in generated["nodes"] if n["kind"] == "entry"}
    lines = _keys(generated)
    assert len(traces.TRACES) == 4
    for trace in traces.TRACES:
        assert trace["sample"] is True
        assert not re.search(r"\d+\s?ms\b", json.dumps(trace)), trace["id"]
        nodes = [s["node"] for s in trace["steps"]]
        assert nodes[0] == trace["entry"] and set(nodes) <= ids, trace["id"]
        for i, node in enumerate(nodes[1:], start=1):
            if node in entries:
                continue  # a new part of the request starts at an entry point
            assert any(f"{earlier}->{node}" in lines for earlier in nodes[:i]), (trace["id"], node)


def test_the_damaged_parcel_trace_runs_the_chat_first_then_the_handoff() -> None:
    trace = next(t for t in traces.TRACES if t["id"] == "handoff")
    assert [s["node"] for s in trace["steps"]] == [
        "shopper-chat",
        "query-understanding",
        "shopper-assistant",
        "northwind-orders",
        "northwind-policy",
        "output-check",
        "answer",
        "talk-to-person",
        "handoff-trigger",
        "handoff-desk",
        "care-subagent",
        "case-queue",
    ]
    assert trace["steps"][7]["label"] == traces.TALK_TO_A_PERSON
    assert "O000800" in trace["title"]
