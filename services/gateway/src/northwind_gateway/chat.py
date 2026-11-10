"""POST /api/chat: one shopper message, answered over SSE (SA-02).

Nothing a shopper sends comes back, reaches a log or stays on disk here (`agent_options` turns the
CLI's transcripts off). FastAPI's default 422 repeats each bad field's `input`, so a rejected body
could echo a card number: both handlers here return fixed text. Card data is refused in a
dependency, before the response starts and before any model call (hard rule 1). `error` events hold
one code from a closed set, never exception or upstream text.

Events: `token` {text}, then `citations` {source_ids} and `done` {}, or `error` {code}. `reset` {}
means a model attempt was cut off and retried, so the text so far must be dropped.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, field_validator

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.turn import Citations, Reset, TextDelta, TurnError, stream_answer
from northwind_common.cards import contains_card_data
from northwind_common.llm import AgentRuntime, agent_runtime
from northwind_common.localenv import mcp_rag_url
from northwind_common.mcp_token import signing_key
from northwind_gateway.settings import current_caller

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 2000
INVALID_MESSAGE = "That message could not be accepted."
CARD_MESSAGE = "Please don't share card details in chat. Your message was not sent."

router = APIRouter()


class ChatRequest(BaseModel):
    """Only the message: audience and region are bound on the server."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)

    @field_validator("message")
    @classmethod
    def _has_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank")  # the handler answers with fixed text, not this
        return value


class CardNumberRefused(Exception):
    """Holds no text on purpose."""


def _refusal(message: str) -> JSONResponse:
    body = {
        "isError": True,
        "errorCategory": "validation",
        "isRetryable": False,
        "message": message,
    }
    return JSONResponse(status_code=422, content=body)


def invalid_request(_request: Request, _error: Exception) -> JSONResponse:
    return _refusal(INVALID_MESSAGE)


def card_refused(_request: Request, _error: Exception) -> JSONResponse:
    return _refusal(CARD_MESSAGE)


def checked_message(body: ChatRequest) -> str:
    if contains_card_data(body.message):
        raise CardNumberRefused
    return body.message


@dataclass(frozen=True)
class ChatServices:
    rag: RagEndpoint
    runtime: AgentRuntime


@lru_cache(maxsize=1)
def chat_services() -> ChatServices:
    """Once per process: each `agent_runtime()` makes a temp directory, and it fixes the proxy URL
    when called, so this stays lazy. Searches go to mcp-rag (CC-10), which holds the index and the
    embedding model; the token key comes only from the environment (hard rule 4)."""
    return ChatServices(RagEndpoint(mcp_rag_url(), signing_key()), agent_runtime())


def _event(event: Any) -> ServerSentEvent:
    if isinstance(event, TextDelta):
        return ServerSentEvent(event="token", data={"text": event.text})
    if isinstance(event, Reset):
        return ServerSentEvent(event="reset", data={})
    if isinstance(event, Citations):
        return ServerSentEvent(event="citations", data={"source_ids": list(event.source_ids)})
    return ServerSentEvent(event="error", data={"code": event.code})


@router.post("/api/chat", response_class=EventSourceResponse)
async def chat(
    message: Annotated[str, Depends(checked_message)],
    caller: Annotated[Caller, Depends(current_caller)],
    services: Annotated[ChatServices, Depends(chat_services)],
) -> AsyncIterable[ServerSentEvent]:
    failed = False
    try:
        async for event in stream_answer(message, caller, services.rag, services.runtime):
            failed = failed or isinstance(event, TurnError)
            yield _event(event)
    except Exception as error:  # noqa: BLE001 - its text must not reach the shopper
        log.error("chat failed: %s", type(error).__name__)
        failed = True
        yield ServerSentEvent(event="error", data={"code": "internal"})
    if not failed:
        yield ServerSentEvent(event="done", data={})
