"""Row #12 (RAG-09; decision 0015 §7, #12 amendment): money and stock through the output check.

A price or stock claim reaches the shopper only as a catalog tool returned it this turn. Any other
figure gets the class's fixed sentence: one from a source, from memory, or from the shopper's own
message (the user's D5). #13's tests are unchanged; these add the #12 classes and exemptions.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CatalogEndpoint
from northwind_assistant.output_check import (
    FIXED_SENTENCES,
    LiveValues,
    OutputCheck,
    check_text,
    hits,
    live_backed,
    money_value,
)
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.tools import ToolState, record_live
from northwind_assistant.turn import TextDelta, TurnEvent, stream_answer
from northwind_common import catalog_tool
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("RAG-09")

MONEY = FIXED_SENTENCES["money"]
STOCK = FIXED_SENTENCES["stock"]
PROMO = FIXED_SENTENCES["promotion"]
USD279 = LiveValues(prices={(Decimal("279.00"), "USD")})
THREE = LiveValues(stock={"P0011-SAG-REG-M": 3})


# ---- money ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "It costs $279.",
        "It costs $279.00.",
        "It costs $ 279.",
        "It costs 279 USD.",
        "It costs 279 dollars.",
        "It costs two hundred seventy-nine dollars.",
        "It is £199.",
        "It is €89.50.",
        "It is 1,299 euros.",
        "That is a hundred dollars.",  # #15 L5: number words
        "Twenty quid.",
        "Jackets under $100 are listed.",  # D5: the shopper's own figure
    ],
)
def test_any_money_figure_without_a_live_price_is_replaced(text: str) -> None:
    assert check_text(text) == MONEY
    assert hits(text) == {"money": 1}


@pytest.mark.parametrize(
    "text",
    [
        "It costs $279.",
        "It costs $279.00.",
        "It costs US$279.",
        "It costs 279 USD.",
        "It costs 279 dollars.",
        "It costs two hundred seventy-nine dollars.",
    ],
)
def test_a_figure_equal_to_a_live_price_passes(text: str) -> None:
    assert check_text(text, live=USD279) == text
    assert hits(text, live=USD279) == {}


@pytest.mark.parametrize(
    "text",
    [
        "It costs $278.",  # another amount
        "It costs $279.01.",
        "It costs £279.",  # another currency
        "It costs 279 euros.",
        "It costs C$279.",  # narrowed to CAD
        "It costs A$279.",
        "Jackets under $100 are listed.",  # D5: not a live value, whatever else was checked
    ],
)
def test_a_figure_that_differs_from_every_live_price_is_replaced(text: str) -> None:
    assert check_text(text, live=USD279) == MONEY


def test_dollar_alone_means_any_dollar_and_a_prefix_narrows_it() -> None:
    cad = LiveValues(prices={(Decimal("279.00"), "CAD")})
    assert check_text("It costs $279.", live=cad) == "It costs $279."
    assert check_text("It costs C$279.", live=cad) == "It costs C$279."
    assert check_text("It costs US$279.", live=cad) == MONEY
    assert money_value("$279", "") == (Decimal("279.00"), frozenset({"USD", "CAD", "AUD"}))
    assert money_value("$279", "ca") == (Decimal("279.00"), frozenset({"CAD"}))


def test_pounds_as_weight_is_not_money() -> None:
    text = "It weighs 2 pounds [P0011]."
    assert hits(text, seen=["P0011"]) == {}


def test_a_money_figure_split_across_deltas_is_never_sent() -> None:
    check = OutputCheck(set(), [])
    sent: list[str] = []
    for piece in ("It costs ", "$2", "7", "9", " today", "."):
        sent += [p for p in check.feed(piece) if p is not None]
    sent += [p for p in check.finish() if p is not None]
    assert "279" not in "".join(sent)
    assert check.shown == MONEY


# ---- stock ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "There are three left.",  # #15 L5
        "There are 3 left.",
        "Only 3 left!",
        "There are 3 units available.",
        "It has 3 in stock.",
        "There are three remaining.",
    ],
)
def test_a_stock_count_passes_only_when_it_equals_a_live_count(text: str) -> None:
    assert check_text(text) == STOCK
    assert check_text(text, live=THREE) == text
    assert check_text(text, live=LiveValues(stock={"P0011-SAG-REG-M": 4})) == STOCK


def test_in_stock_passes_only_when_every_checked_variant_is_in_stock() -> None:
    text = "It is in stock."
    assert check_text(text) == STOCK
    assert check_text(text, live=LiveValues(stock={"a": 2, "b": 5})) == text
    assert check_text(text, live=LiveValues(stock={"a": 2, "b": 0})) == STOCK  # D3


def test_out_of_stock_passes_only_when_every_checked_variant_is_at_zero() -> None:
    for text in ("It is out of stock.", "It is sold out."):
        assert check_text(text) == STOCK
        assert check_text(text, live=LiveValues(stock={"a": 0})) == text
        assert check_text(text, live=LiveValues(stock={"a": 0, "b": 1})) == STOCK
        assert check_text(text, live=THREE) == STOCK


@pytest.mark.parametrize("text", ["Stock is low.", "It is low stock.", "Stock is running low."])
def test_low_stock_never_passes(text: str) -> None:
    assert check_text(text, live=LiveValues(stock={"a": 1})) == STOCK


@pytest.mark.parametrize(
    "text",
    [
        # #15 L9
        "The option you described is available in the sources.",
        # the user's three, row #12: an asking word that does not govern the clause
        "If you need it soon, the Sage M is available now.",
        "Not sure if you saw, but the M is available.",
        "The Glacier Blue 4XL, which is available today, is warm.",
        # more of the same
        "Check if you like it and the M is available.",
        "The size which is available is M.",
        "If you like it; the M is available.",
        "It is currently available.",
        "It is available now.",
        "The Sage M is still available.",
    ],
)
def test_an_availability_claim_is_stock_and_needs_a_live_check(text: str) -> None:
    assert check_text(text) == STOCK
    assert check_text(text, live=THREE) == text


@pytest.mark.parametrize(
    "text",
    [
        # the five false positives measured on the recorded answers (plan §5.1)
        "Check the product page to see whether that size is available right now.",
        "The live product page will show whether Glacier Blue is available in 4XL right now.",
        "I can't see stock levels, so check the live product page to see whether the M is "
        "available now.",
        "The live product page shows which size and colour combinations are available, and it "
        "also has current stock.",
        "I couldn't find anything on compensation for late deliveries, or on what care agents are "
        "told to offer, so I can't say whether any is available.",
        # the range offered, not stock
        "It is available in sizes XS to XL.",
        "They are available in colours Sage and Black.",
    ],
)
def test_asking_about_availability_or_naming_the_range_is_not_a_claim(text: str) -> None:
    assert hits(text) == {}


# ---- member-price wordings (#13b R4) ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The members-only rate applies.",
        "There is a members only price.",
        "Ask about the member rate.",
        "There is a member deal.",
        "Loyalty points apply here.",
        "A loyalty discount is offered.",
    ],
)
def test_more_member_price_wordings_are_promotions(text: str) -> None:
    assert check_text(text) == PROMO
    assert check_text(text, live=USD279) == PROMO  # no live value vouches for a promotion


# ---- one fixed sentence, once (EV-090) ---------------------------------------------------------


def test_a_replacement_that_repeats_the_sentence_before_it_is_dropped() -> None:
    text = f"{PROMO} Use the discount code at checkout. Anything else?"
    check = OutputCheck(set(), [])
    check.feed(text)
    check.finish()
    assert check.shown.count(PROMO) == 1
    assert check.shown.startswith(PROMO) and check.shown.rstrip().endswith("Anything else?")
    assert check.counts["promotion"] == 1  # still counted


def test_two_blocked_sentences_in_a_row_show_one_fixed_sentence() -> None:
    text = "Use the discount code. A coupon works too. Anything else?"
    shown = check_text(text)
    assert shown.count(PROMO) == 1
    assert hits(text) == {"promotion": 2}


def test_different_fixed_sentences_are_not_collapsed() -> None:
    shown = check_text("Use the discount code. It costs $5. Fine.")
    assert PROMO in shown and MONEY in shown


# ---- live_backed: what the evals count as claims backed only by live values --------------------


def test_live_backed_counts_sentences_with_a_live_backed_claim() -> None:
    live = LiveValues(prices={(Decimal("279.00"), "USD")}, stock={"v": 3})
    assert live_backed("It costs $279. There are 3 left in stock. Wash cold.", live) == 2
    assert live_backed("It costs $279 and there are 3 left.", live) == 1
    assert live_backed("It costs $278.", live) == 0
    assert live_backed("Check whether the M is available.", LiveValues()) == 0  # a question
    assert live_backed("It costs $279.", LiveValues()) == 0


# ---- record_live: only well-formed results for the shopper's own region ------------------------


def _state() -> ToolState:
    return ToolState(region="US")


def _price(**override: object) -> str:
    body: dict[str, object] = {
        "variant_id": "P0011-SAG-REG-M",
        "region": "US",
        "amount": "279.00",
        "currency": "USD",
    }
    body.update(override)
    return catalog_tool.label("get_price", body)


def _stock(**override: object) -> str:
    body: dict[str, object] = {
        "variant_id": "P0011-SAG-REG-M",
        "region": "US",
        "on_hand": 3,
        "in_stock": True,
    }
    body.update(override)
    return catalog_tool.label("check_stock", body)


def test_a_live_price_and_stock_join_the_state() -> None:
    state = _state()
    record_live(state, catalog_tool.PRICE_TOOL, is_error=False, text=_price())
    record_live(state, catalog_tool.STOCK_TOOL, is_error=False, text=_stock())
    assert state.live.prices == {(Decimal("279.00"), "USD")}
    assert state.live.stock == {"P0011-SAG-REG-M": 3}


@pytest.mark.parametrize(
    ("tool", "text", "is_error"),
    [
        (catalog_tool.PRICE_TOOL, _price(), True),  # an error result
        (catalog_tool.PRICE_TOOL, _price(region="UK"), False),  # another region
        (catalog_tool.PRICE_TOOL, _price(amount="279"), False),
        (catalog_tool.PRICE_TOOL, _price(amount=279), False),
        (catalog_tool.PRICE_TOOL, _price(currency="usd"), False),
        (catalog_tool.PRICE_TOOL, _stock(), False),  # a stock label on the price call
        (catalog_tool.STOCK_TOOL, _price(), False),
        (catalog_tool.STOCK_TOOL, _stock(on_hand=True), False),
        (catalog_tool.STOCK_TOOL, _stock(on_hand=-1), False),
        (catalog_tool.STOCK_TOOL, _stock(on_hand=0, in_stock=True), False),
        (catalog_tool.PRICE_TOOL, '{"amount":"279.00","currency":"USD","region":"US"}', False),
        (catalog_tool.PRICE_TOOL, "It costs $279.", False),
        (f"mcp__{catalog_tool.SERVER_NAME}__get_product", _price(), False),
    ],
)
def test_anything_else_is_ignored(tool: str, text: str, is_error: bool) -> None:
    state = _state()
    record_live(state, tool, is_error=is_error, text=text)
    assert state.live == LiveValues()


def test_a_state_with_no_region_records_nothing() -> None:
    state = ToolState()
    record_live(state, catalog_tool.PRICE_TOOL, is_error=False, text=_price())
    assert state.live == LiveValues()


# ---- through the real CLI: a price changed between turns, old value blocked --------------------

US = Caller(region="US")
VARIANT = "P0011-SAG-REG-M"
SOURCE = RetrievedChunk("c1", "product", "P0011", None, "Brindlemoor Cairn Storm Jacket.")


class OneSource:
    def search(self, query: str, caller: Caller) -> list[RetrievedChunk]:
        return [SOURCE]


def _turn(answer: str, catalog_url: str, root: Path) -> str:
    with ScriptedLLM() as llm:
        llm.reply_tool_use(catalog_tool.PRICE_TOOL, {"variant_id": VARIANT}, tool_id="toolu_p")
        llm.reply_tool_use(catalog_tool.STOCK_TOOL, {"variant_id": VARIANT}, tool_id="toolu_s")
        llm.reply_stream(answer)
        runtime = agent_runtime(base_url=llm.url, root=root)

        async def collect(rag: RagEndpoint) -> list[TurnEvent]:
            catalog = CatalogEndpoint(catalog_url, KEY)
            return [e async for e in stream_answer("How much?", US, rag, runtime, catalog)]

        with serve_retriever(OneSource(), Caller) as (url, _):
            events = asyncio.run(collect(RagEndpoint(url, KEY)))
    return "".join(e.text for e in events if isinstance(e, TextDelta))


def test_after_a_price_and_stock_change_only_the_new_values_pass(tmp_path: Path) -> None:
    live = FixtureCatalog()
    live.set_price(VARIANT, "US", "250.00", "USD")
    live.set_stock(VARIANT, "US", 4)
    with catalog_testing.running(catalog_testing.server(live, key=KEY)) as url:
        first = _turn("It costs $250.00 and there are 4 left [P0011].", url, tmp_path / "a")
        live.set_price(VARIANT, "US", "199.00", "USD")
        live.set_stock(VARIANT, "US", 0)
        stale = _turn("It costs $250.00 and there are 4 left [P0011].", url, tmp_path / "b")
        fresh = _turn("It costs $199.00, and it is out of stock [P0011].", url, tmp_path / "c")
    assert first == "It costs $250.00 and there are 4 left [P0011]."
    assert "250" not in stale and stale.startswith(MONEY)
    assert fresh == "It costs $199.00, and it is out of stock [P0011]."


# ---- W1 finding: text from two model calls is joined with a paragraph break ---------------------


def test_a_preamble_before_a_tool_call_is_not_glued_to_the_answer(tmp_path: Path) -> None:
    """W1 (2026-11-14): the model wrote "…I'll check the price for each size." before its
    `get_price` calls and "The … costs $263.40 USD." after them. Joined with no space, "size.The"
    read as a bare domain and the link sentence replaced the live price. The calls' text is now
    joined with a paragraph break, so each sentence is checked on its own."""
    from northwind_llm_replay.scripted import blocks_sse

    live = FixtureCatalog()
    live.set_price(VARIANT, "US", "263.40", "USD")
    preamble = [
        {"type": "text", "chunks": ["It comes in five sizes [P0011].", " I'll check the price."]},
        {
            "type": "tool_use",
            "id": "toolu_p",
            "name": catalog_tool.PRICE_TOOL,
            "input": {"variant_id": VARIANT},
        },
    ]
    usage = {"input_tokens": 10, "output_tokens": 5}
    with (
        catalog_testing.running(catalog_testing.server(live, key=KEY)) as url,
        ScriptedLLM() as llm,
    ):
        llm.reply_raw(
            200, blocks_sse("scripted", preamble, "tool_use", usage, "msg_pre"), "text/event-stream"
        )
        llm.reply_stream("The jacket costs $263.40 USD [P0011].")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path)

        async def collect(rag: RagEndpoint) -> list[TurnEvent]:
            catalog = CatalogEndpoint(url, KEY)
            return [e async for e in stream_answer("How much?", US, rag, runtime, catalog)]

        with serve_retriever(OneSource(), Caller) as (rag_url, _):
            events = asyncio.run(collect(RagEndpoint(rag_url, KEY)))
    shown = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert shown == (
        "It comes in five sizes [P0011]. I'll check the price."
        "\n\nThe jacket costs $263.40 USD [P0011]."
    )
    assert FIXED_SENTENCES["link"] not in shown and FIXED_SENTENCES["money"] not in shown


def _streamed(pieces: list[str], live: LiveValues) -> tuple[str, str]:
    check = OutputCheck(set(), [], live=live)
    sent: list[str] = []
    for piece in pieces:
        sent += [p for p in check.feed(piece) if p is not None]
    sent += [p for p in check.finish() if p is not None]
    return "".join(sent), check.shown


def test_an_amount_split_at_its_decimal_point_waits_for_the_decimals() -> None:
    """W1 (2026-11-14): the recorded deltas split "$263.40 USD" as "$263." and "40 USD.". The
    amount pattern ends on a digit, so "$263" read as complete and was blocked against the live
    263.40. A money match followed only by "." or "," at the buffer end is now held."""
    live = LiveValues(prices={(Decimal("263.40"), "USD")})
    sent, shown = _streamed(["It costs $263.", "40 USD. Fine."], live)
    assert shown == "It costs $263.40 USD. Fine." and MONEY not in sent
    sent, shown = _streamed(["It costs $263.", "40 USD. Fine."], LiveValues())
    assert "263" not in sent and shown.startswith(MONEY)


def test_an_amount_that_ends_its_sentence_is_still_read_when_the_next_text_arrives() -> None:
    live = LiveValues(prices={(Decimal("263.00"), "USD")})
    _sent, shown = _streamed(["It costs $263.", " Fine."], live)
    assert shown == "It costs $263. Fine."
    _sent, shown = _streamed(["It costs $264.", " Fine."], live)
    assert shown.startswith(MONEY)
