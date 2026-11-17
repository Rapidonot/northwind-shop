"""The assistant's tool surface is frozen (CC-10 C6, C7'; the user's Q5: fixed now, and #12 and #16
add tools before RECORD-ALL, each changing this list and re-recording).

"Frozen" is the transport, the server name, the tool name, its description and its schema, as the
CLI sends them to the model. A change here changes every recorded request, so it is a reviewed
change with a re-record, never a drive-by.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.turn import assistant_options, stream_answer
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("SA-02")]

REPO = Path(__file__).resolve().parents[3]
# (name, sha256 of the description, sha256 of the canonical input schema), as sent.
GOLDEN = [
    (
        "mcp__northwind-rag__retrieve_context",
        "13fb0ff71833847d3e516c26c831160281a8eb974e5ae920e9a11a97244b8483",
        "417da8f2c6b94ea7f2a3fbb85d0e309c5378b35ce28ca011eb616ce3a0e7d16a",
    )
]
# Row #12 (RAG-09; the user's approval under stop 5, 2026-11-14): with mcp-catalog attached, as the
# gateway and the evals always run, the CLI sends these six, re-frozen from the W1 recording at this
# head. `GOLDEN` above stays the surface with no catalog. Row #16 (CHK-01; an exact-set update
# approved with its plan): the four cart tools join, captured from a scripted turn at this head;
# the five catalog entries are unchanged. The recordings match again once W1 and W2 re-record.
ATTACHED = [
    (
        "mcp__northwind-catalog__add_to_cart",
        "14c0340256c84bfc604500cf297f4285b4577b4e3ec0cca8ff58dafe0b1e2c35",
        "4d926dad33ac294e9192761c25ee14f70e432ca6d048aec4796ed8c1e7ce011e",
    ),
    (
        "mcp__northwind-catalog__check_stock",
        "3ca59182b19f7184392e7fd2cff24101e07c61d68ce31739fc3d2e5cf95de671",
        "4ed0912153ead4e8018c78cd2e31495c25f1774aae0e754ee54b883691bc3d58",
    ),
    (
        "mcp__northwind-catalog__get_cart",
        "575691aab0d302feb4302763a6bd30057b78234320b59d5054b0fd08e4b38755",
        "d746974fa9afd5e951f76f9af38954b0ad7f436f2120dc974da65e5ee39f856f",
    ),
    (
        "mcp__northwind-catalog__get_price",
        "83d66f5cff6811a6eed2ac75c50aaae10ca4d591b20663920becb0e0f9b5fd31",
        "4ed0912153ead4e8018c78cd2e31495c25f1774aae0e754ee54b883691bc3d58",
    ),
    (
        "mcp__northwind-catalog__get_product",
        "fa3abc1cc0eb9e7f4d1723d02cb96bbddb3d73f2eab2f5a55a667e7f6a965791",
        "69b3a2699535310a93dde26df21c8be2d59aba444de5f73b67caba5eeaf6bfe9",
    ),
    (
        "mcp__northwind-catalog__get_variant",
        "35f139456c6291f447d2c49e43a307eea73d369c1a704597caa715f88e768149",
        "4ed0912153ead4e8018c78cd2e31495c25f1774aae0e754ee54b883691bc3d58",
    ),
    (
        "mcp__northwind-catalog__remove_from_cart",
        "76c0def7b25178d2fd8a71299a68338c308d98ca8aaf03439417ab24d222c55a",
        "4ed0912153ead4e8018c78cd2e31495c25f1774aae0e754ee54b883691bc3d58",
    ),
    (
        "mcp__northwind-catalog__search_products",
        "11adb9122eefcc30023f1763c9ec5e52686aa3fdd1ae52d683274a1823e8746b",
        "c24ea675aba27bae5482a38385e8ca12fb2381fe4aadd6f5ad4a01bbe8018788",
    ),
    (
        "mcp__northwind-catalog__set_cart_quantity",
        "a75e6d93887dcf1732b4d2cdc0db77fb7c74f53f789edbd83dc31094d3c36dcb",
        "b39dcd5a26aed2b8f7e6e51f27cab3c44121e49b48ac16b5b79e3205d5b88138",
    ),
    *GOLDEN,
]
RECORDED_SUITES = ("assistant-smoke", "assistant-redteam")


class One:
    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        return [RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")]


def surface(tools: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    def digest(value: Any) -> str:
        text = (
            value
            if isinstance(value, str)
            else json.dumps(value, sort_keys=True, separators=(",", ":"))
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    return [(t["name"], digest(t["description"]), digest(t["input_schema"])) for t in tools]


def test_the_assistant_tool_surface_is_frozen(tmp_path: Path) -> None:
    with ScriptedLLM() as llm, serve_retriever(One(), Caller) as (url, _):
        llm.reply_stream("ok")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path)

        async def go() -> None:
            async for _ in stream_answer("hi", Caller(region="US"), RagEndpoint(url, KEY), runtime):
                pass

        asyncio.run(go())
    assert surface(llm.requests[0]["body"]["tools"]) == GOLDEN


def test_the_transport_and_server_are_frozen(tmp_path: Path) -> None:
    options = assistant_options(
        Caller(region="US"), "http://127.0.0.1:9/mcp", "t", agent_runtime(root=tmp_path)
    )
    assert options.mcp_servers == {
        "northwind-rag": {
            "type": "http",
            "url": "http://127.0.0.1:9/mcp",
            "headers": {"Authorization": "Bearer t"},
        }
    }
    assert options.allowed_tools == ["mcp__northwind-rag__retrieve_context"]
    assert options.tools == []


@pytest.mark.parametrize("suite", RECORDED_SUITES)
def test_every_recorded_request_offers_the_frozen_surface(suite: str) -> None:
    """The recordings are what replays: they must have been made on this surface (D)."""
    cassettes = sorted((REPO / "cassettes" / suite).glob("*.json"))
    assert cassettes, f"no cassettes in {suite}"
    for path in cassettes:
        raw = json.loads(path.read_text(encoding="utf-8"))["match"]["body"]
        body = json.loads(raw) if isinstance(raw, str) else raw
        assert surface(body["tools"]) == ATTACHED, path.name


@pytest.mark.req("RAG-09")
def test_the_attached_surface_is_frozen(tmp_path: Path) -> None:
    """What a live turn with the catalog attached sends today equals what the recordings hold."""
    from northwind_assistant.catalog_client import CatalogEndpoint
    from northwind_mcp_catalog import testing as catalog_testing

    with (
        ScriptedLLM() as llm,
        serve_retriever(One(), Caller) as (url, _),
        catalog_testing.running(catalog_testing.server(key=KEY)) as catalog_url,
    ):
        llm.reply_stream("ok")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path)
        catalog = CatalogEndpoint(catalog_url, KEY)

        async def go() -> None:
            caller = Caller(region="US")
            async for _ in stream_answer("hi", caller, RagEndpoint(url, KEY), runtime, catalog):
                pass

        asyncio.run(go())
    assert surface(llm.requests[0]["body"]["tools"]) == ATTACHED
