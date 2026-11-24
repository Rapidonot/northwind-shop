"""RAG-04 (row #11, decision 0022): query understanding on the first search. No cassette, no spend.

The real CLI and output check, a scripted model (the assistant's calls and Haiku's on separate
routes), a real mcp-rag with a spy retriever, and a real mcp-catalog over the fixtures for the live
filter. Pins: the rules path makes no model call; a narrowing plan reaches mcp-rag as a claim on a
token with the turn's own id; price and availability are a live product allow-list (hard rule 2);
card text never reaches Haiku (hard rule 1); the deadline and the cache apply in live mode only;
and an "under $50" answer states no threshold as fact.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CatalogEndpoint
from northwind_assistant.output_check import FIXED_SENTENCES
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.turn import TextDelta, TurnError, TurnEvent, stream_answer
from northwind_assistant.understand import MARKER, Understander
from northwind_assistant.usage import TELEMETRY_LOGGER
from northwind_common.llm import ROUTINE_MODEL, agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog, eligible_products
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.query import rules_plan
from northwind_rag.retrieval import RetrievedChunk, SearchFilter

pytestmark = pytest.mark.req("RAG-04")

US = Caller(region="US")
CARD = "4111 1111 1111 1111"


class Spy:
    """Records each search: plain (`search`) or planned (`search_planned`)."""

    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.plain: list[str] = []
        self.planned: list[list[tuple[str, SearchFilter]]] = []

    def search(self, query: str, caller: Caller) -> list[RetrievedChunk]:
        self.plain.append(query)
        return self.chunks

    def search_planned(self, parts: Any, caller: Caller) -> list[RetrievedChunk]:
        self.planned.append(list(parts))
        return self.chunks


def chunk(product_id: str) -> RetrievedChunk:
    return RetrievedChunk(
        f"product:{product_id}:0", "product", product_id, None, "A pair of pants."
    )


def haiku_reply(*parts: dict[str, Any]) -> str:
    return json.dumps({"parts": list(parts)})


def part(text: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "text": text,
        "colours": [],
        "sizes": [],
        "price_kind": "none",
        "amount": "",
        "amount_to": "",
        "currency": "none",
        "in_stock_only": False,
    }
    base.update(over)
    return base


def ask(
    llm: ScriptedLLM,
    spy: Spy,
    message: str,
    root: Path,
    understand: Understander | None = None,
) -> tuple[list[TurnEvent], Any]:
    runtime = agent_runtime(base_url=llm.url, root=root)
    understand = understand or Understander(live=False)

    async def collect(rag: RagEndpoint, catalog: CatalogEndpoint) -> list[TurnEvent]:
        return [e async for e in stream_answer(message, US, rag, runtime, catalog, understand)]

    catalog = catalog_testing.server(key=KEY)
    with (
        serve_retriever(spy, Caller) as (url, server),
        catalog_testing.running(catalog) as catalog_url,
    ):
        events = asyncio.run(collect(RagEndpoint(url, KEY), CatalogEndpoint(catalog_url, KEY)))
    return events, server


def haiku_requests(llm: ScriptedLLM) -> list[dict[str, Any]]:
    return [r for r in llm.requests if MARKER in json.dumps(r["body"].get("system", ""))]


def text_of(events: list[TurnEvent]) -> str:
    return "".join(e.text for e in events if isinstance(e, TextDelta))


def telemetry(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    [line] = [json.loads(r.getMessage()) for r in caplog.records if r.name == TELEMETRY_LOGGER]
    return dict(line)


def test_a_plain_message_makes_no_model_call_and_searches_as_before(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    message = "Which rain jackets have a helmet-compatible hood?"
    spy = Spy([chunk("P0011")])
    with ScriptedLLM() as llm, caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        llm.reply_stream("They have hoods [P0011].")
        ask(llm, spy, message, tmp_path)
    assert spy.plain == [message] and spy.planned == []
    assert haiku_requests(llm) == []
    line = telemetry(caplog)
    assert line["qu_path"] == "rules" and line["calls"] == 1


def test_a_price_filter_is_a_live_allow_list_on_the_planned_search(tmp_path: Path) -> None:
    message = "Pants under $50"
    spy = Spy([chunk("P0011")])
    with ScriptedLLM() as llm:
        llm.reply_stream("Here are some [P0011].")
        ask(llm, spy, message, tmp_path)
    expected = eligible_products(FixtureCatalog(), "US", currency="USD", price_max=5000)
    assert spy.plain == []
    [[(text, narrow)]] = spy.planned
    assert text == message  # the rules path searches the raw message
    assert narrow == SearchFilter(products=frozenset(expected))
    assert haiku_requests(llm) == []


def test_colour_and_size_narrow_through_the_claim(tmp_path: Path) -> None:
    message = "Queen sheet sets in white, please."
    spy = Spy([chunk("P0121")])
    with ScriptedLLM() as llm:
        llm.reply_stream("Sheets [P0121].")
        ask(llm, spy, message, tmp_path)
    [[(text, narrow)]] = spy.planned
    assert text == message
    assert narrow == SearchFilter(colours=("White",), sizes=("Queen",))


def test_the_planned_search_counts_against_the_turns_cap(tmp_path: Path) -> None:
    spy = Spy([chunk("P0121")])
    with ScriptedLLM() as llm:
        llm.reply_stream("Sheets [P0121].")
        _, server = ask(llm, spy, "Queen sheet sets in white, please.", tmp_path)
    assert len(server.ledger) == 1  # one turn id: the planned token kept the turn's own


def test_a_multi_part_message_goes_to_haiku_and_each_part_is_searched(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    message = "A black fleece in M and a queen duvet under $200"
    spy = Spy([chunk("P0011")])
    with ScriptedLLM() as llm, caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        llm.route(MARKER).reply(
            haiku_reply(
                part("black fleece", colours=["Black"], sizes=["M"]),
                part(
                    "queen duvet",
                    sizes=["Queen"],
                    price_kind="max",
                    amount="200",
                    currency="USD",
                ),
            )
        )
        llm.reply_stream("Two picks [P0011].")
        ask(llm, spy, message, tmp_path)
    [request] = haiku_requests(llm)
    body = request["body"]
    assert body["model"] == ROUTINE_MODEL
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in body
    assert f"<shopper_message>{message}</shopper_message>" in body["messages"][0]["content"]
    [[(t1, f1), (t2, f2)]] = spy.planned
    assert (t1, f1) == ("black fleece", SearchFilter(colours=("Black",), sizes=("M",)))
    expected = eligible_products(FixtureCatalog(), "US", currency="USD", price_max=20000)
    assert (t2, f2) == ("queen duvet", SearchFilter(sizes=("Queen",), products=frozenset(expected)))
    line = telemetry(caplog)
    assert line["qu_path"] == "model" and line["calls"] == 2  # Haiku's call is counted


def test_card_text_never_reaches_haiku(tmp_path: Path) -> None:
    message = f"A fleece and a duvet? Pay with {CARD}?"
    assert rules_plan(message, "US").needs_model
    spy = Spy([chunk("P0011")])
    with ScriptedLLM() as llm:
        events, _ = ask(llm, spy, message, tmp_path)
    assert llm.requests == []
    assert events == [TurnError("internal")]


def test_a_refused_haiku_reply_keeps_the_rules_plan(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    message = "A black fleece in M and a queen duvet under $200"
    spy = Spy([chunk("P0011")])
    hostile = {"parts": [part("x", visibility="internal")]}
    with ScriptedLLM() as llm, caplog.at_level(logging.INFO, logger=TELEMETRY_LOGGER):
        llm.route(MARKER).reply(json.dumps(hostile))
        llm.reply_stream("Picks [P0011].")
        ask(llm, spy, message, tmp_path)
    [[(text, narrow)]] = spy.planned
    rules = rules_plan(message, "US").parts[0].filters
    assert text == message and narrow.colours == rules.colour and narrow.sizes == rules.size
    assert telemetry(caplog)["qu_path"] == "invalid"


def test_the_model_is_not_told_the_filter_and_the_check_is_the_fallback(tmp_path: Path) -> None:
    """The user's item 4: for "under $50" the answer states no threshold and no price as fact unless
    it came from `get_price`. The filter only narrows the sources; nothing in the model's input
    names it. If the model states the threshold anyway, the output check's money class replaces
    it."""
    message = "Pants under $50"
    [eligible, *_] = eligible_products(FixtureCatalog(), "US", currency="USD", price_max=5000)
    spy = Spy([chunk(eligible)])
    clean = f"Here is a pair of pants that may suit you [{eligible}]."
    with ScriptedLLM() as llm:
        llm.reply_stream(clean)
        events, _ = ask(llm, spy, message, tmp_path)
    assert text_of(events) == clean
    [first] = llm.requests
    sent = json.dumps(first["body"]["messages"])
    without_message = sent.replace(message, "")
    for word in ("5000", "50.00", "eligible", "price_max", "in_stock", "filter"):
        assert word not in without_message
    with ScriptedLLM() as llm:
        llm.reply_stream(f"These pants are all under $50 [{eligible}].")
        events, _ = ask(llm, Spy([chunk(eligible)]), message, tmp_path / "2")
    shown = text_of(events)
    assert "$50" not in shown and FIXED_SENTENCES["money"] in shown


# ---- the deadline and the cache: live mode only --------------------------------------------------


class SlowClient:
    def __init__(self, delay: float, reply: str) -> None:
        self.delay, self.reply, self.calls = delay, reply, 0
        self.messages = self

    def create(self, **_: Any) -> Any:
        self.calls += 1
        time.sleep(self.delay)
        return SimpleNamespace(
            stop_reason="end_turn",
            model=ROUTINE_MODEL,
            usage=None,
            content=[SimpleNamespace(type="text", text=self.reply)],
        )


MULTI = "A black fleece in M and a queen duvet under $200"
REPLY = haiku_reply(part("black fleece", colours=["Black"]), part("queen duvet", sizes=["Queen"]))


def test_the_deadline_applies_in_live_mode_only() -> None:
    slow = SlowClient(0.3, REPLY)
    live = Understander(live=True, deadline=0.05, client_for=lambda _: slow)
    understood = asyncio.run(live.plan(MULTI, "US", "http://unused"))
    assert understood.path == "timeout" and understood.plan == rules_plan(MULTI, "US")
    recorded = Understander(live=False, deadline=0.05, client_for=lambda _: slow)
    understood = asyncio.run(recorded.plan(MULTI, "US", "http://unused"))
    assert understood.path == "model" and len(understood.plan.parts) == 2


def test_the_cache_is_live_mode_only() -> None:
    """Off in record and replay, so every run reads every cassette (the hit log's deletion rule)."""
    for live, calls in ((True, 1), (False, 3)):
        client = SlowClient(0, REPLY)
        understand = Understander(live=live, client_for=lambda _, c=client: c)
        paths = [asyncio.run(understand.plan(MULTI, "US", "http://unused")).path for _ in range(3)]
        assert client.calls == calls
        assert paths == (["model", "cache", "cache"] if live else ["model"] * 3)


def test_a_failing_client_keeps_the_rules_plan() -> None:
    class Broken:
        messages = SimpleNamespace(create=lambda **_: (_ for _ in ()).throw(RuntimeError("x")))

    understand = Understander(live=False, client_for=lambda _: Broken())
    understood = asyncio.run(understand.plan(MULTI, "US", "http://unused"))
    assert understood.path == "error" and understood.plan == rules_plan(MULTI, "US")
