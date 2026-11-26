"""Row #27 (CHK-06; decision 0023): mcp-orders attached to a signed-in turn only. No cassette, no
spend: the real CLI, a scripted model, a real mcp-rag, mcp-catalog and mcp-orders over HTTP.

- A signed-in turn offers `get_order` and carries the prompt's order section; an anonymous turn's
  request is byte for byte the one it was before row #27 (D3), even with an orders endpoint passed.
  PR 2 (CHK-07): and, beside mcp-orders, mcp-policy's one tool and token, likewise; still exact.
- The orders token carries the shopper (`shp`); no other token does, and no other server takes it.
- A `get_order` result reaches the model as labelled data. It vouches for nothing in the output
  check: "your order was placed" stays blocked on every turn (decision 0015's row #27 note).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CatalogEndpoint
from northwind_assistant.orders_client import OrdersEndpoint, PolicyEndpoint
from northwind_assistant.output_check import CONFIRMATION_SENTENCE
from northwind_assistant.prompt import ORDERS_RULES, system_prompt
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.tools import ALLOWED_TOOL
from northwind_assistant.turn import TextDelta, TurnEvent, assistant_options, stream_answer
from northwind_common import catalog_tool, order_tool, policy_tool
from northwind_common.llm import AgentRuntime, agent_runtime
from northwind_common.mcp_token import (
    CATALOG_SERVER,
    ORDERS_SERVER,
    POLICY_SERVER,
    RAG_SERVER,
    TokenError,
    verify_for,
)
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_llm_replay.scrub import canonical, scrub
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_orders import testing as orders_testing
from northwind_mcp_policy import testing as policy_testing
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("CHK-06")

SESSION = "d" * 32
ANON = Caller(region="US", session=SESSION)
SIGNED_IN = Caller(region="US", session=SESSION, shopper_id="S0188")
SOURCE = RetrievedChunk("c1", "document", "POL-001", None, "Orders ship within 2 business days.")
GET_ORDER = order_tool.GET_ORDER_TOOL

# sha256 of `system_prompt(region)`. Row #27 pinned origin/main f84f97d's here (D3's byte-identity:
# row #27 changed no anonymous prompt). Row #30 (SA-04 and D8's remainder) changes the prompt for
# every shopper, anonymous and signed in, so these are re-pinned on purpose at its merge of row #27.
# Re-pinned once more for row #30's one prompt fix round (the user's option (b), 2026-11-25).
MAIN_PROMPTS = {
    "US": "fbb0200f5c09a66097ac39baa7b5c804d0b878cb92d9effa662bfb29f6786b18",
    "CA": "57b5d8c701eb3b4442ef131a5f2c83f5ac01ef70c165cd06135e43ffb5f1d462",
    "UK": "87f2bacc269530dc73e6f194e82122b00f5b4de12104151a04ceb435e3706a2f",
    "EU": "d1c797ce5de97265e5bd6eaadf192c5aab32830a177dfeab4deaf7ac754e3148",
}


class OneSource:
    def search(self, query: str, caller: Caller) -> list[RetrievedChunk]:
        return [SOURCE]


def runtime_for(llm: ScriptedLLM, root: Path) -> AgentRuntime:
    return agent_runtime(base_url=llm.url, root=root)


def ask(
    message: str,
    caller: Caller,
    runtime: AgentRuntime,
    orders_url: str | None,
    policy_url: str | None = None,
) -> list[Any]:
    catalog = catalog_testing.server(key=KEY)

    async def collect(rag: RagEndpoint, catalog_url: str) -> list[TurnEvent]:
        orders = None if orders_url is None else OrdersEndpoint(orders_url, KEY)
        policy = None if policy_url is None else PolicyEndpoint(policy_url, KEY)
        return [
            e
            async for e in stream_answer(
                message,
                caller,
                rag,
                runtime,
                CatalogEndpoint(catalog_url, KEY),
                orders=orders,
                policy=policy,
            )
        ]

    with (
        serve_retriever(OneSource(), Caller, key=KEY) as (url, _),
        catalog_testing.running(catalog) as catalog_url,
    ):
        return asyncio.run(collect(RagEndpoint(url, KEY), catalog_url))


def text_of(events: list[Any]) -> str:
    return "".join(e.text for e in events if isinstance(e, TextDelta))


def tool_results(request: dict[str, Any]) -> list[str]:
    texts = []
    for message in request["body"]["messages"]:
        if isinstance(message["content"], list):
            for block in message["content"]:
                if block.get("type") == "tool_result":
                    content = block["content"]
                    texts.append(
                        content if isinstance(content, str) else "".join(c["text"] for c in content)
                    )
    return texts


@pytest.fixture
def orders_url() -> Any:
    with orders_testing.running(orders_testing.server(key=KEY)) as url:
        yield url


@pytest.fixture
def policy_url() -> Any:
    with policy_testing.running(policy_testing.server(key=KEY)) as url:
        yield url


def _system(request: dict[str, Any]) -> str:
    system = request["body"]["system"]
    return system if isinstance(system, str) else "".join(part["text"] for part in system)


# ---- D3: anonymous turns are unchanged ---------------------------------------------------------


@pytest.mark.parametrize("region", sorted(MAIN_PROMPTS))
def test_the_anonymous_prompt_is_mains_byte_for_byte(region: Any) -> None:
    assert hashlib.sha256(system_prompt(region).encode()).hexdigest() == MAIN_PROMPTS[region]
    assert ORDERS_RULES not in system_prompt(region)


@pytest.mark.parametrize("region", sorted(MAIN_PROMPTS))
def test_the_signed_in_prompt_holds_no_order_data(region: Any) -> None:
    """Hard rule 2: an example in the prompt must not be an order the assistant could repeat as
    read (it was the demo shopper's own O000800, with its real status and date)."""
    prompt = system_prompt(region, signed_in=True)
    assert ORDERS_RULES in prompt
    assert not re.search(r"\bO\d{3,}", prompt, re.I)
    assert not re.search(r"\d", ORDERS_RULES)  # no order number, day, year or scan date
    assert not re.search(
        r"\b(january|february|march|april|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)\b",
        ORDERS_RULES,
        re.I,
    )
    assert "<order number> was delivered on <date>" in ORDERS_RULES


def test_an_anonymous_turn_sends_the_same_request_with_or_without_an_orders_endpoint(
    tmp_path: Path, orders_url: str, policy_url: str
) -> None:
    bodies: list[dict[str, Any]] = []
    for url, policy in ((None, None), (orders_url, policy_url)):
        with ScriptedLLM() as llm:
            llm.reply_stream("Orders ship within 2 business days [POL-001].")
            runtime = runtime_for(llm, tmp_path / str(len(bodies)))
            ask("When do orders ship?", ANON, runtime, url, policy)
        bodies.append(llm.requests[0]["body"])
    # As the replay proxy hashes a request: scrubbed of the CLI's per-run `metadata.user_id`.
    assert canonical(scrub(bodies[0])) == canonical(scrub(bodies[1]))
    names = {tool["name"] for tool in bodies[0]["tools"]}
    assert not names & {*order_tool.ALLOWED_TOOLS, *policy_tool.ALLOWED_TOOLS}
    assert ORDERS_RULES not in json.dumps(bodies[0])


def test_an_anonymous_caller_gets_no_orders_token() -> None:
    assert OrdersEndpoint("http://127.0.0.1:9/mcp", KEY).mint(ANON) is None
    assert PolicyEndpoint("http://127.0.0.1:9/mcp", KEY).mint(ANON) is None


# ---- a signed-in turn --------------------------------------------------------------------------


def test_a_signed_in_turn_offers_get_order_and_the_order_section(
    tmp_path: Path, orders_url: str, policy_url: str
) -> None:
    with ScriptedLLM() as llm:
        llm.reply_stream("Which order number is it?")
        ask("Where's my order?", SIGNED_IN, runtime_for(llm, tmp_path), orders_url, policy_url)
    request = llm.requests[0]
    names = sorted(tool["name"] for tool in request["body"]["tools"])
    assert names == sorted(
        {
            ALLOWED_TOOL,
            *catalog_tool.ALLOWED_TOOLS,
            GET_ORDER,
            "mcp__northwind-orders__request_return",
            "mcp__northwind-policy__evaluate_return_eligibility",
        }
    )
    system = _system(request)
    assert ORDERS_RULES in system and system.endswith("The shopper is in the US region.")
    assert "S0188" not in json.dumps(request["body"])  # the shopper ID never reaches the model


def test_each_server_gets_its_own_token_and_only_orders_and_policy_name_the_shopper(
    tmp_path: Path,
) -> None:
    rag_token = RagEndpoint("http://127.0.0.1:9/mcp", KEY).mint(SIGNED_IN)
    catalog_token = CatalogEndpoint("http://127.0.0.1:8/mcp", KEY).mint(SIGNED_IN)
    orders_token = OrdersEndpoint("http://127.0.0.1:7/mcp", KEY).mint(SIGNED_IN)
    policy_token = PolicyEndpoint("http://127.0.0.1:6/mcp", KEY).mint(SIGNED_IN)
    assert orders_token is not None and policy_token is not None
    options = assistant_options(
        SIGNED_IN,
        "http://127.0.0.1:9/mcp",
        rag_token,
        agent_runtime(base_url="http://127.0.0.1:1", root=tmp_path),
        ("http://127.0.0.1:8/mcp", catalog_token),
        ("http://127.0.0.1:7/mcp", orders_token),
        ("http://127.0.0.1:6/mcp", policy_token),
    )
    assert set(options.mcp_servers) == {RAG_SERVER, CATALOG_SERVER, ORDERS_SERVER, POLICY_SERVER}
    assert options.allowed_tools == [
        ALLOWED_TOOL,
        *catalog_tool.ALLOWED_TOOLS,
        GET_ORDER,
        "mcp__northwind-orders__request_return",
        "mcp__northwind-policy__evaluate_return_eligibility",
    ]
    assert verify_for(orders_token, key=KEY, server=ORDERS_SERVER).shp == "S0188"
    assert verify_for(policy_token, key=KEY, server=POLICY_SERVER).shp == "S0188"
    for token, server in ((rag_token, RAG_SERVER), (catalog_token, CATALOG_SERVER)):
        payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        assert "shp" not in payload
        assert verify_for(token, key=KEY, server=server).shp is None
    for token, others in (
        (orders_token, (RAG_SERVER, CATALOG_SERVER, POLICY_SERVER)),
        (policy_token, (RAG_SERVER, CATALOG_SERVER, ORDERS_SERVER)),
    ):
        for server in others:
            with pytest.raises(TokenError):
                verify_for(token, key=KEY, server=server)
    assert KEY.decode() not in json.dumps(options.mcp_servers)


def test_a_get_order_result_reaches_the_model_as_labelled_data(
    tmp_path: Path, orders_url: str
) -> None:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(GET_ORDER, {"order_id": "O000800"}, tool_id="toolu_o")
        llm.reply_stream("O000800 was delivered on 26 October.")
        events = ask("Where is O000800?", SIGNED_IN, runtime_for(llm, tmp_path), orders_url)
    [text] = tool_results(llm.requests[1])
    assert text.startswith('<order_data tool="get_order">\n') and text.endswith("\n</order_data>")
    labelled = order_tool.unlabel(text)
    assert labelled is not None and labelled[1]["status"] == "delivered"
    assert text_of(events) == "O000800 was delivered on 26 October."


def test_an_order_on_hold_is_never_called_confirmed(tmp_path: Path, orders_url: str) -> None:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(GET_ORDER, {"order_id": "O000418"}, tool_id="toolu_o")
        llm.reply_stream("O000418: your order has been confirmed.")
        events = ask("Is O000418 confirmed?", SIGNED_IN, runtime_for(llm, tmp_path), orders_url)
    labelled = order_tool.unlabel(tool_results(llm.requests[1])[0])
    assert labelled is not None and labelled[1]["status"] == "on_hold"
    shown = text_of(events)
    assert CONFIRMATION_SENTENCE in shown and "has been confirmed" not in shown


def test_another_shoppers_order_reaches_the_model_only_as_not_found(
    tmp_path: Path, orders_url: str
) -> None:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(GET_ORDER, {"order_id": "O000001"}, tool_id="toolu_o")  # S0103's
        llm.reply_stream("I couldn't find that order number on your account.")
        ask("Where is O000001?", SIGNED_IN, runtime_for(llm, tmp_path), orders_url)
    [text] = tool_results(llm.requests[1])
    body = json.loads(text)
    assert body["code"] == "not_found" and "S0103" not in text and "tok_" not in text


# The user's decision on N1-R, option (B): no order exemption in the output check. "Your order has
# been confirmed" stays blocked on a signed-in turn, even for an order `get_order` returned as
# delivered; the assistant gives the status in the result's own words with the order number.
@pytest.mark.parametrize(
    "claim",
    [
        "O000800: your order has been confirmed.",
        "O000800: your order was placed on 20 October.",
        "O000800: order confirmed, and it was delivered.",
    ],
)
def test_b_a_confirmed_or_placed_claim_stays_blocked_on_a_signed_in_turn(
    tmp_path: Path, orders_url: str, claim: str
) -> None:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(GET_ORDER, {"order_id": "O000800"}, tool_id="toolu_o")
        llm.reply_stream(claim)
        events = ask("Where is O000800?", SIGNED_IN, runtime_for(llm, tmp_path), orders_url)
    # The tool really returned the order as delivered: the claim is blocked for being a claim, not
    # because the lookup failed (scoped re-review of option B, L2).
    labelled = order_tool.unlabel(tool_results(llm.requests[1])[0])
    assert labelled is not None and labelled[1]["status"] == "delivered"
    shown = text_of(events)
    assert CONFIRMATION_SENTENCE in shown
    assert claim.split(":", 1)[1].strip() not in shown
