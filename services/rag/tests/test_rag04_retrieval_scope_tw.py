"""RAG-04 (row #11, decision 0022 sections 6 and 7), hard rules 2, 6 and 7: a planned search reads
no price, and a plan cannot widen what the caller may see.

`retrieve_planned` runs against a recording connection, so the statements and bound values it sends
are visible without a database. From the decision: retrieval never reads a price (the allow-list
comes from the live store), audience and region stay caller-bound, and a filter can only narrow. A
search with no filter is the plain search exactly.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from pathlib import Path
from typing import Any

import pytest

from northwind_rag import retrieval
from northwind_rag.retrieval import (
    NO_FILTER,
    SearchFilter,
    retrieve_context,
    retrieve_planned,
)
from northwind_rag.sources import ALL_REGIONS
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("RAG-04")

ALLOWED_PARAMS = {
    "visible",
    "regions",
    "k",
    "colours",
    "sizes",
    "products",
    "vector",
    "query",
    "depth",
    "pool",
}


class Cursor:
    def __init__(self, log: list[tuple[str, dict[str, Any]]]) -> None:
        self.log = log

    def __enter__(self) -> Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, Any]) -> Cursor:
        self.log.append((sql, dict(params)))
        return self

    def fetchall(self) -> list[Any]:
        return []


class Conn:
    """Records every statement and its bound values; every search comes back empty."""

    def __init__(self) -> None:
        self.log: list[tuple[str, dict[str, Any]]] = []

    def cursor(self) -> Cursor:
        return Cursor(self.log)


def planned(
    parts: list[tuple[str, SearchFilter]], *, region: Any = "US", audience: Any = "shopper"
) -> Conn:
    conn = Conn()
    retrieve_planned(conn, FakeEmbedder(), parts, region=region, audience=audience)  # type: ignore[arg-type]
    return conn


# ---- no price is read ---------------------------------------------------------------------------


def test_a_search_filter_has_no_price_audience_region_or_visibility_field() -> None:
    names = {f.name for f in dataclasses.fields(SearchFilter)}
    assert names == {"colours", "sizes", "products"}


def test_no_statement_or_bound_value_mentions_a_price_or_stock() -> None:
    narrow = SearchFilter(colours=("Black",), sizes=("M",), products=frozenset({"P0011"}))
    conn = planned([("pants under fifty", narrow), ("a fleece", NO_FILTER)])
    assert conn.log
    for sql, params in conn.log:
        assert not re.search(r"price|amount|stock|on_hand|currency|band", sql, re.IGNORECASE)
        assert set(params) <= ALLOWED_PARAMS
    # the allow-list is the only price- or stock-shaped input, and it is a list of product IDs
    for _, params in conn.log:
        assert params["products"] in (None, ["P0011"])


def _code_strings(tree: ast.AST) -> list[str]:
    doc_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                doc_nodes.add(id(body[0].value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_nodes
    ]


def test_the_retrieval_code_never_reads_a_price_column_or_the_catalog() -> None:
    source = Path(inspect.getfile(retrieval)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offending = [s[:60] for s in _code_strings(tree) if re.search(r"price", s, re.IGNORECASE)]
    assert offending == []
    modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(m.startswith(("northwind_mcp_catalog", "httpx", "mcp")) for m in modules)


def test_an_empty_allow_list_is_bound_as_empty_never_as_no_filter() -> None:
    """No product in budget must not turn into "every product": [] is not None."""
    conn = planned([("cheap pants", SearchFilter(products=frozenset()))])
    assert all(params["products"] == [] for _, params in conn.log)


def test_the_allow_list_is_bound_sorted_and_whole() -> None:
    conn = planned([("pants", SearchFilter(products=frozenset({"P0100", "P0011", "P0200"})))])
    assert all(params["products"] == ["P0011", "P0100", "P0200"] for _, params in conn.log)


# ---- caller-bound audience and region -----------------------------------------------------------


@pytest.mark.parametrize(
    "narrow",
    [
        NO_FILTER,
        SearchFilter(colours=("Black",)),
        SearchFilter(sizes=("Queen",), products=frozenset({"P0009"})),  # P0009 is internal
        SearchFilter(products=frozenset({"P0001", "P0009", "P0256"})),
    ],
)
def test_a_shoppers_plan_never_sees_internal_whatever_it_names(narrow: SearchFilter) -> None:
    conn = planned([("anything", narrow)], region="UK")
    assert conn.log
    for sql, params in conn.log:
        assert params["visible"] == ["public"]
        assert params["regions"] == ["UK", ALL_REGIONS]
        assert "visibility = ANY(%(visible)s)" in sql
        assert "region && %(regions)s" in sql


def test_staff_visibility_comes_from_the_audience_argument_alone() -> None:
    staff = planned([("anything", SearchFilter(colours=("Black",)))], region=None, audience="staff")
    shopper = planned([("anything", SearchFilter(colours=("Black",)))])
    assert all(p["visible"] == ["public", "internal"] for _, p in staff.log)
    assert all(p["visible"] == ["public"] for _, p in shopper.log)


def test_a_shopper_plan_without_a_real_region_is_refused() -> None:
    for region in (None, "XX", "", "us"):
        with pytest.raises(ValueError):  # noqa: PT011
            planned([("pants", SearchFilter(colours=("Black",)))], region=region)


def test_an_unknown_audience_is_refused_even_with_a_plan() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        planned([("pants", NO_FILTER)], audience="admin")


@pytest.mark.parametrize("count", [0, 4, 5])
def test_a_plan_has_one_to_three_parts(count: int) -> None:
    parts = [(f"part {i}", NO_FILTER) for i in range(count)]
    with pytest.raises(ValueError):  # noqa: PT011
        planned(parts)
    assert not Conn().log


def test_each_part_is_its_own_search_with_its_own_filter() -> None:
    a = SearchFilter(colours=("Black",))
    b = SearchFilter(sizes=("Queen",))
    conn = planned([("a fleece", a), ("a duvet", b)])
    by_query = {p["query"]: p for _, p in conn.log}
    assert by_query["a fleece"]["colours"] == ["Black"] and by_query["a fleece"]["sizes"] is None
    assert by_query["a duvet"]["sizes"] == ["Queen"] and by_query["a duvet"]["colours"] is None


# ---- no filter is the plain search --------------------------------------------------------------


def test_one_unfiltered_part_is_exactly_the_plain_search() -> None:
    """A message with no filter keeps exactly its old first search (decision 0022 section 1)."""
    plain = Conn()
    retrieve_context(plain, FakeEmbedder(), "rain jacket", region="US")  # type: ignore[arg-type]
    plan = planned([("rain jacket", NO_FILTER)])
    assert plan.log == plain.log
