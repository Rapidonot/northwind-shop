"""Row #27 (CHK-06; decision 0023): mcp-orders attached to a signed-in turn only. No cassette, no
spend: the real CLI, a scripted model, a real mcp-rag, mcp-catalog and mcp-orders over HTTP.

- A signed-in turn offers `get_order` and carries the prompt's order section; an anonymous turn's
  request is byte for byte the one it was before row #27 (D3), even with an orders endpoint passed.
- The orders token carries the shopper (`shp`); no other token does, and no other server takes it.
- A `get_order` result reaches the model as labelled data, and its status vouches for "your order
  was placed" only for that order (the output check, decision 0015's row #27 amendment).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CatalogEndpoint
from northwind_assistant.orders_client import OrdersEndpoint
from northwind_assistant.output_check import CONFIRMATION_SENTENCE
from northwind_assistant.prompt import ORDERS_RULES, system_prompt
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.tools import ALLOWED_TOOL
from northwind_assistant.turn import TextDelta, TurnEvent, assistant_options, stream_answer
from northwind_common import catalog_tool, order_tool
from northwind_common.llm import AgentRuntime, agent_runtime
from northwind_common.mcp_token import (
    CATALOG_SERVER,
    ORDERS_SERVER,
    RAG_SERVER,
    TokenError,
    verify_for,
)
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_llm_replay.scrub import canonical, scrub
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_orders import testing as orders_testing
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("CHK-06")

SESSION = "d" * 32
ANON = Caller(region="US", session=SESSION)
SIGNED_IN = Caller(region="US", session=SESSION, shopper_id="S0031")
SOURCE = RetrievedChunk("c1", "document", "POL-001", None, "Orders ship within 2 business days.")
GET_ORDER = order_tool.GET_ORDER_TOOL

# sha256 of `system_prompt(region)` on origin/main f84f97d, before row #27 (D3's byte-identity).
MAIN_PROMPTS = {
    "US": "3d15477ff461e40ca8684869c28dcdb584efe4d7a73061dce0764a307e378e32",
    "CA": "bdd3f29a007c44135f8f031ae9e055eae0f627ceff40e846bc9e46f2bb414772",
    "UK": "0ef47204a52a093aca9a5b37c59b712a082195e19552893dd542670a260413f3",
    "EU": "7ef9cded818ffeeec285381086d970c0f073d925c13b480f04839b6323d4bb09",
}


class OneSource:
    def search(self, query: str, caller: Caller) -> list[RetrievedChunk]:
        return [SOURCE]


def runtime_for(llm: ScriptedLLM, root: Path) -> AgentRuntime:
    return agent_runtime(base_url=llm.url, root=root)


def ask(message: str, caller: Caller, runtime: AgentRuntime, orders_url: str | None) -> list[Any]:
    catalog = catalog_testing.server(key=KEY)

    async def collect(rag: RagEndpoint, catalog_url: str) -> list[TurnEvent]:
        orders = None if orders_url is None else OrdersEndpoint(orders_url, KEY)
        return [
            e
            async for e in stream_answer(
                message, caller, rag, runtime, CatalogEndpoint(catalog_url, KEY), orders
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


def _system(request: dict[str, Any]) -> str:
    system = request["body"]["system"]
    return system if isinstance(system, str) else "".join(part["text"] for part in system)


# ---- D3: anonymous turns are unchanged ---------------------------------------------------------


@pytest.mark.parametrize("region", sorted(MAIN_PROMPTS))
def test_the_anonymous_prompt_is_mains_byte_for_byte(region: Any) -> None:
    assert hashlib.sha256(system_prompt(region).encode()).hexdigest() == MAIN_PROMPTS[region]
    assert ORDERS_RULES not in system_prompt(region)


def test_an_anonymous_turn_sends_the_same_request_with_or_without_an_orders_endpoint(
    tmp_path: Path, orders_url: str
) -> None:
    bodies: list[dict[str, Any]] = []
    for url in (None, orders_url):
        with ScriptedLLM() as llm:
            llm.reply_stream("Orders ship within 2 business days [POL-001].")
            ask("When do orders ship?", ANON, runtime_for(llm, tmp_path / str(len(bodies))), url)
        bodies.append(llm.requests[0]["body"])
    # As the replay proxy hashes a request: scrubbed of the CLI's per-run `metadata.user_id`.
    assert canonical(scrub(bodies[0])) == canonical(scrub(bodies[1]))
    names = {tool["name"] for tool in bodies[0]["tools"]}
    assert GET_ORDER not in names and ORDERS_RULES not in json.dumps(bodies[0])


def test_an_anonymous_caller_gets_no_orders_token() -> None:
    assert OrdersEndpoint("http://127.0.0.1:9/mcp", KEY).mint(ANON) is None


# ---- a signed-in turn --------------------------------------------------------------------------


def test_a_signed_in_turn_offers_get_order_and_the_order_section(
    tmp_path: Path, orders_url: str
) -> None:
    with ScriptedLLM() as llm:
        llm.reply_stream("Which order number is it?")
        ask("Where's my order?", SIGNED_IN, runtime_for(llm, tmp_path), orders_url)
    request = llm.requests[0]
    names = sorted(tool["name"] for tool in request["body"]["tools"])
    assert names == sorted({ALLOWED_TOOL, *catalog_tool.ALLOWED_TOOLS, GET_ORDER})
    system = _system(request)
    assert ORDERS_RULES in system and system.endswith("The shopper is in the US region.")
    assert "S0031" not in json.dumps(request["body"])  # the shopper ID never reaches the model


def test_each_server_gets_its_own_token_and_only_the_orders_one_names_the_shopper(
    tmp_path: Path,
) -> None:
    rag_token = RagEndpoint("http://127.0.0.1:9/mcp", KEY).mint(SIGNED_IN)
    catalog_token = CatalogEndpoint("http://127.0.0.1:8/mcp", KEY).mint(SIGNED_IN)
    orders_token = OrdersEndpoint("http://127.0.0.1:7/mcp", KEY).mint(SIGNED_IN)
    assert orders_token is not None
    options = assistant_options(
        SIGNED_IN,
        "http://127.0.0.1:9/mcp",
        rag_token,
        agent_runtime(base_url="http://127.0.0.1:1", root=tmp_path),
        ("http://127.0.0.1:8/mcp", catalog_token),
        ("http://127.0.0.1:7/mcp", orders_token),
    )
    assert set(options.mcp_servers) == {RAG_SERVER, CATALOG_SERVER, ORDERS_SERVER}
    assert options.allowed_tools == [ALLOWED_TOOL, *catalog_tool.ALLOWED_TOOLS, GET_ORDER]
    assert verify_for(orders_token, key=KEY, server=ORDERS_SERVER).shp == "S0031"
    for token, server in ((rag_token, RAG_SERVER), (catalog_token, CATALOG_SERVER)):
        payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        assert "shp" not in payload
        assert verify_for(token, key=KEY, server=server).shp is None
    for server in (RAG_SERVER, CATALOG_SERVER):
        with pytest.raises(TokenError):
            verify_for(orders_token, key=KEY, server=server)
    assert KEY.decode() not in json.dumps(options.mcp_servers)


def test_a_get_order_result_reaches_the_model_as_labelled_data_and_vouches_its_order(
    tmp_path: Path, orders_url: str
) -> None:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(GET_ORDER, {"order_id": "O000064"}, tool_id="toolu_o")
        llm.reply_stream("O000064: your order was placed on 16 October and was delivered.")
        events = ask("Where is O000064?", SIGNED_IN, runtime_for(llm, tmp_path), orders_url)
    [text] = tool_results(llm.requests[1])
    assert text.startswith('<order_data tool="get_order">\n') and text.endswith("\n</order_data>")
    assert order_tool.order_status(text, False) == ("O000064", "delivered")
    shown = text_of(events)
    assert "your order was placed" in shown and CONFIRMATION_SENTENCE not in shown


def test_an_order_on_hold_is_never_called_confirmed(tmp_path: Path, orders_url: str) -> None:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(GET_ORDER, {"order_id": "O000113"}, tool_id="toolu_o")
        llm.reply_stream("O000113: your order has been confirmed.")
        events = ask("Is O000113 confirmed?", SIGNED_IN, runtime_for(llm, tmp_path), orders_url)
    assert order_tool.order_status(tool_results(llm.requests[1])[0], False) == (
        "O000113",
        "on_hold",
    )
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
