"""Order status and tracking through the real assistant and mcp-orders (row #27, CHK-06).

`make record SUITE=assistant-orders` runs the six recorded scenarios through the user's runner
window and creates the cassettes they replay. As in `test_cart_journey_e2e`, the `e2e` marker keeps
them out of `make test` and `make test-integration`, the first search is a fixed retriever and there
is no understander: no index, no frozen query vector and no query-understanding call. The assistant,
its signed-in prompt, mcp-rag's protocol, mcp-catalog and mcp-orders are all real. Each scenario is
its own turn for the one demo shopper, S0188.

What is checked comes first from the server: which order was read, for which shopper, and the result
it returned, compared with what `data/backend` says. The model's words are checked only for what the
server cannot show: the status it gave, that every date it gave is one of that result's dates, and
that nothing is stated for an order the server did not return.

The checks themselves run at $0 in `make test` on a scripted model, good answers and bad ones.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CatalogEndpoint
from northwind_assistant.orders_client import OrdersEndpoint
from northwind_assistant.output_check import FIXED_SENTENCES
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.turn import Citations, TextDelta, TurnError, TurnEvent, stream_answer
from northwind_common import order_tool
from northwind_common.errors import ToolError
from northwind_common.llm import AgentRuntime, agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_orders import testing as orders_testing
from northwind_mcp_orders.book import OrderBook
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk, ShopperRegion

pytestmark = pytest.mark.req("CHK-06")

REPO = Path(__file__).resolve().parents[3]
SHOPPER = "S0188"
REGION: ShopperRegion = "US"
CALLER = Caller(region=REGION, session="0" * 31 + "d", shopper_id=SHOPPER)
POLICY = "POL-SHIPPING-US"

IN_TRANSIT, DELIVERED = "O000928", "O000800"
LABEL_CREATED, ON_HOLD = "O000406", "O000418"
NOT_THEIRS = "O000001"

MONTHS = {
    name: n
    for n, names in enumerate(
        "january|jan february|feb march|mar april|apr may june|jun july|jul august|aug "
        "september|sept|sep october|oct november|nov december|dec".split(),
        start=1,
    )
    for name in names.split("|")
}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?: of)? ({_MONTH})\b", re.I)
_MONTH_DAY = re.compile(rf"\b({_MONTH})\.? (\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
_SLASHED = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")

# A status as an answer would state it. "Delivery" and "tracking" are not statuses.
STATUS_WORDS = re.compile(
    r"\b(delivered|in transit|out for delivery|picked up|label (?:has been )?created|on hold|"
    r"being processed|processing|shipped|dispatched|confirmed|on its way)\b",
    re.I,
)

# Scans the carrier has not made yet, by the status the server returned.
NOT_YET = {
    "label_created": "picked up|in transit|out for delivery|delivered",
    "in_transit": "out for delivery|delivered",
}


def dates_in(text: str) -> set[tuple[int, int]]:
    """Every (month, day) the text gives; a slashed date, which no result holds, is (0, 0)."""
    found = {(int(m[2]), int(m[3])) for m in _ISO.finditer(text)}
    found |= {(MONTHS[m[2].lower()], int(m[1])) for m in _DAY_MONTH.finditer(text)}
    found |= {(MONTHS[m[1].lower()], int(m[2])) for m in _MONTH_DAY.finditer(text)}
    if _SLASHED.search(text):
        found.add((0, 0))
    return found


@cache
def _rows(name: str) -> dict[str, dict[str, Any]]:
    text = (REPO / "data/backend" / name).read_text(encoding="utf-8")
    return {r["order_id"]: r for r in map(json.loads, filter(str.strip, text.splitlines()))}


def expected_result(order_id: str, status: str) -> dict[str, Any]:
    """What get_order must return, read straight from `data/backend` and not through `OrderBook`."""
    order, parcel = _rows("orders.jsonl")[order_id], _rows("tracking.jsonl").get(order_id)
    assert (order["shopper_id"], order["region"]) == (SHOPPER, REGION)
    return {
        "order_id": order_id,
        "placed_on": order["placed_on"],
        "status": status,
        "lines": [
            {k: line[k] for k in ("product_id", "variant_id", "quantity")}
            for line in order["lines"]
        ],
        "tracking": None
        if parcel is None
        else {
            "carrier": parcel["carrier"],
            "tracking_number": parcel["tracking_number"],
            "scans": [{"on": e["on"], "status": e["status"]} for e in parcel["events"]],
        },
    }


def result_dates(result: Mapping[str, Any]) -> set[tuple[int, int]]:
    scans = (result["tracking"] or {"scans": []})["scans"]
    days = [date.fromisoformat(d) for d in (result["placed_on"], *(s["on"] for s in scans))]
    return {(d.month, d.day) for d in days}


@dataclass
class WatchedBook(OrderBook):
    """The real book, and what each read asked for and returned (a result, or an error's code)."""

    reads: list[tuple[str, str, str | None, Any]] = field(default_factory=list)

    def view(self, order_id: str, *, shopper: str, region: str | None) -> Mapping[str, Any]:
        try:
            result = super().view(order_id, shopper=shopper, region=region)
        except ToolError as error:
            self.reads.append((order_id, shopper, region, error.code))
            raise
        self.reads.append((order_id, shopper, region, dict(result)))
        return result


class PolicySource:
    """The first search's one source: the shopper's own public shipping policy."""

    def __init__(self) -> None:
        text = (REPO / "data/fixtures/documents.jsonl").read_text(encoding="utf-8")
        [doc] = [d for d in map(json.loads, text.splitlines()) if d["doc_id"] == POLICY]
        assert (doc["visibility"], doc["region"]) == ("public", REGION)  # hard rule 7
        self.chunks = [
            RetrievedChunk(f"document:{POLICY}:0", "document", POLICY, REGION, doc["body"])
        ]

    def search(self, query: str, caller: Caller) -> list[RetrievedChunk]:
        return list(self.chunks)


@dataclass(frozen=True)
class Turn:
    answer: str
    reads: list[tuple[str, str, str | None, Any]]
    tool_calls: int  # every call that reached mcp-orders, refused ones included


def run_turn(message: str, runtime: AgentRuntime) -> Turn:
    real = OrderBook.from_data()
    book = WatchedBook(real.orders, real.carrier, real.tracking_numbers, real.placed_on)
    orders_server = orders_testing.server(book, key=KEY)

    async def collect(rag: RagEndpoint, catalog_url: str, orders_url: str) -> list[TurnEvent]:
        return [
            e
            async for e in stream_answer(
                message,
                CALLER,
                rag,
                runtime,
                CatalogEndpoint(catalog_url, KEY),
                orders=OrdersEndpoint(orders_url, KEY),
            )
        ]

    with (
        serve_retriever(PolicySource(), Caller, key=KEY) as (url, _),
        catalog_testing.running(catalog_testing.server(key=KEY)) as catalog_url,
        orders_testing.running(orders_server) as orders_url,
    ):
        events = asyncio.run(collect(RagEndpoint(url, KEY), catalog_url, orders_url))
    assert not [e for e in events if isinstance(e, TurnError)], events
    assert isinstance(events[-1], Citations)
    answer = "".join(e.text for e in events if isinstance(e, TextDelta))
    return Turn(answer, book.reads, orders_server.calls)


@dataclass(frozen=True)
class Scenario:
    message: str
    order_id: str | None  # the order the shopper names
    status: str | None  # what the server must return for it; None for not found
    says: str  # what the answer must say
    planned_requests: int  # model requests in the recording: get_order then the answer, or one


SCENARIOS: dict[str, Scenario] = {
    "in_transit": Scenario(
        f"Where is my order {IN_TRANSIT}?", IN_TRANSIT, "in_transit", r"in transit", 2
    ),
    "delivered": Scenario(
        f"Has order {DELIVERED} arrived yet?", DELIVERED, "delivered", r"delivered", 2
    ),
    "label_created_no_scans": Scenario(
        f"What's happening with order {LABEL_CREATED}?", LABEL_CREATED, "label_created", r"label", 2
    ),
    "on_hold": Scenario(
        f"Why hasn't order {ON_HOLD} shipped?", ON_HOLD, "on_hold", r"hold|a person|someone", 2
    ),
    "another_shoppers_order": Scenario(
        f"Where is order {NOT_THEIRS}?",
        NOT_THEIRS,
        None,
        r"(?:could ?n[o']t|could not|can(?:no|')t|unable to|did ?n[o']t) (?:find|locate)|"
        r"not? (?:find|found)|no order",
        2,
    ),
    "no_order_number": Scenario("where's my order?", None, None, r"order number", 1),
}


def check(scenario: Scenario, turn: Turn) -> None:
    answer = turn.answer.replace("’", "'")
    # The status and wording checks read a status code as its words ("in_transit", "in transit").
    # The private values are looked for in the exact answer.
    words = answer.replace("_", " ")
    assert re.search(scenario.says, words, re.I), answer
    # Nothing was replaced by the output check: the scenario is the answer the model gave.
    assert not [s for s in FIXED_SENTENCES.values() if s in turn.answer], turn.answer

    if scenario.order_id is None:
        assert turn.reads == [] and turn.tool_calls == 0
        assert "?" in answer
        assert not STATUS_WORDS.search(words) and not dates_in(answer), answer
        return

    assert turn.reads, "get_order was never called"
    if scenario.status is None:
        assert _rows("orders.jsonl")[scenario.order_id]["shopper_id"] != SHOPPER
        assert {r[:3] for r in turn.reads} == {(scenario.order_id, SHOPPER, REGION)}
        assert {r[3] for r in turn.reads} == {"not_found"}
        assert not STATUS_WORDS.search(words) and not dates_in(answer), answer
        for value in _private_values(scenario.order_id, with_order=True):
            assert value not in turn.answer, value
        return

    expected = expected_result(scenario.order_id, scenario.status)
    for read in turn.reads:
        assert read == (scenario.order_id, SHOPPER, REGION, expected)
    assert scenario.order_id in answer
    assert dates_in(answer) <= result_dates(expected), (dates_in(answer), answer)
    for value in _private_values(scenario.order_id, with_order=False):
        assert value not in turn.answer, value
    for sentence in re.split(r"(?<=[.;!?])\s+", words):
        if re.search(rf"\b(?:{NOT_YET.get(scenario.status, '$^')})\b", sentence, re.I):
            # only ever to say it has not happened, in that same sentence
            assert re.search(r"n't\b|\b(?:not|no|once|until|before)\b", sentence, re.I), answer
    if scenario.status == "label_created":
        assert expected["tracking"]["scans"] == []  # a valid empty result, not an error
    if scenario.status == "on_hold":
        label = _rows("replay_labels.jsonl")[scenario.order_id]
        assert label["expected_outcome"] == "escalated" and label["expected_reason"]
        # The reason is in no result and no answer, in the code's words or a shopper's.
        assert label["expected_reason"] not in json.dumps(turn.reads)
        assert not re.search(
            r"out[ _-]of[ _-]stock|stock|sold out|unavailable|inventory|escalat", words, re.I
        ), answer


def _private_values(order_id: str, *, with_order: bool) -> list[str]:
    """What no answer may hold; `with_order` adds the order's own fields, for another shopper's."""
    order, parcel = _rows("orders.jsonl")[order_id], _rows("tracking.jsonl").get(order_id)
    values = [order["payment_token"], order["shipping_postal_code"], order["shopper_id"], SHOPPER]
    if with_order:
        values += [line["variant_id"] for line in order["lines"]]
        values += [parcel["carrier"], parcel["tracking_number"]] if parcel else []
    return values


# ---- the six recorded scenarios (`make record SUITE=assistant-orders`, then replay) -------------


@pytest.mark.e2e
@pytest.mark.llm_suite("assistant-orders")
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_recorded(name: str, llm_proxy: Any, tmp_path: Path) -> None:
    scenario = SCENARIOS[name]
    check(scenario, run_turn(scenario.message, agent_runtime(root=tmp_path)))


# ---- $0: the checks, on a scripted model --------------------------------------------------------

GOOD = {
    "in_transit": f"Order {IN_TRANSIT} is in transit. It was last scanned on 16 October.",
    "delivered": f"{DELIVERED} was delivered on 26 October.",
    "label_created_no_scans": f"A label has been created for {LABEL_CREATED}; no scans yet.",
    "on_hold": f"{ON_HOLD} is on hold, and a person is looking at it.",
    "another_shoppers_order": f"I couldn't find order {NOT_THEIRS} on your account.",
    "no_order_number": "What's your order number?",
}
BAD = [
    ("in_transit", f"Order {IN_TRANSIT} is in transit and arrives on October 30th."),
    ("in_transit", f"Order {IN_TRANSIT} was picked up."),
    ("in_transit", "Your order is in transit."),
    ("in_transit", f"{IN_TRANSIT} was in transit. It has been delivered."),
    ("delivered", f"{DELIVERED} was delivered on 2026-10-27."),
    ("delivered", f"{DELIVERED} was delivered on 10/26."),
    ("label_created_no_scans", f"The label for {LABEL_CREATED} was made; it is in transit."),
    ("label_created_no_scans", f"{LABEL_CREATED} has a label. It was delivered. No other scans."),
    ("on_hold", f"{ON_HOLD} is on hold because an item is out of stock."),
    ("on_hold", f"{ON_HOLD} is on hold: it was escalated to a person."),
    ("on_hold", f"{ON_HOLD} is on hold. A person will ship it to US-3391."),
    ("on_hold", f"{ON_HOLD} is on hold. A person has tok_sandbox_ok_0188."),
    ("another_shoppers_order", f"I couldn't find {NOT_THEIRS}, but it was picked up."),
    ("another_shoppers_order", "I couldn't find it. It was with Fernway Freight."),
    ("another_shoppers_order", f"I couldn't find {NOT_THEIRS}, placed on 11 October."),
    ("no_order_number", "What's your order number? O000800 was delivered on 26 October."),
    ("no_order_number", "Please give me your order number? It is on its way."),
    ("no_order_number", "I can't look that up."),
    # Underscored status codes: the same statement, in the result's own spelling.
    ("in_transit", f"{IN_TRANSIT} was in_transit. Its status is out_for_delivery."),
    (
        "label_created_no_scans",
        f"The label for {LABEL_CREATED} was made; its status is in_transit.",
    ),
    ("label_created_no_scans", f"{LABEL_CREATED} has a label_created status. It was picked_up."),
    ("another_shoppers_order", f"I couldn't find {NOT_THEIRS}, but its status is on_hold."),
    ("another_shoppers_order", f"I couldn't find {NOT_THEIRS}; it is not yet in_transit."),
    ("another_shoppers_order", f"I couldn't find {NOT_THEIRS}, only a label_created status."),
    ("no_order_number", "What's your order number? Its status is on_hold."),
    ("no_order_number", "What's your order number? It is not yet in_transit."),
    ("no_order_number", "What's your order number? The last scan is out_for_delivery."),
    ("no_order_number", "What's your order number? The last scan is picked_up."),
    ("no_order_number", "What's your order number? It is label_created."),
]


def scripted(name: str, answer: str, root: Path, *, call: str | None) -> Turn:
    with ScriptedLLM() as llm:
        if call is not None:
            llm.reply_tool_use(order_tool.GET_ORDER_TOOL, {"order_id": call}, tool_id="toolu_a")
        llm.reply(answer)
        turn = run_turn(SCENARIOS[name].message, agent_runtime(base_url=llm.url, root=root))
        assert len(llm.requests) == SCENARIOS[name].planned_requests
    return turn


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_a_right_answer_passes_its_scenario(name: str, tmp_path: Path) -> None:
    scenario = SCENARIOS[name]
    check(scenario, scripted(name, GOOD[name], tmp_path, call=scenario.order_id))


UNDERSCORED = [
    (
        "in_transit",
        f"Order {IN_TRANSIT}: its status is in_transit. Last scan 2026-10-16: in_transit.",
    ),
    ("label_created_no_scans", f"{LABEL_CREATED} is label_created. It is not yet in_transit."),
    ("on_hold", f"{ON_HOLD}: its status is on_hold. A person is looking at it."),
]


@pytest.mark.parametrize(("name", "answer"), UNDERSCORED)
def test_a_right_answer_in_the_results_own_spelling_passes(
    name: str, answer: str, tmp_path: Path
) -> None:
    check(SCENARIOS[name], scripted(name, answer, tmp_path, call=SCENARIOS[name].order_id))


@pytest.mark.parametrize(("name", "answer"), BAD)
def test_a_wrong_answer_fails_its_scenario(name: str, answer: str, tmp_path: Path) -> None:
    scenario = SCENARIOS[name]
    turn = scripted(name, answer, tmp_path, call=scenario.order_id)
    with pytest.raises(AssertionError):
        check(scenario, turn)


@pytest.mark.parametrize("name", [n for n, s in SCENARIOS.items() if s.order_id])
def test_an_answer_without_the_tool_call_fails(name: str, tmp_path: Path) -> None:
    """A right-sounding answer from memory or from the prompt is not enough (hard rule 2)."""
    with ScriptedLLM() as llm:
        llm.reply(GOOD[name])
        turn = run_turn(SCENARIOS[name].message, agent_runtime(base_url=llm.url, root=tmp_path))
    with pytest.raises(AssertionError, match="never called"):
        check(SCENARIOS[name], turn)


def test_reading_another_order_fails(tmp_path: Path) -> None:
    turn = scripted("in_transit", GOOD["in_transit"], tmp_path, call=DELIVERED)
    with pytest.raises(AssertionError):
        check(SCENARIOS["in_transit"], turn)


def test_a_tool_call_with_no_order_number_given_fails(tmp_path: Path) -> None:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(order_tool.GET_ORDER_TOOL, {"order_id": DELIVERED}, tool_id="toolu_a")
        llm.reply(GOOD["no_order_number"])
        turn = run_turn("where's my order?", agent_runtime(base_url=llm.url, root=tmp_path))
    with pytest.raises(AssertionError):
        check(SCENARIOS["no_order_number"], turn)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("on 26 October", {(10, 26)}),
        ("on the 26th of October", {(10, 26)}),
        ("on October 26, 2026", {(10, 26)}),
        ("Oct. 26 and 2026-10-14", {(10, 26), (10, 14)}),
        ("10/26/2026", {(0, 0)}),
        ("3-5 business days, 2 items, order O000800", set()),
    ],
)
def test_dates_in(text: str, expected: set[tuple[int, int]]) -> None:
    assert dates_in(text) == expected
