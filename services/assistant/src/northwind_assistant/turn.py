"""One shopper message, streamed (SA-02).

Each message stands alone: conversation memory is SA-05. The server searches on the shopper's
message first, so the first model call can already answer (decision 0007). Every piece of text the
model writes goes through the output check before it leaves (`output_check`, NFR-03, decision
0015): `stream_answer` is the only caller of `run_turn`, which checks nothing (a test pins it).
Citations are the `[ID]` markers in the text the shopper is left seeing that match a source
returned this turn, so an invented ID never becomes a citation (hard rule 5). Failures surface only
as fixed codes: no exception or upstream text goes further.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from claude_agent_sdk import AssistantMessage, ClaudeSDKError, ResultMessage, UserMessage, query
from claude_agent_sdk.types import StreamEvent, ToolResultBlock, ToolUseBlock

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CatalogEndpoint
from northwind_assistant.orders_client import OrdersEndpoint, PolicyEndpoint
from northwind_assistant.output_check import OutputCheck, citation_parts
from northwind_assistant.prompt import system_prompt, user_prompt
from northwind_assistant.rag_client import McpRagSearcher, RagEndpoint
from northwind_assistant.source_cards import SourceCard, source_cards
from northwind_assistant.tools import (
    ALLOWED_TOOL,
    SERVER_NAME,
    SearchFailure,
    ToolState,
    record,
    record_live,
    search_sources,
)
from northwind_assistant.understand import (
    LiveFilter,
    Understander,
    Understood,
    mint_planned,
    plan_claim,
    query_ok,
)
from northwind_assistant.usage import TurnUsage
from northwind_common import catalog_tool, order_tool, policy_tool
from northwind_common.llm import ASSISTANT_MODEL, AgentRuntime, agent_options
from northwind_common.mcp_token import bearer

log = logging.getLogger(__name__)

# Model calls per turn. Before row #12 this was 4: two refinement searches, one refused search and
# the answer. A live price answer adds catalog rounds (`get_product`, then `get_price` and
# `check_stock` together), so row #12 raised it to 6 (the user's D2). mcp-rag still holds RAG-10's
# cap of 3 searches per turn; this cap is also the interim bound on catalog calls until hardening
# adds a per-turn catalog cap.
MAX_TURNS = 6
# Text from the next model call after a finished one (a tool call between them) starts a new
# paragraph. Glued on, "…for each size." and "The jacket…" read as one word, "size.The", which the
# output check takes for a bare domain (row #12, W1).
CALL_SEPARATOR = "\n\n"
# Anything else, such as max_tokens or a refusal, is not a finished answer.
FINISHED_STOPS = frozenset({"end_turn", "tool_use"})

# Reaching the turn limit is not an error: the shopper gets this instead (RAG-10). It makes no
# product claim, so it needs no source (hard rule 5).
INSUFFICIENT_EVIDENCE = (
    "I couldn't find enough in Northwind's product information to answer that. Could you tell me "
    "a bit more about what you're looking for?"
)

ErrorCode = Literal["retrieval_failed", "model_unavailable", "replay_miss", "internal"]

_MARKER = re.compile(r"\[([^\[\]]+)\]")


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class Reset:
    """A model attempt was cut off and is being retried: drop the text streamed so far."""


@dataclass(frozen=True)
class Citations:
    """`sources`: a card per cited source for the chat panel (row #17, SA-02). It is not part of
    equality: which sources are cited is the citation; the cards only describe them."""

    source_ids: tuple[str, ...]
    sources: tuple[SourceCard, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class TurnError:
    code: ErrorCode


TurnEvent = TextDelta | Reset | Citations | TurnError


def cited_ids(answer: str, seen: set[str]) -> tuple[str, ...]:
    """Source IDs cited in `answer` that were really returned, first mention first."""
    marked = [part for group in _MARKER.findall(answer) for part in citation_parts(group)]
    cited = tuple(dict.fromkeys(source_id for source_id in marked if source_id in seen))
    invented = len(set(marked) - seen)
    if invented:
        log.warning("dropped %d citation(s) to sources not returned this turn", invented)
    return cited


def assistant_options(
    caller: Caller,
    url: str,
    token: str,
    runtime: AgentRuntime,
    catalog: tuple[str, str] | None = None,
    orders: tuple[str, str] | None = None,
    policy: tuple[str, str] | None = None,
) -> Any:
    """mcp-rag over HTTP with this turn's token, `retrieve_context` its one tool; and, given
    `catalog` (URL, token), mcp-catalog with its five read-only tools (row #12, RAG-09; PRD §7,
    NFR-01) and, from row #16 (CHK-01), its four cart tools, which act on the session in the
    token. Each server gets its own token, which the other refuses. The CLI gets the tokens, never
    the key.

    Row #27 (CHK-06): given `orders` (URL, token), a signed-in turn also gets mcp-orders with
    `get_order`, and the prompt's order section. Without it, the options are the anonymous turn's,
    unchanged. PR 2 (CHK-07, decision 0024): mcp-orders also has `request_return`, and `policy`
    (URL, token) adds mcp-policy with its one read-only tool, only ever beside `orders`."""
    if policy is not None and orders is None:
        raise ValueError("a policy server needs a signed-in turn's order server")
    servers: dict[str, Any] = {SERVER_NAME: {"type": "http", "url": url, "headers": bearer(token)}}
    allowed = [ALLOWED_TOOL]
    if catalog is not None:
        catalog_url, catalog_token = catalog
        servers[catalog_tool.SERVER_NAME] = {
            "type": "http",
            "url": catalog_url,
            "headers": bearer(catalog_token),
        }
        allowed.extend(catalog_tool.ALLOWED_TOOLS)
    if orders is not None:
        orders_url, orders_token = orders
        servers[order_tool.SERVER_NAME] = {
            "type": "http",
            "url": orders_url,
            "headers": bearer(orders_token),
        }
        allowed.extend(order_tool.ALLOWED_TOOLS)
    if policy is not None:
        policy_url, policy_token = policy
        servers[policy_tool.SERVER_NAME] = {
            "type": "http",
            "url": policy_url,
            "headers": bearer(policy_token),
        }
        allowed.extend(policy_tool.ALLOWED_TOOLS)
    return agent_options(
        system_prompt=system_prompt(caller.region, signed_in=orders is not None),
        model=ASSISTANT_MODEL,
        tools=[],
        runtime=runtime,
        mcp_servers=servers,
        allowed_tools=allowed,
        permission_mode="dontAsk",
        include_partial_messages=True,
        max_turns=MAX_TURNS,
    )


def _text_delta(message: StreamEvent) -> str | None:
    event = message.event
    if event.get("type") != "content_block_delta":
        return None
    delta = event.get("delta", {})
    return str(delta["text"]) if delta.get("type") == "text_delta" else None


def _result_text(block: ToolResultBlock) -> str:
    content = block.content
    if isinstance(content, str):
        return content
    return "".join(str(part.get("text", "")) for part in content or [] if isinstance(part, dict))


# Row #16: the cart tools' results carry live prices and stock too (`record_live`).
_LIVE_TOOLS = frozenset(
    {catalog_tool.PRICE_TOOL, catalog_tool.STOCK_TOOL, *catalog_tool.CART_TOOL_NAMES}
)


def _blocks_text(block: ToolResultBlock) -> str:
    """A live tool's result, its text blocks joined as `catalog_tool.labelled_result` reads them:
    one block is unchanged; a cart refusal's envelope and label stay apart (row #16)."""
    content = block.content
    if isinstance(content, str):
        return content
    return catalog_tool.BLOCK_SEPARATOR.join(
        str(part.get("text", "")) for part in content or [] if isinstance(part, dict)
    )


def _note_tool_results(
    item: UserMessage, ours: set[str], state: ToolState, live: dict[str, str] | None = None
) -> None:
    """The model's `retrieve_context` results, read as the first search's is (C4'); and its
    `get_price` and `check_stock` results, whose values join `state.live` (row #12)."""
    if not isinstance(item.content, list):
        return
    for block in item.content:
        if not isinstance(block, ToolResultBlock):
            continue
        if block.tool_use_id in ours:
            record(state, is_error=bool(block.is_error), text=_result_text(block))
        elif live and block.tool_use_id in live:
            tool = live[block.tool_use_id]
            record_live(state, tool, is_error=bool(block.is_error), text=_blocks_text(block))


def _result_error(result: ResultMessage) -> ErrorCode | None:
    """An upstream failure arrives as `is_error` with subtype `success`, so test `is_error`. The
    result text is only matched here, never passed on."""
    if not result.is_error:
        return None
    if "cassette miss" in (result.result or ""):
        return "replay_miss"
    return "model_unavailable"


async def run_turn(
    prompt: str, options: Any, state: ToolState, usage: TurnUsage | None = None
) -> AsyncIterator[TurnEvent]:
    """The model's own events, unchecked: only `stream_answer` may call this."""
    parts: list[str] = []
    code: ErrorCode | None = None
    finished = False
    capped = False
    usage = usage if usage is not None else TurnUsage()
    # A cut stream still ends in `message_stop` and a `success` result, so a stop reason in
    # `message_delta` is the only sign that an attempt reached its end.
    complete = True
    separate = False  # the next text starts a new call after text that did not end in whitespace
    ours: set[str] = set()  # tool_use ids of this turn's `retrieve_context` calls
    live: dict[str, str] = {}  # tool_use id -> name, for this turn's price and stock calls
    try:
        # Runs to the end even after an error: leaving early strands the SDK's generator.
        async for item in query(prompt=prompt, options=options):
            if isinstance(item, StreamEvent):
                kind = item.event.get("type")
                if kind == "message_start":
                    usage.call_started(item.event.get("message") or {})
                    if not complete and parts:
                        parts.clear()
                        separate = False
                        yield Reset()
                    elif parts and not parts[-1][-1:].isspace():
                        separate = True
                    complete = False
                elif kind == "message_delta":
                    usage.call_updated(item.event.get("usage") or {})
                    complete = item.event.get("delta", {}).get("stop_reason") in FINISHED_STOPS
                text = _text_delta(item)
                if text:
                    if separate:
                        separate = False
                        parts.append(CALL_SEPARATOR)
                        yield TextDelta(CALL_SEPARATOR)
                    parts.append(text)
                    yield TextDelta(text)
            elif isinstance(item, AssistantMessage):
                ours.update(
                    block.id
                    for block in item.content
                    if isinstance(block, ToolUseBlock) and block.name == ALLOWED_TOOL
                )
                live.update(
                    (block.id, block.name)
                    for block in item.content
                    if isinstance(block, ToolUseBlock) and block.name in _LIVE_TOOLS
                )
            elif isinstance(item, UserMessage):
                _note_tool_results(item, ours, state, live)
            elif isinstance(item, ResultMessage):
                capped = item.subtype == "error_max_turns"
                code = None if capped else _result_error(item)
                finished = True
    except ClaudeSDKError as error:
        # The SDK raises after an error result too, and that result names the cause.
        if not capped:  # the shopper still gets a reply, so this is not a failed turn
            log.error("assistant turn failed: %s", type(error).__name__)
    except Exception as error:  # noqa: BLE001 - its text must not reach the shopper
        log.error("assistant turn failed: %s", type(error).__name__)
        code = "internal"
    outage = state.failed and not state.seen
    if capped and not outage:
        usage.report("insufficient_evidence")
        if parts:
            yield Reset()  # text from a call that went on to search is not an answer
        yield TextDelta(INSUFFICIENT_EVIDENCE)
        yield Citations(())
        return
    if (not finished or not complete) and not capped:
        code = code or "model_unavailable"
    if code is None and outage:
        code = "retrieval_failed"
    if code is not None:
        log.error("assistant turn failed: %s", code)
        usage.report(code)
        if parts and outage:
            yield Reset()  # with no sources behind it, this text must not stay on screen
        yield TurnError(code)
        return
    usage.report("answered")
    yield Citations(cited_ids("".join(parts), state.seen))


def _turn_usage(understood: Understood | None, *, deferred: bool = False) -> TurnUsage:
    usage = TurnUsage(deferred=deferred)
    if understood is not None:
        usage.understanding(understood.path, understood.ms, understood.model, understood.usage)
    return usage


async def _first_searcher(
    message: str,
    caller: Caller,
    rag: RagEndpoint,
    runtime: AgentRuntime,
    catalog: CatalogEndpoint | None,
    understand: Understander,
    token: str,
    turn: str,
) -> tuple[McpRagSearcher, Understood | None]:
    """Row #11 (RAG-04): the first search's searcher, with the query plan as a claim on a token for
    that one search when the plan narrows anything. A message the first search would refuse never
    reaches the model (hard rule 1); any failure keeps the plain search."""
    plain = McpRagSearcher(rag.url, token)
    if caller.region is None or not query_ok(message):
        return plain, None
    base_url = runtime.env["ANTHROPIC_BASE_URL"]  # the turn's own proxy and suite
    understood = await understand.plan(message, caller.region, base_url)
    live = None if catalog is None else LiveFilter(catalog.url, catalog.key)
    claim = await plan_claim(understood.plan, message, caller, live)
    if claim is None:
        return plain, understood
    planned = mint_planned(rag.key, caller, turn, claim)
    return (plain if planned is None else McpRagSearcher(rag.url, planned)), understood


async def stream_answer(
    message: str,
    caller: Caller,
    rag: RagEndpoint,
    runtime: AgentRuntime,
    catalog: CatalogEndpoint | None = None,
    understand: Understander | None = None,
    orders: OrdersEndpoint | None = None,
    policy: PolicyEndpoint | None = None,
) -> AsyncIterator[TurnEvent]:
    """`catalog` attaches mcp-catalog (row #12). The gateway and the eval answerer always pass one
    (pinned by tests); `None` is the one-server surface that tests written before #12 use.

    `understand` (row #11, RAG-04): query understanding for the first search. The gateway passes
    one; the eval answerer does not, because its first search is the saved context, which already
    holds the planned search (`make eval-snapshot`).

    `orders` (row #27, CHK-06) attaches mcp-orders, only for a signed-in caller: the gateway passes
    one; the eval answerer does not, and its caller is never signed in. `policy` (PR 2, CHK-07)
    attaches mcp-policy beside it, on the same turns only."""
    state = ToolState(region=caller.region)
    token, turn = rag.mint_turn(caller)
    understood: Understood | None = None
    searcher = McpRagSearcher(rag.url, token)
    if understand is not None:
        searcher, understood = await _first_searcher(
            message, caller, rag, runtime, catalog, understand, token, turn
        )
    found = await search_sources(message, searcher, state)
    if isinstance(found, SearchFailure):
        usage = _turn_usage(understood)
        if found.category == "validation":
            # The gateway refuses these first, but no caller gets to rely on that: a message the
            # search will not take must not reach the model either (hard rule 1).
            usage.report("refused")
            yield TurnError("internal")
            return
        # Q2 and Q6: the first search failed (mcp-rag down, a refused token, a refused or failed
        # search). With nothing to ground it, the turn ends here: no model call.
        log.error("first search failed: %s", found.category)
        usage.report("retrieval_failed")
        yield TurnError("retrieval_failed")
        return
    attached = None if catalog is None else (catalog.url, catalog.mint(caller))
    orders_token = None if orders is None else orders.mint(caller)
    signed_in = None if orders is None or orders_token is None else (orders.url, orders_token)
    policy_token = None if policy is None or signed_in is None else policy.mint(caller)
    returns = None if policy is None or policy_token is None else (policy.url, policy_token)
    options = assistant_options(caller, rag.url, token, runtime, attached, signed_in, returns)
    usage = _turn_usage(understood, deferred=True)
    check = OutputCheck(state.seen, state.sources, live=state.live)
    try:
        async for event in run_turn(user_prompt(message, found), options, state, usage):
            if isinstance(event, TextDelta):
                for out in _sent(check.feed(event.text)):
                    yield out
            elif isinstance(event, Reset):
                outage = state.failed and not state.seen  # text with no sources behind it
                for out in _sent(check.reset(discard=outage)):
                    yield out
            elif isinstance(event, Citations):
                for out in _sent(check.finish()):
                    yield out
                cited = cited_ids(check.shown, state.seen)
                yield Citations(cited, source_cards(cited, state.sources))
            else:
                check.abort()  # fail closed: held text is never sent after a failure
                yield event
    finally:
        usage.blocked = dict(check.counts)
        usage.flush()


def _sent(pieces: list[str | None]) -> list[TextDelta | Reset]:
    return [Reset() if piece is None else TextDelta(piece) for piece in pieces]
