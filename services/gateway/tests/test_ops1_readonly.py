"""OPS-1 C2: the console's code calls no store write (the plan's AST half; advisor-reviewer
finding 2). The snapshot half, every store unchanged by every request, is test_ops1_tw_gate.py.

Scans every module of `northwind_gateway.staff` for a call or reference to a write method of the
stores it reads (inventory, orders, payment, fraud, carrier, handoff and case queues, the
confirmation book, the audit), and for an append to an audit sink."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("OPS-1")

STAFF = Path(__file__).resolve().parents[1] / "src" / "northwind_gateway" / "staff"
WRITES = frozenset(
    {
        "reserve", "commit", "release", "authorize", "capture", "refund", "void", "confirm",
        "escalate", "escalate_order", "use_address_on_file", "create_shipment", "assess",
        "open_handoff", "file_case", "create_case", "issue", "place", "pay", "finish",
        "set_status", "add_scan", "inject", "record", "attempt", "outcome",
        "add_to_cart", "remove_from_cart", "set_cart_quantity", "write",
    }
)  # fmt: skip
SINKS = frozenset({"audit", "sink"})


def _attributes(tree: ast.AST) -> list[ast.Attribute]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Attribute)]


def _called(tree: ast.AST) -> set[str]:
    """The names of everything called: `x.reserve(...)` and `reserve(...)` alike. Reading a field
    called `outcome` or a loop variable called `record` is not a call, so it is not a write."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                out.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                out.add(node.func.id)
    return out


def test_the_scan_sees_every_staff_module() -> None:
    names = {path.name for path in STAFF.glob("*.py")}
    assert {"views.py", "routes.py", "agent_map.py", "screen.py", "traces.py", "flag.py"} <= names


@pytest.mark.parametrize("path", sorted(STAFF.glob("*.py")), ids=lambda p: p.name)
def test_no_staff_module_calls_a_store_write(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert not _called(tree) & WRITES, sorted(_called(tree) & WRITES)
    for node in _attributes(tree):
        if node.attr == "append" and isinstance(node.value, ast.Attribute):
            assert node.value.attr not in SINKS, ast.unparse(node)
    # And no write is handed out to be called later (`getattr(x, "reserve")`, a bound method).
    for node in _attributes(tree):
        if isinstance(node.ctx, ast.Load) and node.attr in WRITES - {"outcome", "record"}:
            raise AssertionError(f"{path.name}: {ast.unparse(node)}")


def test_the_scan_catches_a_write() -> None:
    """The check itself: planted writes are found."""
    tree = ast.parse("src.checkout.inventory.reserve(key=1)\nsrc.handoffs.audit.append({})")
    assert _called(tree) & WRITES == {"reserve"}
    assert any(
        n.attr == "append" and isinstance(n.value, ast.Attribute) and n.value.attr in SINKS
        for n in _attributes(tree)
    )
