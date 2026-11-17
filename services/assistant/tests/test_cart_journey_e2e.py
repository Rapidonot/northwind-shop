"""The cart journey through the real assistant, index and catalog (row #16, CHK-01; W4).

`make record SUITE=assistant-cart` runs this through the user's runner window and creates the
cassettes it replays. As in `test_smoke_e2e`, the `e2e` marker keeps it out of `make test` and
`make test-integration`: it needs the real index.

Four messages, each its own turn (memory is SA-05), in one session against one live store. They are
the same messages the browser journey uses (`apps/portal/e2e/cart.json`). Every check reads the
server's own cart, not the model's words, except one: after the refused add, every catalog product
the answer names must be the requested one or one of the alternatives the server returned.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import psycopg
import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CatalogEndpoint
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.turn import Citations, TextDelta, TurnError, TurnEvent, stream_answer
from northwind_common.llm import agent_runtime
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.cart import alternatives
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_rag.testing import KEY, CallerIndex, serve_retriever
from northwind_rag.cli import DEFAULT_DATABASE_URL
from northwind_rag.embedding import CachedEmbedder, FastEmbedEmbedder

pytestmark = [pytest.mark.e2e, pytest.mark.req("CHK-01"), pytest.mark.llm_suite("assistant-cart")]

REPO = Path(__file__).resolve().parents[3]
JOURNEY = json.loads((REPO / "apps/portal/e2e/cart.json").read_text(encoding="utf-8"))
REGION = JOURNEY["region"]
SESSION = "0" * 31 + "c"  # fixed: it never reaches the model, so any value replays the same
IN_STOCK = "P0001-BLK-REG-M"
OUT_OF_STOCK = "P0008-CHR-REL-M"


def connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    return psycopg.connect(url, autocommit=True)


def lines(server: CatalogServer) -> dict[str, int]:
    with server.carts.open(SESSION, REGION) as cart:
        return dict(cart.lines)


def answer_of(events: list[TurnEvent]) -> str:
    return "".join(event.text for event in events if isinstance(event, TextDelta))


def test_add_refused_add_change_quantity_remove(llm_proxy: Any, tmp_path: Path) -> None:
    store = FixtureCatalog()
    store.set_stock(IN_STOCK, REGION, 5)
    store.set_stock(OUT_OF_STOCK, REGION, 0)
    server = catalog_testing.server(store, key=KEY)
    index = CallerIndex(connect, CachedEmbedder(FastEmbedEmbedder()))
    caller = Caller(region=REGION, session=SESSION)
    [add, refused, change, remove] = [step["message"] for step in JOURNEY["steps"]]

    with (
        serve_retriever(index, Caller) as (url, _),
        catalog_testing.running(server) as catalog_url,
    ):
        rag, catalog = RagEndpoint(url, KEY), CatalogEndpoint(catalog_url, KEY)

        def turn(message: str, n: int) -> list[TurnEvent]:
            runtime = agent_runtime(root=tmp_path / f"turn{n}")

            async def collect() -> list[TurnEvent]:
                return [e async for e in stream_answer(message, caller, rag, runtime, catalog)]

            events = asyncio.run(collect())
            assert not [e for e in events if isinstance(e, TurnError)], events
            assert isinstance(events[-1], Citations)
            return events

        turn(add, 1)
        assert lines(server) == {IN_STOCK: 1}

        refusal = answer_of(turn(refused, 2))
        assert lines(server) == {IN_STOCK: 1}  # refused: unchanged
        offered = {
            a["product_id"] for a in alternatives(server, _claims(), REGION, _variant(store), 1)
        }
        assert offered, "the server had alternatives to offer"
        named = {
            p.product_id for p in store.products() if p.visibility == "public" and p.name in refusal
        }
        assert named <= {"P0008", *offered}, (named, offered)

        turn(change, 3)
        assert lines(server) == {IN_STOCK: 2}

        turn(remove, 4)
        assert lines(server) == {}


def _claims() -> Any:
    from northwind_common.mcp_token import CATALOG_SERVER, Claims

    return Claims("shopper", REGION, "shopper-assistant", "turn", 2**31, CATALOG_SERVER, SESSION)


def _variant(store: FixtureCatalog) -> Any:
    variant = store.variant(OUT_OF_STOCK)
    assert variant is not None
    return variant
