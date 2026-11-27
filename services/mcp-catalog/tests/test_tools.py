"""The five catalog tools over HTTP against the real fixtures (CC-10, row #10b; C2, C4, D3, HR2).

Every error is read off the wire. Nothing here edits a file or re-hashes a manifest.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from northwind_common.errors import ENVELOPE_KEYS
from northwind_mcp_catalog.live import FixtureCatalog, Price, Product, Variant
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.testing import KEY, body_of, connect, running, token
from northwind_mcp_catalog.tools import TOOLS

pytestmark = pytest.mark.req("CC-10")

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "data" / "fixtures"
SRC = REPO / "services" / "mcp-catalog" / "src"
PAN = "4242" * 4
MARKER = "MARKERSECRET9f3a"
FIVE = {"search_products", "get_product", "get_variant", "check_stock", "get_price"}
CURRENCY = {"US": "USD", "CA": "CAD", "UK": "GBP", "EU": "EUR"}


def jsonl(name: str) -> list[dict[str, Any]]:
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def run_calls(
    url: str, bearer: str, calls: list[tuple[str, dict[str, Any]]]
) -> list[tuple[bool, str, Any]]:
    """One session, many calls: (is_error, raw text, structuredContent)."""

    async def go() -> list[tuple[bool, str, Any]]:
        out: list[tuple[bool, str, Any]] = []
        async with connect(url, bearer) as client:
            for name, args in calls:
                result = await client.call_tool(name, args)
                [block] = result.content
                out.append(
                    (bool(result.is_error), getattr(block, "text", ""), result.structured_content)
                )
        return out

    return asyncio.run(go())


def one(url: str, name: str, args: dict[str, Any], region: str = "UK") -> tuple[bool, str, Any]:
    return run_calls(url, token("shopper-assistant", region)[0], [(name, args)])[0]


def assert_envelope(
    outcome: tuple[bool, str, Any], category: str, retryable: bool, code: str
) -> dict[str, Any]:
    is_error, text, structured = outcome
    assert is_error is True
    body = json.loads(text)
    assert set(body) == ENVELOPE_KEYS
    assert (body["errorCategory"], body["isRetryable"], body["code"]) == (
        category,
        retryable,
        code,
    )
    assert body["isError"] is True
    assert structured == body
    return dict(body)


class SpyCatalog(FixtureCatalog):
    """The real, verified store, counting every read."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def products(self) -> Any:
        self.reads += 1
        return super().products()

    def product(self, product_id: str) -> Product | None:
        self.reads += 1
        return super().product(product_id)

    def variant(self, variant_id: str) -> Variant | None:
        self.reads += 1
        return super().variant(variant_id)

    def variants_of(self, product_id: str) -> Any:
        self.reads += 1
        return super().variants_of(product_id)

    def price(self, variant_id: str, region: str) -> Price | None:
        self.reads += 1
        return super().price(variant_id, region)

    def stock(self, variant_id: str, region: str) -> int | None:
        self.reads += 1
        return super().stock(variant_id, region)


class FailingCatalog(FixtureCatalog):
    """Every read raises, with a planted secret in the exception text."""

    def _boom(self) -> Any:
        raise RuntimeError(f"db password is {MARKER}")

    def products(self) -> Any:
        return self._boom()

    def product(self, product_id: str) -> Any:
        return self._boom()

    def variant(self, variant_id: str) -> Any:
        return self._boom()

    def variants_of(self, product_id: str) -> Any:
        return self._boom()

    def price(self, variant_id: str, region: str) -> Any:
        return self._boom()

    def stock(self, variant_id: str, region: str) -> Any:
        return self._boom()


@pytest.fixture(scope="module")
def spy() -> SpyCatalog:
    return SpyCatalog()


@pytest.fixture(scope="module")
def spy_url(spy: SpyCatalog) -> Iterator[str]:
    with running(CatalogServer(spy, key=KEY, tools=TOOLS)) as url:
        yield url


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    with running(CatalogServer(FixtureCatalog(), key=KEY, tools=TOOLS)) as live_url:
        yield live_url


def fixture_price(variant_id: str, region: str) -> dict[str, Any]:
    [row] = [
        r for r in jsonl("prices.jsonl") if r["variant_id"] == variant_id and r["region"] == region
    ]
    return row


def fixture_stock(variant_id: str, region: str) -> int:
    return sum(
        int(r["on_hand"])
        for r in jsonl("stock.jsonl")
        if r["variant_id"] == variant_id and r["region"] == region
    )


# ---- C2: happy paths ---------------------------------------------------------------------------


# Row #16 (CHK-01): the four cart tools join the registry. Exact: the five catalog tools and
# `get_cart` stay read-only, and the three cart writes are the only tools that are not.
CART = {"get_cart", "add_to_cart", "remove_from_cart", "set_cart_quantity"}
CART_WRITES = {"add_to_cart", "remove_from_cart", "set_cart_quantity"}


def test_the_server_serves_exactly_the_five_tools() -> None:
    # Row #11 (RAG-04; approved under stop 5): plus `eligible_products`, read-only, for
    # `shopper-query` only (tests/mcp/test_contract.py).
    assert set(TOOLS) == FIVE | CART | {"eligible_products"}
    writes = {
        name
        for name, spec in TOOLS.items()
        if not (spec.tool.annotations and spec.tool.annotations.read_only_hint)
    }
    assert writes == CART_WRITES


def test_the_assistant_lists_the_five_tools_over_the_wire(url: str) -> None:
    async def go() -> set[str]:
        async with connect(url, token()[0]) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    assert asyncio.run(go()) == FIVE | CART


def test_search_products_finds_a_public_product_by_name(url: str) -> None:
    is_error, text, structured = one(url, "search_products", {"text": "Tidewick Harbor"})
    body = body_of(text)
    assert (is_error, structured) == (False, None)
    assert set(body) == {"products", "returned", "more"}
    assert [p["product_id"] for p in body["products"]] == ["P0001"]
    assert body["products"][0] == {
        "product_id": "P0001",
        "name": "Tidewick Harbor Rain Jacket",
        "brand": body["products"][0]["brand"],
        "category": "apparel",
        "department": "Jackets",
        "subcategory": body["products"][0]["subcategory"],
    }
    assert (body["returned"], body["more"]) == (1, False)


def test_search_products_limits_and_orders_by_product_id(url: str) -> None:
    _, text, _ = one(url, "search_products", {"limit": 3})
    body = body_of(text)
    ids = [p["product_id"] for p in body["products"]]
    assert (body["returned"], body["more"]) == (3, True)
    assert ids == sorted(ids)
    _, default_text, _ = one(url, "search_products", {})
    assert body_of(default_text)["returned"] == 10


def test_search_products_carries_no_price_or_stock(url: str) -> None:
    _, text, _ = one(url, "search_products", {"limit": 20})
    for product in body_of(text)["products"]:
        assert set(product) == {
            "product_id",
            "name",
            "brand",
            "category",
            "department",
            "subcategory",
        }


def test_get_product_returns_the_record_and_its_variant_ids(url: str) -> None:
    is_error, text, structured = one(url, "get_product", {"product_id": "P0001"})
    body = body_of(text)
    expected = sorted(
        v["variant_id"] for v in jsonl("variants.jsonl") if v["product_id"] == "P0001"
    )
    assert (is_error, structured) == (False, None)
    assert body["product_id"] == "P0001"
    assert body["name"] == "Tidewick Harbor Rain Jacket"
    assert sorted(body["variant_ids"]) == expected
    assert not {"price", "amount", "prices", "stock", "on_hand", "price_bands"} & set(body)


def test_get_variant_returns_its_attributes(url: str) -> None:
    is_error, text, structured = one(url, "get_variant", {"variant_id": "P0001-BLK-REG-L"})
    assert (is_error, structured) == (False, None)
    assert body_of(text) == {
        "variant_id": "P0001-BLK-REG-L",
        "product_id": "P0001",
        "sku": "NW-0001-BLK-REG-L",
        "colour": "Black",
        "colour_family": "black",
        "fit": "regular",
        "size": "L",
    }


@pytest.mark.parametrize("region", ["US", "CA", "UK", "EU"])
def test_get_price_reads_the_token_region(url: str, region: str) -> None:
    row = fixture_price("P0001-BLK-REG-L", region)
    is_error, text, structured = one(url, "get_price", {"variant_id": "P0001-BLK-REG-L"}, region)
    assert (is_error, structured) == (False, None)
    assert body_of(text) == {
        "variant_id": "P0001-BLK-REG-L",
        "region": region,
        "amount": row["amount"],
        "currency": row["currency"],
    }
    assert row["currency"] == CURRENCY[region]


@pytest.mark.parametrize("region", ["US", "CA", "UK", "EU"])
def test_check_stock_reads_the_token_region(url: str, region: str) -> None:
    on_hand = fixture_stock("P0001-BLK-REG-L", region)
    is_error, text, structured = one(url, "check_stock", {"variant_id": "P0001-BLK-REG-L"}, region)
    assert (is_error, structured) == (False, None)
    assert body_of(text) == {
        "variant_id": "P0001-BLK-REG-L",
        "region": region,
        "on_hand": on_hand,
        "in_stock": on_hand > 0,
    }


def test_a_caller_key_cannot_pick_the_region(url: str) -> None:
    outcome = one(url, "get_price", {"variant_id": "P0001-BLK-REG-L", "region": "US"}, "UK")
    assert_envelope(outcome, "validation", False, "unknown_argument")


# ---- C4: every error path, off the wire ---------------------------------------------------------


def test_an_empty_search_is_not_an_error(url: str) -> None:
    is_error, text, structured = one(url, "search_products", {"text": "zzzz no such product"})
    assert (is_error, structured) == (False, None)
    assert body_of(text) == {"products": [], "returned": 0, "more": False}


@pytest.mark.parametrize(
    ("name", "args", "code"),
    [
        ("get_product", {"product_id": f"p1-{MARKER}"}, "invalid_product_id"),
        ("get_product", {"product_id": "P00001"}, "invalid_product_id"),
        ("get_product", {}, "invalid_product_id"),
        ("get_product", {"product_id": 1}, "invalid_product_id"),
        ("get_variant", {"variant_id": f"x-{MARKER}"}, "invalid_variant_id"),
        ("get_price", {"variant_id": "P0001 BLK"}, "invalid_variant_id"),
        ("check_stock", {"variant_id": "p0001-blk-reg-l"}, "invalid_variant_id"),
        ("check_stock", {"variant_id": "P0001-" + "A" * 200}, "invalid_variant_id"),
    ],
)
def test_a_malformed_id_is_a_validation_error_and_is_not_echoed(
    url: str, name: str, args: dict[str, Any], code: str
) -> None:
    outcome = one(url, name, args)
    assert_envelope(outcome, "validation", False, code)
    assert MARKER not in outcome[1] and "P00001" not in outcome[1]


@pytest.mark.parametrize(
    "args",
    [
        {"limit": 0},
        {"limit": 21},
        {"limit": "5"},
        {"limit": True},
        {"text": "a" * 65},
        {"text": 5},
        {"text": "   "},
        {"brand": ["x"]},
        {"category": MARKER * 10},
    ],
)
def test_a_wrong_type_or_length_is_invalid_argument_without_echo(
    url: str, args: dict[str, Any]
) -> None:
    outcome = one(url, "search_products", args)
    assert_envelope(outcome, "validation", False, "invalid_argument")
    assert MARKER not in outcome[1]


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("get_product", {"product_id": "P9999"}),
        ("get_variant", {"variant_id": "P9999-BLK-REG-M"}),
        ("get_price", {"variant_id": "P9999-BLK-REG-M"}),
        ("check_stock", {"variant_id": "P9999-BLK-REG-M"}),
    ],
)
def test_an_unknown_id_is_not_found(url: str, name: str, args: dict[str, Any]) -> None:
    outcome = one(url, name, args)
    assert_envelope(outcome, "validation", False, "not_found")
    assert "P9999" not in outcome[1]


def test_a_variant_not_sold_in_the_region_is_a_business_error(url: str) -> None:
    # P0010-CHR-REG-L has price rows for US and CA only.
    assert not [
        r
        for r in jsonl("prices.jsonl")
        if r["variant_id"] == "P0010-CHR-REG-L" and r["region"] == "UK"
    ]
    outcome = one(url, "get_price", {"variant_id": "P0010-CHR-REG-L"}, "UK")
    assert_envelope(outcome, "business", False, "not_sold_in_region")
    ok = one(url, "get_price", {"variant_id": "P0010-CHR-REG-L"}, "US")
    assert ok[0] is False


@pytest.mark.parametrize("name", ["get_price", "check_stock"])
def test_a_token_without_a_region_gets_region_required(name: str) -> None:
    from northwind_common.errors import ToolError
    from northwind_common.mcp_token import CATALOG_SERVER, Claims
    from northwind_mcp_catalog.server import error_result

    staff = Claims("staff", None, "catalog-subagent", "turn-1", 2**31, CATALOG_SERVER)
    instance = CatalogServer(FixtureCatalog(), key=KEY, tools=TOOLS)

    async def go() -> Any:
        try:
            return await TOOLS[name].handler(instance, staff, {"variant_id": "P0001-BLK-REG-L"})
        except ToolError as error:
            return error_result(error)

    result = asyncio.run(go())
    [block] = result.content
    body = json.loads(block.text)
    assert result.is_error is True
    assert set(body) == ENVELOPE_KEYS
    assert (body["errorCategory"], body["isRetryable"], body["code"]) == (
        "validation",
        False,
        "region_required",
    )


# ---- D3: unknown keys and card data -------------------------------------------------------------

TOOL_ARGS: dict[str, dict[str, Any]] = {
    "search_products": {"text": "Tidewick"},
    "get_product": {"product_id": "P0001"},
    "get_variant": {"variant_id": "P0001-BLK-REG-L"},
    "check_stock": {"variant_id": "P0001-BLK-REG-L"},
    "get_price": {"variant_id": "P0001-BLK-REG-L"},
}


@pytest.mark.parametrize("name", sorted(FIVE))
def test_an_unknown_key_is_refused_before_any_lookup(
    spy: SpyCatalog, spy_url: str, name: str
) -> None:
    spy.reads = 0
    outcome = one(spy_url, name, {**TOOL_ARGS[name], "surprise": "x"})
    assert_envelope(outcome, "validation", False, "unknown_argument")
    assert spy.reads == 0


@pytest.mark.parametrize("name", sorted(FIVE))
def test_a_pan_in_a_known_key_is_refused_before_any_lookup(
    spy: SpyCatalog, spy_url: str, name: str
) -> None:
    spy.reads = 0
    known = next(iter(TOOL_ARGS[name]))
    outcome = one(spy_url, name, {known: PAN})
    assert_envelope(outcome, "validation", False, "card_data_refused")
    assert PAN not in outcome[1] and spy.reads == 0


@pytest.mark.parametrize("name", sorted(FIVE))
def test_a_pan_in_an_unknown_key_is_card_data_refused_first(
    spy: SpyCatalog, spy_url: str, name: str
) -> None:
    spy.reads = 0
    outcome = one(spy_url, name, {**TOOL_ARGS[name], "note": PAN})
    assert_envelope(outcome, "validation", False, "card_data_refused")
    assert spy.reads == 0
    as_key = one(spy_url, name, {**TOOL_ARGS[name], PAN: "x"})
    assert_envelope(as_key, "validation", False, "card_data_refused")
    assert spy.reads == 0


def test_a_pan_split_across_values_is_refused(spy: SpyCatalog, spy_url: str) -> None:
    spy.reads = 0
    outcome = one(
        spy_url,
        "search_products",
        {"text": "4242", "brand": "4242", "colour": "4242", "size": "4242"},
    )
    assert_envelope(outcome, "validation", False, "card_data_refused")
    assert spy.reads == 0


# ---- a failing store ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FIVE))
def test_a_store_that_raises_gives_tool_failed_without_its_text(name: str) -> None:
    with running(CatalogServer(FailingCatalog(), key=KEY, tools=TOOLS)) as failing_url:
        outcome = one(failing_url, name, TOOL_ARGS[name])
    assert_envelope(outcome, "transient", True, "tool_failed")
    assert MARKER not in outcome[1] and "password" not in outcome[1]


# ---- HR2 ----------------------------------------------------------------------------------------


@pytest.mark.req("RAG-09")
def test_price_and_stock_follow_the_live_store_with_no_file_edit() -> None:
    catalog = FixtureCatalog()
    instance = CatalogServer(catalog, key=KEY, tools=TOOLS)
    vid = "P0001-BLK-REG-L"
    before = fixture_price(vid, "UK")
    with running(instance) as live_url:
        assert instance.catalog is catalog
        _, text, _ = one(live_url, "get_price", {"variant_id": vid})
        assert body_of(text)["amount"] == before["amount"]
        _, text, _ = one(live_url, "check_stock", {"variant_id": vid})
        assert body_of(text)["on_hand"] == fixture_stock(vid, "UK")

        new_amount = "1.07" if before["amount"] != "1.07" else "2.07"
        new_stock = 7 if fixture_stock(vid, "UK") != 7 else 8
        instance.catalog.set_price(vid, "UK", new_amount, "GBP")
        instance.catalog.set_stock(vid, "UK", new_stock)

        _, text, _ = one(live_url, "get_price", {"variant_id": vid})
        assert body_of(text) == {
            "variant_id": vid,
            "region": "UK",
            "amount": new_amount,
            "currency": "GBP",
        }
        _, text, _ = one(live_url, "check_stock", {"variant_id": vid})
        body = body_of(text)
        assert (body["on_hand"], body["in_stock"]) == (new_stock, True)
        # another region is untouched
        _, text, _ = one(live_url, "get_price", {"variant_id": vid}, "US")
        assert body_of(text)["amount"] == fixture_price(vid, "US")["amount"]

        instance.catalog.set_stock(vid, "UK", 0)
        _, text, _ = one(live_url, "check_stock", {"variant_id": vid})
        body = body_of(text)
        assert (body["on_hand"], body["in_stock"]) == (0, False)


def test_the_set_tools_are_not_reachable_over_mcp(url: str) -> None:
    for name in ("set_price", "set_stock"):
        assert name not in TOOLS
        outcome = one(url, name, {"variant_id": "P0001-BLK-REG-L", "amount": "1.00"})
        assert_envelope(outcome, "permission", False, "not_allowed")


# Row #18 step 0, the user's option B (2026-11-27): the one module allowed a database driver, to
# read BE-04's live holds. Every table it names must be one of BE-04's (test below).
DRIVER_ALLOWED = "holds.py"


def _imports(text: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    return imported


@pytest.mark.req("RAG-09")
def test_the_catalog_source_never_touches_the_index() -> None:
    sources = sorted(SRC.rglob("*.py"))
    assert sources
    assert DRIVER_ALLOWED in {p.name for p in sources}
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "rag_chunks" not in text, path
        assert "northwind_rag" not in text, path
        assert "northwind_rag" not in _imports(text), path
        if path.name != DRIVER_ALLOWED:
            assert "psycopg" not in text, path
            assert "psycopg" not in _imports(text), path


@pytest.mark.req("RAG-09")
def test_the_one_database_module_names_only_inventory_tables() -> None:
    """Every table named in `holds.py`'s strings (any case, quoted or not) is a BE-04 table, and no
    string holds a write statement. Docstrings are prose and are skipped; the code's strings, SQL
    included, are all read (option B's security read, L2)."""
    tree = ast.parse((SRC / "northwind_mcp_catalog" / DRIVER_ALLOWED).read_text(encoding="utf-8"))
    docstrings = {
        id(body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and (body := node.body)
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    }
    strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    code = "\n".join(strings)
    named = re.findall(r'(?i)\b(?:JOIN|INTO|UPDATE|TABLE)\s+"?([A-Za-z_][\w.]*)"?', code)
    # Every table of a FROM list, comma joins included (option B's re-review N-L2).
    clause_end = r"\b(?:WHERE|JOIN|GROUP|ORDER|LIMIT|LEFT|RIGHT|INNER|OUTER|CROSS)\b|;|$"
    for found in re.finditer(rf"(?is)\bFROM\s+(.*?)(?={clause_end})", code):
        for item in found.group(1).split(","):
            first = item.strip().split()
            if first:
                named.append(first[0].strip('"'))
    assert named, "the reader names its tables"
    assert set(named) <= {"inventory_holds", "inventory_reservations", "inventory_stock"}, named
    writes = r"(?i)\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|COPY)\b"
    assert not re.search(writes, code), "the reader only reads"


def test_stock_is_summed_per_region_from_the_fixture_rows() -> None:
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for r in jsonl("stock.jsonl"):
        totals[(r["variant_id"], r["region"])] += int(r["on_hand"])
    catalog = FixtureCatalog()
    for (vid, region), total in list(totals.items())[:50]:
        assert catalog.stock(vid, region) == total
