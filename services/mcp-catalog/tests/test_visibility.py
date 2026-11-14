"""Internal records never reach a shopper through the catalog tools (CC-10, row #10b; C7; RAG-11,
NFR-01, hard rule 7). For a shopper, P0009 and its variants get exactly the result an unknown
well-formed ID gets, in every region, so a refusal reveals nothing."""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from northwind_common.errors import ToolError
from northwind_common.mcp_token import CATALOG_SERVER, Claims
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer, error_result
from northwind_mcp_catalog.testing import KEY, connect, running, token
from northwind_mcp_catalog.tools import TOOLS

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("RAG-11"), pytest.mark.req("NFR-01")]

REPO = Path(__file__).resolve().parents[3]
KEY_FILE = REPO / "data" / "fixtures" / "_answer_key.json"
[ENTRY] = [
    e
    for e in json.loads(KEY_FILE.read_text(encoding="utf-8"))["internal_only"]
    if e.get("product_id") == "P0009"
]
TITLE = ENTRY["title"]
CANARY = ENTRY["canary"]
REGIONS = ("US", "CA", "UK", "EU")
P0009_VARIANTS = [f"P0009-BLK-REG-{s}" for s in ("S", "M", "L", "XL")]
DEPARTMENTS = [
    "Base Layers",
    "Bath",
    "Bedding",
    "Dining",
    "Fleece",
    "Jackets",
    "Knitwear",
    "Living",
    "Pants",
    "Shirts",
]


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    with running(CatalogServer(FixtureCatalog(), key=KEY, tools=TOOLS)) as live_url:
        yield live_url


def session(
    url: str, region: str, calls: list[tuple[str, dict[str, Any]]]
) -> list[tuple[bool, str]]:
    async def go() -> list[tuple[bool, str]]:
        out: list[tuple[bool, str]] = []
        async with connect(url, token("shopper-assistant", region)[0]) as client:
            for name, args in calls:
                result = await client.call_tool(name, args)
                [block] = result.content
                out.append((bool(result.is_error), getattr(block, "text", "")))
        return out

    return asyncio.run(go())


def fixture_set() -> list[tuple[str, str, str]]:
    """(tool, hidden argument, matching unknown well-formed argument)."""
    cases = [("get_product", "P0009", "P9999")]
    for tool in ("get_variant", "get_price", "check_stock"):
        cases += [(tool, v, v.replace("P0009", "P9999")) for v in P0009_VARIANTS]
    return cases


@pytest.mark.parametrize("region", REGIONS)
def test_p0009_and_its_variants_look_like_an_unknown_id(url: str, region: str) -> None:
    cases = fixture_set()
    key_of = {"get_product": "product_id"}
    calls: list[tuple[str, dict[str, Any]]] = []
    for tool, hidden, unknown in cases:
        key = key_of.get(tool, "variant_id")
        calls += [(tool, {key: hidden}), (tool, {key: unknown})]
    out = session(url, region, calls)
    assert len(cases) == 13
    for i, (tool, hidden, _unknown) in enumerate(cases):
        internal, absent = out[2 * i], out[2 * i + 1]
        assert internal[0] is True
        assert json.loads(internal[1])["code"] == "not_found"
        # byte-identical for the same tool
        assert internal == absent, (tool, hidden)


@pytest.mark.parametrize("region", REGIONS)
def test_an_internal_variant_looks_like_an_unknown_variant_of_a_public_product(
    url: str, region: str
) -> None:
    out = session(
        url,
        region,
        [
            ("get_variant", {"variant_id": "P0009-BLK-REG-M"}),
            ("get_variant", {"variant_id": "P0001-ZZZ-REG-M"}),
        ],
    )
    assert out[0] == out[1]


def combos() -> Iterator[dict[str, Any]]:
    yield {}
    yield {"limit": 20}
    for dept in DEPARTMENTS:
        yield {"department": dept, "limit": 20}
        yield {"department": dept, "text": TITLE, "limit": 20}
    singles: dict[str, list[str]] = {
        "text": [TITLE, TITLE.lower(), "Corvane", "Ascent", "Shell", "Pro", "P0009", CANARY],
        "brand": ["Corvane", "corvane"],
        "category": ["apparel"],
        "subcategory": ["rain_shell"],
        "colour": ["Black"],
        "size": ["S", "M", "L", "XL"],
        "fit": ["regular"],
    }
    for key, values in singles.items():
        for value in values:
            yield {key: value, "limit": 20}
    for a, b in itertools.combinations(["brand", "subcategory", "colour", "size", "fit"], 2):
        yield {a: singles[a][0], b: singles[b][0], "limit": 20}
    yield {
        "text": "Corvane",
        "brand": "Corvane",
        "category": "apparel",
        "department": "Jackets",
        "subcategory": "rain_shell",
        "gender": "unisex",
        "colour": "Black",
        "size": "M",
        "fit": "regular",
        "limit": 20,
    }


@pytest.mark.parametrize("region", ["US", "UK"])
def test_search_never_returns_p0009(url: str, region: str) -> None:
    all_args = list(combos())
    out = session(url, region, [("search_products", a) for a in all_args])
    assert len(all_args) > 50
    returned = 0
    for args, (is_error, text) in zip(all_args, out, strict=True):
        assert is_error is False, args
        body = json.loads(text)
        returned += len(body["products"])
        assert "P0009" not in [p["product_id"] for p in body["products"]], args
        assert TITLE not in text and CANARY not in text, args
    assert returned > 0  # the filters did match public products, so the check is not vacuous


@pytest.mark.parametrize("gender", ["unisex", "mens", "womens"])
def test_search_by_gender_is_a_valid_search_and_never_returns_p0009(url: str, gender: str) -> None:
    # Home products have no gender; the filter must skip them, not fail (a valid empty or partial
    # result is never `tool_failed`).
    [(is_error, text)] = session(url, "UK", [("search_products", {"gender": gender, "limit": 20})])
    assert is_error is False, text
    body = json.loads(text)
    assert body["products"]
    assert "P0009" not in [p["product_id"] for p in body["products"]]


def test_search_by_the_internal_name_is_an_empty_valid_result(url: str) -> None:
    [(is_error, text)] = session(url, "UK", [("search_products", {"text": TITLE})])
    assert (is_error, json.loads(text)) == (False, {"products": [], "returned": 0, "more": False})


# ---- a staff caller does see it (no staff agent is scoped, so the handler is called directly) --


def staff_call(name: str, args: dict[str, Any], region: str | None = None) -> tuple[bool, Any]:
    claims = Claims("staff", region, "catalog-subagent", "turn-1", 2**31, CATALOG_SERVER)
    instance = CatalogServer(FixtureCatalog(), key=KEY, tools=TOOLS)

    async def go() -> Any:
        try:
            return await TOOLS[name].handler(instance, claims, args)
        except ToolError as error:
            return error_result(error)

    result = asyncio.run(go())
    [block] = result.content
    return bool(result.is_error), json.loads(block.text)


def test_staff_search_finds_p0009() -> None:
    is_error, body = staff_call("search_products", {"text": TITLE})
    assert is_error is False
    assert [p["product_id"] for p in body["products"]] == ["P0009"]


def test_staff_sees_the_product_and_its_variants() -> None:
    is_error, body = staff_call("get_product", {"product_id": "P0009"})
    assert (is_error, body["product_id"]) == (False, "P0009")
    assert sorted(body["variant_ids"]) == sorted(P0009_VARIANTS)
    for vid in P0009_VARIANTS:
        is_error, variant = staff_call("get_variant", {"variant_id": vid})
        assert (is_error, variant["variant_id"], variant["product_id"]) == (False, vid, "P0009")


def test_staff_gets_past_the_visibility_filter_on_price_and_stock() -> None:
    # P0009 has no price row in any region: a visible variant says "not sold", never "not found".
    for region in REGIONS:
        is_error, body = staff_call("get_price", {"variant_id": "P0009-BLK-REG-M"}, region)
        assert is_error is True
        assert body["code"] == "not_sold_in_region"
        _, stock = staff_call("check_stock", {"variant_id": "P0009-BLK-REG-M"}, region)
        assert stock.get("code") != "not_found"
