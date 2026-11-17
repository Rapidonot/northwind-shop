"""The eval answerer with the cart tools attached (row #16, CHK-01; the user's amendment 2).

- Each item's cart session comes from its ID, and its cart starts empty, so running the same item
  twice sends byte-identical model requests, compared as the replay proxy matches them.
- Cart results reach the judge as live values only: no names, colours, sizes or fits (security
  review pass 1, finding 9).
- The leak guard also runs on a cart stock refusal, whose alternatives are catalog records.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

import pytest

from northwind_common import catalog_tool
from northwind_evals.answerers import (
    CatalogLog,
    assistant_answerer,
    eval_session,
    live_of,
    serve_catalog,
)
from northwind_evals.contexts import ContextChunk
from northwind_evals.items import EvalItem
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_llm_replay.scrub import canonical, scrub

pytestmark = pytest.mark.req("CHK-01")

V = "P0001-BLK-REG-M"
ADD = f"mcp__{catalog_tool.SERVER_NAME}__add_to_cart"
ITEM = EvalItem(
    "EV-CART",
    "specification",
    "Add the black M jacket",
    "US",
    "answer",
    (("P0001",),),
    ("f",),
    "w",
    None,
    "model",
)
CHUNK = ContextChunk("product:P0001:0", "P0001", "product", "A rain jacket.")


def test_an_items_session_comes_from_its_id_alone() -> None:
    assert eval_session("EV-001") == eval_session("EV-001")
    assert eval_session("EV-001") != eval_session("EV-002")
    assert re.fullmatch(r"[0-9a-f]{32}", eval_session("EV-001"))


def run_twice() -> list[dict[str, Any]]:
    with ScriptedLLM() as llm:
        for _ in range(2):
            llm.reply_tool_use(ADD, {"variant_id": V, "quantity": 1})
            llm.reply_stream("Added it to your cart.")
        with assistant_answerer(llm.url, {}, "m", lambda s: s) as (answerer, _index):
            for _ in range(2):
                answer = answerer.answer(ITEM, (CHUNK,))
                assert answer.text == "Added it to your cart."
        return [r["body"] for r in llm.requests]


def test_the_same_item_twice_sends_byte_identical_requests() -> None:
    bodies = run_twice()
    assert len(bodies) == 4
    first, second = bodies[:2], bodies[2:]
    assert [canonical(scrub(b)) for b in first] == [canonical(scrub(b)) for b in second]
    # The cart started empty both times: one unit, not two, after the second add.
    assert '\\"quantity\\":1' in json.dumps(second[1]) and '\\"quantity\\":2' not in json.dumps(
        second[1]
    )
    assert eval_session(ITEM.id) not in json.dumps(bodies)


def cart_add() -> str:
    line = {
        "variant_id": V,
        "product_id": "P0001",
        "name": "Tidewick Harbor Rain Jacket",
        "colour": "Black",
        "size": "M",
        "fit": "regular",
        "quantity": 1,
        "on_hand": 5,
        "in_stock": True,
        "unit_price": "139.00",
        "line_total": "139.00",
        "currency": "USD",
    }
    cart = {
        "region": "US",
        "lines": [line],
        "item_count": 1,
        "subtotal": "139.00",
        "currency": "USD",
    }
    return catalog_tool.label(
        "add_to_cart", {"result": "added", "variant_id": V, "quantity": 1, "cart": cart}
    )


def test_a_cart_result_reaches_the_judge_as_live_values_only() -> None:
    blocks, live = live_of([("add_to_cart", False, cart_add())], "US")
    [block] = blocks
    assert block.source_id == "live:add_to_cart:0"
    for attribute in ("Tidewick", "Black", '"size"', '"fit"', '"colour"', '"name"'):
        assert attribute not in block.text
    assert '"line_total": "139.00"' in block.text
    assert (Decimal("139.00"), "USD") in live.prices and live.stock == {V: 5}


def test_a_refusal_reaches_the_judge_with_alternative_ids_only() -> None:
    envelope = {
        "isError": True,
        "errorCategory": "business",
        "isRetryable": False,
        "message": "That item is out of stock in your region.",
        "code": "out_of_stock",
    }
    alt = {
        "variant_id": "P0001-BLK-REG-L",
        "product_id": "P0001",
        "name": "Tidewick Harbor Rain Jacket",
        "colour": "Black",
        "size": "L",
        "fit": "regular",
        "on_hand": 9,
        "in_stock": True,
    }
    data = {
        "region": "US",
        "requested": {"variant_id": V, "on_hand": 0, "in_stock": False},
        "alternatives": [alt],
    }
    text = (
        json.dumps(envelope)
        + catalog_tool.BLOCK_SEPARATOR
        + catalog_tool.label("add_to_cart", data)
    )
    blocks, live = live_of([("add_to_cart", True, text)], "US")
    [block] = blocks
    assert json.loads(block.text)["alternatives"] == ["P0001-BLK-REG-L"]
    assert "Tidewick" not in block.text
    assert live.stock == {V: 0}


def test_the_leak_guard_runs_on_a_stock_refusal_and_not_on_a_bare_error() -> None:
    import asyncio
    import secrets

    from northwind_assistant.caller import Caller
    from northwind_assistant.catalog_client import CatalogEndpoint
    from northwind_mcp_catalog.testing import connect

    key = secrets.token_bytes(40)
    log = CatalogLog()
    with serve_catalog(key, log, leaks=lambda _chunks: True) as url:
        assert log.drop_cart is not None  # set by serve_catalog
        token = CatalogEndpoint(url, key).mint(Caller(region="US", session=eval_session("EV-X")))

        async def call(name: str, args: dict[str, Any]) -> bool:
            async with connect(url, token) as client:
                result = await client.call_tool(name, args)
            return bool(result.is_error)

        # A bare business error carries no catalog data: not a leak.
        assert asyncio.run(call("remove_from_cart", {"variant_id": V}))
        assert log.leak is False
        # An out-of-stock refusal carries alternatives: the guard runs and refuses it.
        assert asyncio.run(call("add_to_cart", {"variant_id": "P0008-CHR-REL-M"}))
        assert log.leak is True
        [*_, (tool, is_error, text)] = log.taken()
        assert (tool, is_error) == ("add_to_cart", True) and "tool_failed" in text
