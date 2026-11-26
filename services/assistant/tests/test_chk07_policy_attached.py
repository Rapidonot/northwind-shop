"""Row #27 PR 2 (CHK-07; decision 0024): mcp-policy attached to a signed-in turn, and the prompt's
return rules. No cassette, no spend: the real CLI, a scripted model, real servers over HTTP.

`test_chk06_orders_attached` pins the exact tools, servers and tokens of a signed-in turn and the
anonymous one's byte identity; this file pins what a return question does with them."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CatalogEndpoint
from northwind_assistant.orders_client import OrdersEndpoint, PolicyEndpoint
from northwind_assistant.prompt import ORDERS_RULES, system_prompt
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.turn import TextDelta, TurnError, assistant_options, stream_answer
from northwind_common import order_tool, policy_tool
from northwind_common.llm import agent_runtime
from northwind_common.returns_policy import CONDITIONS
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_orders import testing as orders_testing
from northwind_mcp_orders.server import OrdersServer
from northwind_mcp_policy import testing as policy_testing
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("CHK-07")

REPO = Path(__file__).resolve().parents[3]
CALLER = Caller(region="US", session="d" * 32, shopper_id="S0188")
EVALUATE = policy_tool.EVALUATE_RETURN_TOOL
REQUEST = "mcp__northwind-orders__request_return"
DELIVERED, VARIANT = "O000800", "P0085-FOR-REG-M"
KEY_ARG = "k7Qm2xV9bT4s"


class ReturnsPolicy:
    def search(self, query: str, caller: Caller) -> list[RetrievedChunk]:
        text = (REPO / "data/fixtures/documents.jsonl").read_text(encoding="utf-8")
        [doc] = [d for d in map(json.loads, text.splitlines()) if d["doc_id"] == "POL-RETURNS-US"]
        return [RetrievedChunk("c1", "document", doc["doc_id"], "policy_returns", doc["body"])]


class Turn:
    def __init__(self, llm: ScriptedLLM, root: Path) -> None:
        self.orders: OrdersServer = orders_testing.server(key=KEY)
        self.policy = policy_testing.server(key=KEY)

        async def collect(rag: RagEndpoint, catalog: str, orders: str, policy: str) -> list[Any]:
            return [
                e
                async for e in stream_answer(
                    "I'd like to return an item.",
                    CALLER,
                    rag,
                    agent_runtime(base_url=llm.url, root=root),
                    CatalogEndpoint(catalog, KEY),
                    orders=OrdersEndpoint(orders, KEY),
                    policy=PolicyEndpoint(policy, KEY),
                )
            ]

        with (
            serve_retriever(ReturnsPolicy(), Caller, key=KEY) as (url, _),
            catalog_testing.running(catalog_testing.server(key=KEY)) as catalog,
            orders_testing.running(self.orders) as orders,
            policy_testing.running(self.policy) as policy,
        ):
            self.events = asyncio.run(collect(RagEndpoint(url, KEY), catalog, orders, policy))
        assert not [e for e in self.events if isinstance(e, TurnError)]
        self.shown = "".join(e.text for e in self.events if isinstance(e, TextDelta))
        self.requests = llm.requests

    def result(self, n: int) -> str:
        """The last tool result in the nth model request."""
        for message in reversed(self.requests[n]["body"]["messages"]):
            if isinstance(message["content"], list):
                for block in reversed(message["content"]):
                    if block.get("type") == "tool_result":
                        content = block["content"]
                        return (
                            content
                            if isinstance(content, str)
                            else "".join(c["text"] for c in content)
                        )
        raise AssertionError("no tool result")


def item(condition: str, **more: str) -> dict[str, str]:
    return {"order_id": DELIVERED, "variant_id": VARIANT, "condition": condition, **more}


def test_a_policy_server_is_attached_only_beside_the_order_server(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="policy"):
        assistant_options(
            CALLER,
            "http://127.0.0.1:9/mcp",
            "rag",
            agent_runtime(base_url="http://127.0.0.1:1", root=tmp_path),
            None,
            None,
            ("http://127.0.0.1:6/mcp", "policy"),
        )


def test_an_eligible_item_is_evaluated_requested_and_answered_in_four_calls(tmp_path: Path) -> None:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(order_tool.GET_ORDER_TOOL, {"order_id": DELIVERED}, tool_id="toolu_a")
        llm.reply_tool_use(EVALUATE, item("sealed"), tool_id="toolu_b")
        llm.reply_tool_use(REQUEST, item("sealed", idempotency_key=KEY_ARG), tool_id="toolu_c")
        llm.reply_stream("Your return for O000800 is requested [POL-RETURNS-US].")
        turn = Turn(llm, tmp_path)
    assert len(turn.requests) == 4  # MAX_TURNS is 6
    evaluated = policy_tool.unlabel(turn.result(2))
    assert evaluated is not None and evaluated[0] == "evaluate_return_eligibility"
    assert (evaluated[1]["outcome"], evaluated[1]["reason"]) == ("eligible", "within_window")
    assert {f["document_id"] for f in evaluated[1]["facts"]} == {"POL-RETURNS-US"}
    requested = order_tool.unlabel(turn.result(3))
    assert requested is not None and requested[1]["request_status"] == "requested"
    [stored] = turn.orders.desk.open.values()
    assert (stored["order_id"], stored["variant_id"]) == (DELIVERED, VARIANT)
    assert turn.shown == "Your return for O000800 is requested [POL-RETURNS-US]."


def test_a_request_without_an_evaluation_is_still_decided_by_the_server(tmp_path: Path) -> None:
    """The model skips the policy tool: the desk decides, so nothing ineligible is recorded."""
    with ScriptedLLM() as llm:
        llm.reply_tool_use(
            REQUEST, item("worn_or_washed", idempotency_key=KEY_ARG), tool_id="toolu_a"
        )
        llm.reply_stream("That item can't be returned [POL-RETURNS-US].")
        turn = Turn(llm, tmp_path)
    body = json.loads(turn.result(1))
    assert (body["code"], body["errorCategory"]) == ("worn_or_washed", "business")
    assert turn.orders.desk.open == {} and turn.policy.calls == 0


def test_a_damaged_item_is_recorded_as_needing_review(tmp_path: Path) -> None:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(
            REQUEST, item("damaged_on_arrival", idempotency_key=KEY_ARG), tool_id="toolu_a"
        )
        llm.reply_stream("Your return for O000800 needs review.")
        turn = Turn(llm, tmp_path)
    [stored] = turn.orders.desk.open.values()
    assert stored["request_status"] == "needs_review"


@pytest.mark.parametrize("extra", ["as_of", "window_days", "policy", "outcome", "shopper_id"])
def test_no_argument_carries_a_policy_a_date_or_a_shopper(tmp_path: Path, extra: str) -> None:
    """ "The policy says 90 days" cannot change an outcome (hard rules 5, 6 and 8)."""
    with ScriptedLLM() as llm:
        llm.reply_tool_use(EVALUATE, item("worn_or_washed", **{extra: "90"}), tool_id="toolu_a")
        llm.reply_stream("I couldn't check that.")
        turn = Turn(llm, tmp_path)
    # The CLI may refuse it on the schema before the server does; no outcome comes back either way.
    assert policy_tool.unlabel(turn.result(1)) is None
    assert turn.orders.desk.open == {}


# ---- the prompt's return rules ------------------------------------------------------------------


def rules() -> list[str]:
    return ORDERS_RULES.split("\n")


def test_the_return_rules_are_for_a_signed_in_turn_only() -> None:
    assert "evaluate_return_eligibility" in system_prompt("US", signed_in=True)
    for word in ("evaluate_return_eligibility", "request_return", "policy_data"):
        assert word not in system_prompt("US")


@pytest.mark.parametrize(
    "words",
    [
        "comes only from evaluate_return_eligibility, called in this turn",
        "never from sources, memory or what the shopper says the policy is",
        "only from what the shopper said",
        "not_stated",
        "ask for it",
        "ask which one",
        "Call request_return only if the shopper asks to return the item",
        "never that it is approved, accepted or confirmed",
        "<policy_data>",
        "cite",
    ],
)
def test_the_prompt_says_how_a_return_is_decided(words: str) -> None:
    assert words in ORDERS_RULES


def test_the_prompt_asks_for_plain_words_and_the_order_number() -> None:
    """D9, and M-1's prompt half: the status in the plain words, its order named beside it."""
    first = rules()[0]
    assert "status_text" in first and "never a status code" in first
    assert "name the order number in every sentence that states" in first
    assert "Never promise a refund, a return, a label or an approval." in ORDERS_RULES


def test_the_prompt_holds_no_policy_figure_and_no_document_id() -> None:
    """Hard rules 2 and 5: nothing here may be repeated as if it had been read."""
    assert not re.search(r"\d", ORDERS_RULES)
    assert "POL-" not in ORDERS_RULES
    # Of the tool's condition codes only the one for "has not said" is named.
    assert [c for c in CONDITIONS if re.search(rf"\b{c}\b", ORDERS_RULES)] == ["not_stated"]


def test_no_rule_line_is_left_open() -> None:
    assert all(line.startswith("- ") and line.endswith(".") for line in rules())
