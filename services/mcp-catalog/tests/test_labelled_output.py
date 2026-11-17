"""Row #12 (#10b security review L6, hard rule 6): every successful catalog result is one
`<catalog_data>` label over the wire; errors stay the bare envelope."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from northwind_common.catalog_tool import unlabel
from northwind_common.errors import ENVELOPE_KEYS
from northwind_mcp_catalog.testing import connect, running, server, token

pytestmark = [pytest.mark.req("RAG-09"), pytest.mark.req("NFR-03")]

CALLS: list[tuple[str, dict[str, Any]]] = [
    ("search_products", {"text": "Cairn"}),
    ("get_product", {"product_id": "P0011"}),
    ("get_variant", {"variant_id": "P0011-SAG-REG-M"}),
    ("get_price", {"variant_id": "P0011-SAG-REG-M"}),
    ("check_stock", {"variant_id": "P0011-SAG-REG-M"}),
]


def raw(calls: list[tuple[str, dict[str, Any]]]) -> list[tuple[bool, str]]:
    async def go(url: str) -> list[tuple[bool, str]]:
        out = []
        async with connect(url, token("shopper-assistant", "US")[0]) as client:
            for name, args in calls:
                result = await client.call_tool(name, args)
                [block] = result.content
                out.append((bool(result.is_error), getattr(block, "text", "")))
        return out

    with running(server()) as url:
        return asyncio.run(go(url))


def test_every_success_is_exactly_one_label_naming_its_tool() -> None:
    for (name, _), (is_error, text) in zip(CALLS, raw(CALLS), strict=True):
        assert is_error is False, name
        labelled = unlabel(text)
        assert labelled is not None, name
        assert labelled[0] == name
        assert not set(text.split("\n")[1]) & {"<", ">", "&"}


def test_an_error_is_the_bare_envelope_not_a_label() -> None:
    [(is_error, text)] = raw([("get_product", {"product_id": "P9999"})])
    assert is_error is True
    assert unlabel(text) is None and not text.startswith("<")
    import json

    assert set(json.loads(text)) == ENVELOPE_KEYS
