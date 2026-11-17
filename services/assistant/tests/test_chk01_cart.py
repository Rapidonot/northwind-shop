"""The assistant's side of the cart tools (row #16, CHK-01; plan R11, R14; amendments 2, 4 and 5).

- R11: cart results join the turn's live values only when well formed and for the shopper's own
  region. A refusal adds only the requested variant's stock, so "out of stock" can pass. The money
  check then passes the cart's own figures and blocks any other.
- Amendment 2: the session id never reaches the model's input. Two turns with different sessions
  send byte-identical requests, compared as the replay proxy matches them (`scrub`, `canonical`:
  only the CLI's own per-run `metadata.user_id` is scrubbed).
- Amendment 4: "no orders in chat" is conditional on the shopper asking; no unprompted capability
  statement.
- Amendment 5: no recorded assistant-redteam turn, and no recorded eval-answers turn for an
  adversarial item, calls a cart write.
"""

from __future__ import annotations

import asyncio
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CatalogEndpoint
from northwind_assistant.output_check import FIXED_SENTENCES, LiveValues, OutputCheck
from northwind_assistant.prompt import system_prompt
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.tools import ToolState, record_live
from northwind_assistant.turn import TextDelta, stream_answer
from northwind_common import catalog_tool
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_llm_replay.scrub import canonical, scrub
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("CHK-01")

REPO = Path(__file__).resolve().parents[3]
SERVER = catalog_tool.SERVER_NAME
ADD = f"mcp__{SERVER}__add_to_cart"
GET = f"mcp__{SERVER}__get_cart"
WRITES = {f"mcp__{SERVER}__{n}" for n in ("add_to_cart", "remove_from_cart", "set_cart_quantity")}
V = "P0001-BLK-REG-M"


def cart(region: str = "US", **line: Any) -> dict[str, Any]:
    row = {
        "variant_id": V,
        "product_id": "P0001",
        "name": "Tidewick Harbor Rain Jacket",
        "colour": "Black",
        "size": "M",
        "fit": "regular",
        "quantity": 2,
        "on_hand": 5,
        "in_stock": True,
        "unit_price": "139.00",
        "line_total": "278.00",
        "currency": "USD",
        **line,
    }
    return {
        "region": region,
        "lines": [row],
        "item_count": 2,
        "subtotal": "278.00",
        "currency": "USD",
    }


def added(value: dict[str, Any]) -> str:
    return catalog_tool.label(
        "add_to_cart", {"result": "added", "variant_id": V, "quantity": 2, "cart": value}
    )


def refusal(alternatives: list[Any], region: str = "US", code: str = "out_of_stock") -> str:
    envelope = {
        "isError": True,
        "errorCategory": "business",
        "isRetryable": False,
        "message": "That item is out of stock in your region.",
        "code": code,
    }
    data = {
        "region": region,
        "requested": {"variant_id": V, "on_hand": 0, "in_stock": False},
        "alternatives": alternatives,
    }
    return (
        json.dumps(envelope)
        + catalog_tool.BLOCK_SEPARATOR
        + catalog_tool.label("add_to_cart", data)
    )


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


def recorded(
    text: str, tool: str = ADD, *, is_error: bool = False, region: str = "US"
) -> LiveValues:
    state = ToolState(region=region)
    record_live(state, tool, is_error=is_error, text=text)
    return state.live


# ---- R11: what joins the live values -----------------------------------------------------------


def test_a_cart_result_adds_its_unit_prices_only() -> None:
    """Security review F2: no line total, no subtotal, no cart-line stock."""
    live = recorded(added(cart()))
    assert live.prices == {(Decimal("139.00"), "USD")}
    assert live.stock == {}


def test_get_cart_is_read_the_same_way() -> None:
    live = recorded(catalog_tool.label("get_cart", cart()), GET)
    assert live.prices == {(Decimal("139.00"), "USD")} and live.stock == {}


@pytest.mark.parametrize(
    "text",
    [
        added(cart(region="UK")),  # another region's cart
        added(cart(unit_price="139")),  # not two decimals
        added(cart(currency="usd")),
        added(cart(on_hand=5, in_stock=False)),  # inconsistent stock
        added(cart(on_hand=True)),
        catalog_tool.label("get_cart", cart()),  # the label names another tool than was called
        "not a label",
    ],
)
def test_a_malformed_or_foreign_cart_result_adds_nothing(text: str) -> None:
    live = recorded(text)
    assert live.prices == set() and live.stock == {}


def test_a_line_not_sold_in_the_region_adds_no_price_but_keeps_the_rest() -> None:
    value = cart(unit_price=None, line_total=None, currency=None)
    value.update(subtotal="0.00")
    live = recorded(added(value))
    assert live.prices == set() and live.stock == {}


def test_a_refusal_adds_only_the_requested_variants_stock() -> None:
    live = recorded(refusal([ALT]), is_error=True)
    assert live.stock == {V: 0} and live.prices == set()


@pytest.mark.parametrize(
    ("text", "is_error"),
    [
        (refusal([ALT]), False),  # an error's text, read as a success
        (refusal([ALT], code="not_in_cart"), True),  # not a stock refusal
        (refusal([ALT], region="UK"), True),
        (refusal([{**ALT, "in_stock": False}]), True),  # a malformed alternative
        (refusal([ALT]).replace(catalog_tool.BLOCK_SEPARATOR, ""), True),  # one block only
    ],
)
def test_anything_but_our_own_stock_refusal_adds_nothing(text: str, is_error: bool) -> None:
    live = recorded(text, is_error=is_error)
    assert live.prices == set() and live.stock == {}


def shown(text: str, live: LiveValues) -> str:
    check = OutputCheck(set(), [], live=live)
    out = [p for p in check.feed(text) if p is not None]
    out += [p for p in check.finish() if p is not None]
    return "".join(out)


def test_the_check_passes_a_unit_price_and_blocks_totals_and_cart_stock() -> None:
    """Security review F2: a total depends on a quantity the model chose, so it vouches nothing,
    even the cart's own; nor does a cart line's stock."""
    live = recorded(added(cart()))
    assert shown("It is $139.00 each.", live) == "It is $139.00 each."
    assert shown("Your cart total is $278.00.", live) == FIXED_SENTENCES["money"]
    assert shown("The Squall jacket is $278.00.", live) == FIXED_SENTENCES["money"]
    assert shown("Only 5 left.", live) == FIXED_SENTENCES["stock"]


def test_after_a_refusal_out_of_stock_passes_and_in_stock_does_not() -> None:
    live = recorded(refusal([ALT]), is_error=True)
    assert shown("That size is out of stock.", live) == "That size is out of stock."
    assert shown("The L is in stock.", live) == FIXED_SENTENCES["stock"]


# ---- amendment 2: no session in the model's input ----------------------------------------------


class One:
    def search(self, query: str, caller: Caller) -> list[RetrievedChunk]:
        return [RetrievedChunk("c1", "product", "P0001", "product", "A rain jacket.")]


def turn_requests(tmp_path: Path, session: str) -> list[dict[str, Any]]:
    store = FixtureCatalog()
    store.set_stock(V, "US", 5)
    with (
        ScriptedLLM() as llm,
        serve_retriever(One(), Caller) as (url, _),
        catalog_testing.running(catalog_testing.server(store, key=KEY)) as catalog_url,
    ):
        llm.reply_tool_use(ADD, {"variant_id": V, "quantity": 1, "idempotency_key": "turn-add-01"})
        llm.reply_stream("Added it to your cart.")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path / session)

        async def go() -> list[Any]:
            caller = Caller(region="US", session=session)
            catalog = CatalogEndpoint(catalog_url, KEY)
            return [
                e
                async for e in stream_answer(
                    "add one", caller, RagEndpoint(url, KEY), runtime, catalog
                )
            ]

        events = asyncio.run(go())
        assert (
            "".join(e.text for e in events if isinstance(e, TextDelta)) == "Added it to your cart."
        )
        return [r["body"] for r in llm.requests]


def test_the_session_never_reaches_the_model_and_two_sessions_send_the_same_bytes(
    tmp_path: Path,
) -> None:
    first, second = "1" * 32, "2" * 32
    a, b = turn_requests(tmp_path, first), turn_requests(tmp_path, second)
    assert len(a) == 2  # the tool call, then the answer after the cart result
    assert "added" in json.dumps(a[1])  # the cart result reached the model
    for bodies, session in ((a, first), (b, second)):
        assert session not in json.dumps(bodies)
    assert [canonical(scrub(x)) for x in a] == [canonical(scrub(y)) for y in b]


# ---- R14 and amendment 4: the prompt -----------------------------------------------------------


def test_the_prompt_states_the_cart_rules_and_no_unprompted_capability() -> None:
    prompt = system_prompt("US")
    assert "Change the cart only when the shopper asks" in prompt
    assert "offer only the alternatives that result lists" in prompt
    assert "each by its product name and variant ID" in prompt  # security review F8
    assert 'A cart result of "already_applied" changed nothing now' in prompt  # re-review N1
    assert "or saying whether they are in stock" in prompt
    # "No orders in chat" only on the shopper's asking, never as a self-description.
    [sentence] = [s for s in re.split(r"(?<=\.)\s", prompt) if "orders can't be placed" in s]
    assert sentence.startswith("If the shopper asks to place an order or check out")
    assert (
        "Do not describe your own abilities or offer to check anything unless the shopper asks."
        in prompt
    )


# ---- amendment 5: no cart write in a recorded adversarial turn ---------------------------------


def cart_writes(cassette: dict[str, Any]) -> set[str]:
    """Cart writes the recorded response called."""
    names = set(
        re.findall(r'"type":\s*"tool_use"[^}]*?"name":\s*"([^"]+)"', str(cassette.get("body", "")))
    )
    return names & WRITES


def offered_cart_writes(cassette: dict[str, Any]) -> bool:
    """Whether the recorded request offered the model every cart write (security review F3: a
    cassette that never offered them proves nothing)."""
    body = (cassette.get("match") or {}).get("body") or {}
    offered = {str(tool.get("name", "")) for tool in body.get("tools") or []}
    return WRITES <= offered


def adversarial_items() -> set[str]:
    rows = (REPO / "evals/fixtures/starter_set.jsonl").read_text(encoding="utf-8").splitlines()
    items = [json.loads(row) for row in rows if row.strip()]
    return {item["id"] for item in items if item.get("segment") == "adversarial"}


def test_no_recorded_red_team_turn_writes_a_cart() -> None:
    cassettes = sorted((REPO / "cassettes/assistant-redteam").glob("*.json"))
    assert cassettes
    for path in cassettes:
        cassette = json.loads(path.read_text(encoding="utf-8"))
        # Stale by design until W2 re-records the suite with the cart tools offered (F3).
        assert offered_cart_writes(cassette), f"{path.name}: recorded before the cart tools"
        assert cart_writes(cassette) == set(), path.name


def test_no_recorded_adversarial_eval_turn_writes_a_cart() -> None:
    adversarial = adversarial_items()
    assert adversarial
    seen = 0
    for path in sorted((REPO / "cassettes/eval-answers").glob("*.json")):
        cassette = json.loads(path.read_text(encoding="utf-8"))
        item = str(cassette.get("test_id", "")).rpartition(":")[2]
        if item in adversarial:
            seen += 1
            # Stale by design until W5 re-records eval-answers with the cart tools offered (F3).
            assert offered_cart_writes(cassette), f"{item}: recorded before the cart tools"
            assert cart_writes(cassette) == set(), (item, path.name)
    assert seen, "no adversarial eval cassette found"


def test_the_cart_write_scan_sees_a_recorded_tool_use() -> None:
    body = (
        'event: content_block_start\ndata: {"type":"content_block_start","index":1,'
        '"content_block":{"type":"tool_use","id":"toolu_1","name":"' + ADD + '","input":{}}}\n\n'
    )
    assert cart_writes({"body": body}) == {ADD}
