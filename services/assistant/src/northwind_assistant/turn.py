"""One shopper message, streamed (SA-02).

Each message stands alone: conversation memory is SA-05. Citations are the `[ID]` markers in the
answer that match a source returned this turn, so an invented ID never becomes a citation (hard
rule 5). Failures surface only as fixed codes: no exception or upstream text goes further.
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
    ToolState,
    retrieve_tool,
)
from northwind_common.llm import ASSISTANT_MODEL, AgentRuntime, agent_options

log = logging.getLogger(__name__)

# Up to 3 retrieval rounds and the answer: inside RAG-10's 3-round cap.
MAX_TURNS = 4

ErrorCode = Literal[
    "retrieval_failed", "model_unavailable", "replay_miss", "turn_limit", "internal"
]

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
    if result.subtype == "error_max_turns":
        return "turn_limit"
    if not result.is_error:
        return None
    if "cassette miss" in (result.result or ""):
        return "replay_miss"
    return "model_unavailable"


async def run_turn(message: str, options: Any, state: ToolState) -> AsyncIterator[TurnEvent]:
    parts: list[str] = []
    code: ErrorCode | None = None
    finished = False
    # A cut stream still ends in `message_stop` and a `success` result, so a stop reason in
    # `message_delta` is the only sign that an attempt reached its end.
    complete = True
    try:
        # Runs to the end even after an error: leaving early strands the SDK's generator.
        async for item in query(prompt=user_prompt(message), options=options):
            if isinstance(item, StreamEvent):
                kind = item.event.get("type")
                if kind == "message_start":
                    if not complete and parts:
                        parts.clear()
                        yield Reset()
                    complete = False
                elif kind == "message_delta":
                    complete = True
                text = _text_delta(item)
                if text:
                    parts.append(text)
                    yield TextDelta(text)
            elif isinstance(item, ResultMessage):
                code = _result_error(item)
                finished = True
    except ClaudeSDKError as error:
        # The SDK raises after an error result too, and that result names the cause.
        log.error("assistant turn failed: %s", type(error).__name__)
    except Exception as error:  # noqa: BLE001 - its text must not reach the shopper
        log.error("assistant turn failed: %s", type(error).__name__)
        code = "internal"
    if not finished or not complete:
        code = code or "model_unavailable"
    if code is None and state.failed and not state.seen:
        code = "retrieval_failed"
    if code is not None:
        log.error("assistant turn failed: %s", code)
        if code == "retrieval_failed" and parts:
            yield Reset()  # with no sources behind it, this text must not stay on screen
        yield TurnError(code)
        return
    yield Citations(cited_ids("".join(parts), state.seen))


async def stream_answer(
    message: str, caller: Caller, retriever: Retriever, runtime: AgentRuntime
) -> AsyncIterator[TurnEvent]:
    state = ToolState()
    options = assistant_options(caller, retriever, runtime, state)
    async for event in run_turn(message, options, state):
        yield event
