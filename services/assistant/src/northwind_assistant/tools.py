"""Searching through mcp-rag, and reading what a search said (SA-02; CC-10 C1', C4').

`search_sources` is the assistant's one path to a search. The server calls it on the shopper's
message before the first model call (decision 0007); the model's refinement calls go from the CLI
to the same `retrieve_context` on mcp-rag, with the same per-turn token, so the checks, the failure
handling and RAG-10's cap are the server's for both. On the first search the card check runs here
first, so card text in a shopper's message never leaves this process; a query the model writes goes
from the CLI to mcp-rag, which refuses card data in any argument before searching (hard rule 1).

What a search found or how it failed is read the same way for both: `record` takes a result's text
and error flag. Source IDs come from the server-rendered `<source id="…">` lines, so citations stay
limited to what was returned this turn (hard rule 5), and an error whose code means the search
failed marks the turn, so an answer with nothing behind it is an outage, not an answer (hard rule
10). Audience and region are in the token, built from the server-side `Caller` (hard rule 7).
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from northwind_assistant.output_check import LiveValues, Source
from northwind_assistant.rag_client import SearchResult
from northwind_common import catalog_tool, order_tool
from northwind_common.cards import contains_card_data
from northwind_common.errors import ENVELOPE_KEYS
from northwind_common.rag_tool import (
    ALLOWED_TOOL,
    BAD_QUERY_MESSAGE,
    CARD_QUERY_MESSAGE,
    DESCRIPTION,
    FAILED_MESSAGE,
    FAILURE_CODES,
    INPUT_SCHEMA,
    LIMIT_MESSAGE,
    MAX_SEARCHES,
    NO_SOURCES,
    SERVER_NAME,
    TOOL_NAME,
    source_ids,
)

__all__ = [
    "ALLOWED_TOOL",
    "BAD_QUERY_MESSAGE",
    "CARD_QUERY_MESSAGE",
    "DESCRIPTION",
    "FAILED_MESSAGE",
    "INPUT_SCHEMA",
    "LIMIT_MESSAGE",
    "MAX_SEARCHES",
    "NO_SOURCES",
    "SERVER_NAME",
    "TOOL_NAME",
    "SearchFailure",
    "Searcher",
    "ToolState",
    "record",
    "record_live",
    "search_sources",
]

log = logging.getLogger(__name__)


class Searcher(Protocol):
    async def search(self, query: str) -> SearchResult: ...


@dataclass
class ToolState:
    """What one turn's searches did. `seen` holds the only source IDs that may be cited;
    `sources` their text, which the output check reads to tell policy from a review (NFR-03)."""

    seen: set[str] = field(default_factory=set)
    failed: bool = False
    searches: int = 0
    sources: list[Source] = field(default_factory=list)
    # Row #12 (RAG-09): live price and stock from this turn's catalog results, for the shopper's
    # `region` only; the output check reads them (`record_live`).
    region: str | None = None
    live: LiveValues = field(default_factory=LiveValues)


# mcp-rag's rendering (`northwind_mcp_rag.render`): an ID and type line, the escaped text, a close.
_SOURCE_BLOCK = re.compile(
    r'^<source id="([A-Za-z0-9._-]+)" type="([a-z]+)">\n(.*?)\n</source>$', re.MULTILINE | re.DOTALL
)


def parse_sources(rendered: str) -> list[Source]:
    return [
        Source(source_id, source_type, html.unescape(text))
        for source_id, source_type, text in _SOURCE_BLOCK.findall(rendered)
    ]


@dataclass(frozen=True)
class SearchFailure:
    category: str
    retryable: bool
    message: str


def _envelope(text: str) -> dict[str, Any] | None:
    try:
        body = json.loads(text)
    except ValueError:
        return None
    if isinstance(body, dict) and set(body) == ENVELOPE_KEYS and body.get("isError") is True:
        return body
    return None


def record(state: ToolState, *, is_error: bool, text: str) -> str | SearchFailure:
    """What one `retrieve_context` result means for the turn. A success's source IDs join `seen`;
    an error is a `SearchFailure`, and marks the turn failed when its code says the search failed,
    or when it is not our envelope at all (the CLI's own error text, a transport failure)."""
    if not is_error:
        state.seen.update(source_ids(text))
        state.sources.extend(parse_sources(text))
        return text
    body = _envelope(text)
    if body is None:
        state.failed = True
        return SearchFailure("transient", True, FAILED_MESSAGE)
    if body["code"] in FAILURE_CODES:
        state.failed = True
    return SearchFailure(
        str(body["errorCategory"]), bool(body["isRetryable"]), str(body["message"])
    )


_AMOUNT = re.compile(r"\d{1,7}\.\d{2}", re.ASCII)
_CURRENCY = re.compile(r"[A-Z]{3}", re.ASCII)


def record_live(state: ToolState, tool: str, *, is_error: bool, text: str) -> None:
    """A `get_price` or `check_stock` result joins `state.live` only if it is a success, exactly one
    `<catalog_data>` label for that tool, with well-formed values for the shopper's own region.
    Anything else is ignored, so the check stays closed (hard rule 2).

    Row #16 (CHK-01): a cart tool's result too, read by `_record_cart`. Its blocks are joined with
    `catalog_tool.BLOCK_SEPARATOR` by the caller, so a stock refusal's alternatives are read only
    behind our own refusal envelope (`catalog_tool.labelled_result`)."""
    if tool in catalog_tool.CART_TOOL_NAMES:
        _record_cart(state, tool, is_error=is_error, text=text)
        return
    if tool == order_tool.GET_ORDER_TOOL:
        # Row #27 (CHK-06): the order's status, from a well-formed success only.
        found = order_tool.order_status(text, is_error)
        if found is not None:
            state.live.orders[found[0]] = found[1]
        return
    if is_error or state.region is None:
        return
    labelled = catalog_tool.unlabel(text)
    if labelled is None:
        return
    name, body = labelled
    if not isinstance(body, dict) or body.get("region") != state.region:
        return
    variant = body.get("variant_id")
    if not isinstance(variant, str) or not variant:
        return
    if tool == catalog_tool.PRICE_TOOL and name == "get_price":
        amount, currency = body.get("amount"), body.get("currency")
        if (
            isinstance(amount, str)
            and _AMOUNT.fullmatch(amount)
            and isinstance(currency, str)
            and _CURRENCY.fullmatch(currency)
        ):
            state.live.prices.add((Decimal(amount), currency))
    elif tool == catalog_tool.STOCK_TOOL and name == "check_stock":
        on_hand, in_stock = body.get("on_hand"), body.get("in_stock")
        if (
            isinstance(on_hand, int)
            and not isinstance(on_hand, bool)
            and on_hand >= 0
            and in_stock is (on_hand > 0)
        ):
            state.live.stock[variant] = on_hand


def _amount(value: Any) -> Decimal | None:
    return Decimal(value) if isinstance(value, str) and _AMOUNT.fullmatch(value) else None


def _currency(value: Any) -> str | None:
    return value if isinstance(value, str) and _CURRENCY.fullmatch(value) else None


def _stock_entry(entry: Any) -> tuple[str, int] | None:
    """A variant and its on-hand count, if the entry states both consistently."""
    if not isinstance(entry, dict):
        return None
    variant, on_hand, in_stock = (
        entry.get("variant_id"),
        entry.get("on_hand"),
        entry.get("in_stock"),
    )
    if (
        isinstance(variant, str)
        and variant
        and isinstance(on_hand, int)
        and not isinstance(on_hand, bool)
        and on_hand >= 0
        and in_stock is (on_hand > 0)
    ):
        return variant, on_hand
    return None


def _record_cart(state: ToolState, tool: str, *, is_error: bool, text: str) -> None:
    """A cart result's live values (row #16), only for the shopper's own region and only when the
    whole result is well-formed, so the check stays closed (hard rule 2):

    - a cart (from any cart tool's success): each line's **unit price** only;
    - a stock refusal: the **requested variant's stock** only.

    Not recorded (security review F2): line totals and the subtotal, which depend on quantities the
    model chose, so they could vouch an invented price; and a cart line's stock, which would vouch a
    stock claim about a product the shopper never asked to check. A refusal's alternatives are
    checked for shape but their stock is not recorded either: the check vouches "out of stock" only
    when every variant read this turn is at zero (#12's D3), so recording them would block the one
    stock fact a refusal states. The alternatives are offered by name, not by stock (the prompt)."""
    if state.region is None:
        return
    labelled = catalog_tool.labelled_result(text, is_error)
    if labelled is None or tool != f"mcp__{catalog_tool.SERVER_NAME}__{labelled[0]}":
        return
    name, body = labelled
    if not isinstance(body, dict):
        return
    if is_error:  # a stock refusal: the requested variant's stock, nothing else
        if body.get("region") != state.region or not isinstance(body.get("alternatives"), list):
            return
        if any(_stock_entry(alt) is None for alt in body["alternatives"]):
            return
        found = _stock_entry(body.get("requested"))
        if found is not None:
            state.live.stock[found[0]] = found[1]
        return
    cart = body if name == "get_cart" else body.get("cart")
    if not isinstance(cart, dict) or cart.get("region") != state.region:
        return
    lines = cart.get("lines")
    if not isinstance(lines, list):
        return
    prices: set[tuple[Decimal, str]] = set()
    for line in lines:
        if not isinstance(line, dict) or _stock_entry(line) is None:
            return
        unit, total, currency = line.get("unit_price"), line.get("line_total"), line.get("currency")
        if unit is None and total is None and currency is None:
            continue  # not sold in the region: no price to vouch for
        amount, code = _amount(unit), _currency(currency)
        if amount is None or code is None or _amount(total) is None:
            return
        prices.add((amount, code))
    state.live.prices.update(prices)


async def search_sources(query: Any, searcher: Searcher, state: ToolState) -> str | SearchFailure:
    """Rendered sources, or `NO_SOURCES` for a search that found nothing, or a failure: an access
    failure and a valid empty result stay distinct (hard rule 10)."""
    if not isinstance(query, str) or not query.strip():
        return SearchFailure("validation", False, BAD_QUERY_MESSAGE)
    if "\x00" in query:  # Postgres text cannot hold NUL, so it would fail every time (RAG-03)
        return SearchFailure("validation", False, BAD_QUERY_MESSAGE)
    if contains_card_data(query):  # hard rule 1: the shopper's card text never leaves here
        return SearchFailure("validation", False, CARD_QUERY_MESSAGE)
    if state.searches >= MAX_SEARCHES:  # the server holds the cap too; this one never sends
        return SearchFailure("business", False, LIMIT_MESSAGE)
    state.searches += 1
    try:
        result = await searcher.search(query)
    except Exception as error:  # noqa: BLE001 - its text must not reach the model or a log
        log.error("search failed: %s", type(error).__name__)
        state.failed = True
        return SearchFailure("transient", True, FAILED_MESSAGE)
    return record(state, is_error=result.is_error, text=result.text)
