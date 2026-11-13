"""C8 (catalog): P0009's canary, ID and title never appear in any catalog tool output (CC-10;
RAG-11, hard rule 7). Every shopper-scoped tool, every region, with arguments built from the
product's title, ID, variant IDs and canary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.testing import KEY, connect, running, token
from northwind_mcp_catalog.tools import TOOLS

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("RAG-11")]

REPO = Path(__file__).resolve().parents[3]
[ENTRY] = [
    e
    for e in json.loads((REPO / "data/fixtures/_answer_key.json").read_text(encoding="utf-8"))[
        "internal_only"
    ]
    if e.get("product_id") == "P0009"
]
CANARY = ENTRY["canary"]
TITLE = ENTRY["title"]
VARIANTS = [f"P0009-BLK-REG-{s}" for s in ("S", "M", "L", "XL")]
REGIONS = ("US", "CA", "UK", "EU")
VALUES = [TITLE, TITLE.upper(), "P0009", "p0009", CANARY, f"{TITLE} {CANARY}", *VARIANTS]
FORBIDDEN = (CANARY, "P0009", TITLE, "NWCANARY")


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    with running(CatalogServer(FixtureCatalog(), key=KEY, tools=TOOLS)) as live_url:
        yield live_url


def arguments() -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for value in VALUES:
        calls.append(("get_product", {"product_id": value}))
        for tool in ("get_variant", "get_price", "check_stock"):
            calls.append((tool, {"variant_id": value}))
        for key in ("text", "brand", "category", "department", "subcategory", "colour", "size"):
            calls.append(("search_products", {key: value}))
        calls.append(("search_products", {"text": value, "limit": 20}))
    calls.append(("get_product", {"product_id": "P0009", "note": CANARY}))
    return calls


def test_the_canary_title_and_id_are_in_no_response_byte(url: str) -> None:
    calls = arguments()
    seen_tools = {name for name, _ in calls}
    assert seen_tools == set(TOOLS)

    async def go(region: str) -> list[str]:
        out: list[str] = []
        async with connect(url, token("shopper-assistant", region)[0]) as client:
            for name, args in calls:
                result = await client.call_tool(name, args)
                out.append(json.dumps(result.model_dump(mode="json", by_alias=True)))
        return out

    for region in REGIONS:
        for (name, args), raw in zip(calls, asyncio.run(go(region)), strict=True):
            for needle in FORBIDDEN:
                assert needle not in raw, (region, name, args)
