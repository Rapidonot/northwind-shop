"""One shopper message, streamed (SA-02).

Each message stands alone: conversation memory is SA-05. The server searches on the shopper's
message first, so the first model call can already answer (decision 0007). Citations are the `[ID]`
markers in the answer that match a source returned this turn, so an invented ID never becomes a
citation (hard rule 5). Failures surface only as fixed codes: no exception or upstream text goes
further.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from claude_agent_sdk import ClaudeSDKError, ResultMessage, create_sdk_mcp_server, query
from claude_agent_sdk.types import StreamEvent

from northwind_assistant.caller import Caller
from northwind_assistant.prompt import system_prompt, user_prompt
from northwind_assistant.tools import (
    ALLOWED_TOOL,
    SERVER_NAME,
    Retriever,
    SearchFailure,
    ToolState,
    retrieve_tool,
    search_sources,
)
from northwind_assistant.usage import TurnUsage
from northwind_common.llm import ASSISTANT_MODEL, AgentRuntime, agent_options

log = logging.getLogger(__name__)

# Two refinement searches, one refused search and the answer. `search_sources` holds RAG-10's cap
# of 3 searches, and the refused one tells the model to answer, which takes this extra turn.
MAX_TURNS = 4
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
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class TurnError:
    code: ErrorCode


TurnEvent = TextDelta | Reset | Citations | TurnError


def cited_ids(answer: str, seen: set[str]) -> tuple[str, ...]:
    """Source IDs cited in `answer` that were really returned, first mention first."""
    marked = [part.strip() for group in _MARKER.findall(answer) for part in group.split(",")]
    cited = tuple(dict.fromkeys(source_id for source_id in marked if source_id in seen))
    invented = len(set(marked) - seen)
    if invented:
        log.warning("dropped %d citation(s) to sources not returned this turn", invented)
    return cited


def assistant_options(
    caller: Caller, retriever: Retriever, runtime: AgentRuntime, state: ToolState
) -> Any:
    server = create_sdk_mcp_server(SERVER_NAME, tools=[retrieve_tool(caller, retriever, state)])
    return agent_options(
        system_prompt=system_prompt(caller.region),
        model=ASSISTANT_MODEL,
        tools=[],
        runtime=runtime,
        mcp_servers={SERVER_NAME: server},
        allowed_tools=[ALLOWED_TOOL],
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


def _result_error(result: ResultMessage) -> ErrorCode | None:
    """An upstream failure arrives as `is_error` with subtype `success`, so test `is_error`. The
    result text is only matched here, never passed on."""
    if not result.is_error:
        return None
    if "cassette miss" in (result.result or ""):
        return "replay_miss"
    return "model_unavailable"


async def run_turn(prompt: str, options: Any, state: ToolState) -> AsyncIterator[TurnEvent]:
    parts: list[str] = []
    code: ErrorCode | None = None
    finished = False
    capped = False
    usage = TurnUsage()
    # A cut stream still ends in `message_stop` and a `success` result, so a stop reason in
    # `message_delta` is the only sign that an attempt reached its end.
    complete = True
    try:
        # Runs to the end even after an error: leaving early strands the SDK's generator.
        async for item in query(prompt=prompt, options=options):
            if isinstance(item, StreamEvent):
                kind = item.event.get("type")
                if kind == "message_start":
                    usage.call_started(item.event.get("message") or {})
                    if not complete and parts:
                        parts.clear()
                        yield Reset()
                    complete = False
                elif kind == "message_delta":
                    usage.call_updated(item.event.get("usage") or {})
                    complete = item.event.get("delta", {}).get("stop_reason") in FINISHED_STOPS
                text = _text_delta(item)
                if text:
                    parts.append(text)
                    yield TextDelta(text)
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


async def stream_answer(
    message: str, caller: Caller, retriever: Retriever, runtime: AgentRuntime
) -> AsyncIterator[TurnEvent]:
    state = ToolState()
    found = await search_sources(message, caller, retriever, state)
    if isinstance(found, SearchFailure) and found.category == "validation":
        # The gateway refuses these first, but no caller gets to rely on that: a message the search
        # will not take must not reach the model either (hard rule 1).
        TurnUsage().report("refused")
        yield TurnError("internal")
        return
    sources = found.message if isinstance(found, SearchFailure) else found
    options = assistant_options(caller, retriever, runtime, state)
    async for event in run_turn(user_prompt(message, sources), options, state):
        yield event
