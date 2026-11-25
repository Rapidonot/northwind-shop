"""RAG-07: the rules router picks the labelled indexes on at least 54 of 60 queries (row #31).

Report-only by the user's decision (2026-11-25): accuracy is measured here and nothing narrows a
search with the router, so RAG-07 is partial. The router was written from the guide alone, by an
author that had not opened the labels; the first score and the post-fix score are both in the PR
body. Scoring: the exact set of indexes (the guide).
"""

from __future__ import annotations

import ast
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from northwind_rag.query import ALL_INDEXES, route_indexes

pytestmark = pytest.mark.req("RAG-07")

REPO = Path(__file__).resolve().parents[2]
LABELS = REPO / "evals/fixtures/rag07_queries.jsonl"
NEEDED = 54


def _items() -> list[dict[str, Any]]:
    return [json.loads(line) for line in LABELS.read_text(encoding="utf-8").splitlines()]


def score() -> tuple[int, dict[str, str], str]:
    """(correct, misses, report)."""
    items = _items()
    misses: dict[str, str] = {}
    right: Counter[str] = Counter()
    added: Counter[str] = Counter()
    left_out: Counter[str] = Counter()
    for item in items:
        gold, got = frozenset(item["indexes"]), route_indexes(item["query"])
        if got == gold:
            right[item["category"]] += 1
            continue
        misses[item["id"]] = f"want {sorted(gold)}, got {sorted(got)}: {item['query']}"
        added.update(got - gold)
        left_out.update(gold - got)
    totals = Counter(item["category"] for item in items)
    report = [f"{sum(right.values())}/{len(items)}"]
    report += [f"  {name}: {right[name]}/{n}" for name, n in totals.items()]
    report += [f"  {i}: added {added[i]}, left out {left_out[i]}" for i in sorted(ALL_INDEXES)]
    report += [f"  {item}: {why}" for item, why in misses.items()]
    return len(items) - len(misses), misses, "\n".join(report)


def test_at_least_54_of_60_are_routed_exactly() -> None:
    correct, _, report = score()
    print(report)
    assert len(_items()) == 60
    assert correct >= NEEDED, report


def uses(source: str) -> list[str]:
    """Every way a module can touch the router other than defining it."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id == "route_indexes":
            found.append("name")
        elif isinstance(node, ast.Attribute) and node.attr == "route_indexes":
            found.append("attribute")
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] == "route_indexes":
            found.append("import")
        elif isinstance(node, ast.Constant) and node.value == "route_indexes":
            found.append("string")
    return found


@pytest.mark.parametrize(
    "source",
    [
        "_router = route_indexes",
        "map(route_indexes, parts)",
        "query.route_indexes(m)",
        "from northwind_rag.query import route_indexes as r",
        "getattr(query, 'route_indexes')",
    ],
)
def test_the_use_finder_sees_each_kind_of_use(source: str) -> None:
    assert uses(source)


def test_nothing_calls_the_router() -> None:
    """Report-only: wiring it is the user's decision (plan, stops)."""
    tracked = subprocess.run(  # noqa: S603 - fixed arguments
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.py"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    paths = [p for p in tracked if p and "tests/" not in p and (REPO / p).is_file()]
    assert "services/rag/src/northwind_rag/query.py" in paths and len(paths) > 100
    callers = {p: u for p in paths if (u := uses((REPO / p).read_text(encoding="utf-8")))}
    assert callers == {}
