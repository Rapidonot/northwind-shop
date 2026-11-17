"""CHK-01 (row #16): the Shopper Assistant's cart tools, from the criterion and the plan's R11, R12.

Criterion: an out-of-stock add is refused with alternatives offered, and the cart tools cannot place
an order. Beside `test_chk01_cart.py`, this file covers: the other cart tools' results (remove and
change quantity), the refusal on a quantity change, a whole turn on a scripted model against a real
mcp-catalog (the refusal and its alternatives reach the model, the cart stays as it was, and what
the shopper reads cannot state a figure the tools did not return), the allowed tool names,
and the gateway's cart reader failing closed without a session.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CartReader, CatalogEndpoint
from northwind_assistant.output_check import FIXED_SENTENCES, LiveValues, OutputCheck
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.tools import ToolState, record_live
from northwind_assistant.turn import TextDelta, stream_answer
from northwind_common import catalog_tool, tool_scopes
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("CHK-01")

SERVER = catalog_tool.SERVER_NAME
ADD = f"mcp__{SERVER}__add_to_cart"
REMOVE = f"mcp__{SERVER}__remove_from_cart"
SET = f"mcp__{SERVER}__set_cart_quantity"
V = "P0001-BLK-REG-M"
V2 = "P0001-BLK-REG-S"
ORDER_WORDS = ("order", "checkout", "pay", "purchase", "refund", "capture", "assign", "buy")


def line(variant: str, quantity: int, unit: str, total: str, on_hand: int = 6) -> dict[str, Any]:
    return {
        "variant_id": variant,
        "product_id": "P0001",
        "name": "Tidewick Harbor Rain Jacket",
        "colour": "Black",
        "size": variant.split("-")[-1],
        "fit": "regular",
        "quantity": quantity,
        "on_hand": on_hand,
        "in_stock": on_hand > 0,
        "unit_price": unit,
        "line_total": total,
        "currency": "USD",
    }


def cart(lines: list[dict[str, Any]], subtotal: str) -> dict[str, Any]:
    return {
        "region": "US",
        "lines": lines,
        "item_count": sum(ln["quantity"] for ln in lines),
        "subtotal": subtotal,
        "currency": "USD",
    }


def recorded(text: str, tool: str, *, is_error: bool = False, region: str = "US") -> LiveValues:
    state = ToolState(region=region)
    record_live(state, tool, is_error=is_error, text=text)
    return state.live


def shown(text: str, live: LiveValues) -> str:
    check = OutputCheck(set(), [], live=live)
    out = [p for p in check.feed(text) if p is not None]
    out += [p for p in check.finish() if p is not None]
    return "".join(out)


def refusal(tool: str, code: str, on_hand: int, alternatives: list[Any]) -> str:
    envelope = {
        "isError": True,
        "errorCategory": "business",
        "isRetryable": False,
        "message": "That item is out of stock in your region.",
        "code": code,
    }
    data = {
        "region": "US",
        "requested": {"variant_id": V, "on_hand": on_hand, "in_stock": on_hand > 0},
        "alternatives": alternatives,
    }
    return json.dumps(envelope) + catalog_tool.BLOCK_SEPARATOR + catalog_tool.label(tool, data)


ALT = {
    "variant_id": "P0001-BLK-REG-L",
    "product_id": "P0001",
    "name": "Tidewick Harbor Rain Jacket",
    "colour": "Black",
    "size": "L",
    "fit": "regular",
    "on_hand": 9,
    "in_stock": True,
}


# ---- the assistant's tool names ----


def test_the_assistant_is_allowed_the_four_cart_tools_and_no_tool_that_could_place_an_order() -> (
    None
):
    assert set(catalog_tool.CART_TOOL_NAMES) == {
        f"mcp__{SERVER}__{n}"
        for n in ("get_cart", "add_to_cart", "remove_from_cart", "set_cart_quantity")
    }
    assert set(catalog_tool.CART_TOOL_NAMES) <= set(catalog_tool.ALLOWED_TOOLS)
    for name in catalog_tool.ALLOWED_TOOLS:
        short = name.rpartition("__")[2]
        assert not any(word in short for word in ORDER_WORDS), name


def test_the_assistants_scope_holds_none_of_the_forbidden_fragments() -> None:
    names = {n for names in tool_scopes.AGENT_TOOLS["shopper-assistant"].values() for n in names}
    for fragment in tool_scopes.FORBIDDEN_FRAGMENTS["shopper-assistant"]:
        assert not any(fragment in n for n in names), fragment


# ---- R11 for the other cart tools ----


def test_a_remove_result_joins_the_remaining_lines_figures() -> None:
    value = cart([line(V2, 2, "50.00", "100.00")], "100.00")
    text = catalog_tool.label(
        "remove_from_cart", {"result": "removed", "variant_id": V, "quantity": 0, "cart": value}
    )
    live = recorded(text, REMOVE)
    assert (Decimal("100.00"), "USD") in live.prices and live.stock == {V2: 6}


def test_a_set_quantity_result_joins_its_lines_and_subtotal() -> None:
    value = cart([line(V, 3, "19.99", "59.97")], "59.97")
    text = catalog_tool.label(
        "set_cart_quantity", {"result": "updated", "variant_id": V, "quantity": 3, "cart": value}
    )
    live = recorded(text, SET)
    assert {(Decimal("19.99"), "USD"), (Decimal("59.97"), "USD")} <= live.prices


def test_a_quantity_change_refusal_adds_only_the_requested_variants_stock() -> None:
    for code, on_hand in (("out_of_stock", 0), ("insufficient_stock", 2)):
        live = recorded(refusal("set_cart_quantity", code, on_hand, [ALT]), SET, is_error=True)
        assert live.stock == {V: on_hand} and live.prices == set()


def test_a_refusal_with_no_alternatives_still_states_the_requested_stock() -> None:
    live = recorded(refusal("add_to_cart", "out_of_stock", 0, []), ADD, is_error=True)
    assert live.stock == {V: 0}


def test_a_two_line_cart_passes_its_sum_and_blocks_a_sum_no_tool_returned() -> None:
    value = cart([line(V, 3, "19.99", "59.97"), line(V2, 7, "0.10", "0.70")], "60.67")
    live = recorded(
        catalog_tool.label("get_cart", value),
        f"mcp__{SERVER}__get_cart",
    )
    assert shown("Your subtotal is $60.67.", live) == "Your subtotal is $60.67."
    assert shown("Your subtotal is $60.00.", live) == FIXED_SENTENCES["money"]


def test_a_price_from_the_other_region_is_not_vouched() -> None:
    value = cart([line(V, 1, "139.00", "139.00")], "139.00")
    live = recorded(catalog_tool.label("get_cart", value), f"mcp__{SERVER}__get_cart", region="UK")
    assert live.prices == set() and live.stock == {}


# ---- a whole turn, scripted model, real mcp-catalog ----


class One:
    def search(self, query: str, caller: Caller) -> list[RetrievedChunk]:
        return [RetrievedChunk("c1", "product", "P0001", "product", "A rain jacket.")]


def run_turn(
    tmp_path: Path,
    store: FixtureCatalog,
    tool_input: dict[str, Any],
    tool: str,
    answer: str,
    session: str,
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """One turn: the model calls `tool`, then says `answer`. Returns what the shopper read, the
    requests the model received and the cart as the portal's reader sees it afterwards."""
    with (
        ScriptedLLM() as llm,
        serve_retriever(One(), Caller) as (url, _),
        catalog_testing.running(catalog_testing.server(store, key=KEY)) as catalog_url,
    ):
        llm.reply_tool_use(tool, tool_input)
        llm.reply_stream(answer)
        runtime = agent_runtime(base_url=llm.url, root=tmp_path / "rt")
        caller = Caller(region="US", session=session)

        async def go() -> tuple[list[Any], dict[str, Any] | None]:
            events = [
                e
                async for e in stream_answer(
                    "please add the black medium jacket",
                    caller,
                    RagEndpoint(url, KEY),
                    runtime,
                    CatalogEndpoint(catalog_url, KEY),
                )
            ]
            return events, await CartReader(catalog_url, KEY).read(caller)

        events, after = asyncio.run(go())
        text = "".join(e.text for e in events if isinstance(e, TextDelta))
        return text, [r["body"] for r in llm.requests], after


def test_a_turn_with_an_out_of_stock_add_gives_the_model_the_refusal_and_alternatives(
    tmp_path: Path,
) -> None:
    store = FixtureCatalog()
    store.set_stock(V, "US", 0)
    session = "ab" * 16
    text, requests, after = run_turn(
        tmp_path,
        store,
        {"variant_id": V, "idempotency_key": "assist-key-0005"},
        ADD,
        "That size is out of stock.",
        session,
    )
    assert text == "That size is out of stock."  # the one stock fact a refusal states passes
    assert len(requests) == 2
    seen_by_model = json.dumps(requests[1])
    assert "out_of_stock" in seen_by_model
    assert "alternatives" in seen_by_model
    assert session not in seen_by_model
    assert after is not None and after["lines"] == []  # a refused add changes nothing


def test_a_turn_with_an_in_stock_add_changes_the_cart_and_passes_the_live_total(
    tmp_path: Path,
) -> None:
    store = FixtureCatalog()
    store.set_stock(V, "US", 5)
    store.set_price(V, "US", "139.00", "USD")
    text, _, after = run_turn(
        tmp_path,
        store,
        {"variant_id": V, "quantity": 2, "idempotency_key": "assist-key-0002"},
        ADD,
        "Added two. Your cart total is $278.00.",
        "bc" * 16,
    )
    assert text == "Added two. Your cart total is $278.00."
    assert after is not None
    [added] = after["lines"]
    assert (added["variant_id"], added["quantity"], added["line_total"]) == (V, 2, "278.00")
    assert after["subtotal"] == "278.00"


def test_a_turn_that_states_a_total_the_cart_did_not_return_is_blocked(tmp_path: Path) -> None:
    store = FixtureCatalog()
    store.set_stock(V, "US", 5)
    store.set_price(V, "US", "139.00", "USD")
    text, _, _ = run_turn(
        tmp_path,
        store,
        {"variant_id": V, "quantity": 2, "idempotency_key": "assist-key-0002"},
        ADD,
        "Added two. Your cart total is $199.00.",
        "cd" * 16,
    )
    assert "$199.00" not in text
    assert FIXED_SENTENCES["money"] in text


def test_a_turn_cannot_reach_a_cart_tool_through_another_shoppers_session(
    tmp_path: Path,
) -> None:
    """The model's tool arguments cannot name a session: an unknown key is refused and nothing is
    written to either cart."""
    store = FixtureCatalog()
    store.set_stock(V, "US", 5)
    other = "de" * 16
    _, requests, after = run_turn(
        tmp_path,
        store,
        {"variant_id": V, "session_id": other, "idempotency_key": "assist-key-0003"},
        ADD,
        "I could not add that.",
        "ef" * 16,
    )
    assert "unknown_argument" in json.dumps(requests[1])
    assert after is not None and after["lines"] == []


# ---- the gateway's reader fails closed ----


def test_the_cart_reader_without_a_session_returns_none_and_never_calls_out() -> None:
    reader = CartReader("http://127.0.0.1:1/mcp", KEY)  # nothing listens here
    assert asyncio.run(reader.read(Caller(region="US"))) is None


def test_the_cart_reader_returns_the_servers_cart_for_the_callers_session() -> None:
    store = FixtureCatalog()
    store.set_stock(V, "US", 5)
    session = "f1" * 16
    with catalog_testing.running(catalog_testing.server(store, key=KEY)) as url:

        async def go() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
            bearer = catalog_testing.token("shopper-assistant", "US", session=session)[0]
            async with catalog_testing.connect(url, bearer) as client:
                await client.call_tool(
                    "add_to_cart", {"variant_id": V, "idempotency_key": "assist-key-0004"}
                )
            mine = await CartReader(url, KEY).read(Caller(region="US", session=session))
            other = await CartReader(url, KEY).read(Caller(region="US", session="a0" * 16))
            return mine, other

        mine, other = asyncio.run(go())
    assert mine is not None and [ln["variant_id"] for ln in mine["lines"]] == [V]
    assert other is not None and other["lines"] == []
