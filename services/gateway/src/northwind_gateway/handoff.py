"""POST /api/handoff: the shopper asks for a person (row #23, SA-06; NFR-08; decision 0025).

The portal calls this from its "Talk to a person" button, or after `/api/chat` answered a message
the deterministic trigger read as a request for a person (`handoff_trigger`). Either way no model
decides it, and the Shopper Assistant has no tool that reaches here (hard rule 8).

- The body is the shopper's own transcript as the portal showed it: at most 10 turns of at most
  2,000 characters, untrusted. Card data in any turn, or across turns, is refused with chat's fixed
  422 before anything is stored or reaches the Care agent (hard rule 1).
- Who is asking comes from the server only: the session the middleware signed, the demo sign-in,
  the region (`shopper_caller`). An anonymous session is named by `session_ref`, an HMAC of its ID
  under a key derived for this purpose, never the ID itself. Without a session it fails closed.
- The cart is read live as `shopper-portal` (row #16's `CartReader`) and becomes the case's amounts
  when there is no order (hard rule 2). A failed read is no amounts, never a guess.
- The answer is 202 with the handoff's reference and a fixed notice, at once: the Care agent builds
  the case after, in the desk (`northwind_backend.handoff`). The same session or shopper asking
  again gets the same reference (hard rule 9). Failures are the structured envelope with fixed
  text (hard rule 10); nothing from a request or an upstream error reaches a log or a response.

The desk runs in this process, on BE-07's fakes: a lab simplification (decision 0025).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CartReader
from northwind_backend.agents.care import CarePorts, Turn
from northwind_backend.audit import AuditContext, InMemoryAuditSink
from northwind_backend.errors import Ok
from northwind_backend.fakes.cases import CaseQueueFake, CaseState
from northwind_backend.fakes.handoffs import HandoffQueueFake, HandoffState
from northwind_backend.fakes.orders import DATA, OrderState, OrderStoreFake
from northwind_backend.handoff import (
    MAX_HANDOFF_TURNS,
    CartSnapshot,
    HandoffDesk,
    HandoffRequest,
    Identity,
)
from northwind_common.cards import contains_card_data
from northwind_common.llm import agent_runtime
from northwind_common.localenv import mcp_catalog_url
from northwind_common.mcp_token import TokenKeyError, signing_key
from northwind_gateway.chat import CardNumberRefused
from northwind_gateway.session import HANDOFF_PATH, session_key
from northwind_gateway.settings import shopper_caller

log = logging.getLogger(__name__)

MAX_TURN_CHARS = 2000
HANDOFF_LABEL = b"northwind-handoff-v1"
NOTICE = (
    "You asked for a person. Someone from Northwind's care team will take over this chat. Until "
    "they reply, you're still chatting with an AI assistant."
)
UNAVAILABLE = "A person can't be reached from here right now. Please try again."
NO_STORE = {"Cache-Control": "no-store"}
_LETTERS = "abcdefghijklmnop"

router = APIRouter()


class TurnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["shopper", "assistant"]
    text: str = Field(max_length=MAX_TURN_CHARS)


class HandoffBody(BaseModel):
    """`trigger` is what the portal reports (the button or a matched message): a label for staff,
    never a permission."""

    model_config = ConfigDict(extra="forbid")

    turns: list[TurnIn] = Field(default_factory=list, max_length=MAX_HANDOFF_TURNS)
    trigger: Literal["button", "message"] = "button"


def checked_body(body: HandoffBody) -> HandoffBody:
    texts = [turn.text for turn in body.turns]
    if any(contains_card_data(text) for text in texts) or contains_card_data(" ".join(texts)):
        raise CardNumberRefused
    return body


def session_ref(token_key: bytes, session: str) -> str:
    """32 letters a-p: an HMAC of the session ID under a key derived for handoffs. It names the
    session to staff without being the cookie's value, and it has no digits, so no card check can
    mistake it for card data."""
    key = hmac.new(session_key(token_key), HANDOFF_LABEL, hashlib.sha256).digest()
    digest = hmac.new(key, session.encode("ascii"), hashlib.sha256).digest()[:16]
    return "".join(_LETTERS[b >> 4] + _LETTERS[b & 15] for b in digest)


@lru_cache(maxsize=1)
def handoff_desk() -> HandoffDesk:
    """Once per process, on BE-07's fakes (decision 0025): the order store seeded read-only from
    `data/backend`, an in-memory case queue and handoff queue, and an in-memory audit. Each Care run
    gets its own agent runtime, through the LLM proxy."""
    care = CarePorts(
        orders=OrderStoreFake(state=OrderState.from_data(DATA)),
        cases=CaseQueueFake(state=CaseState()),
    )
    audit = AuditContext(InMemoryAuditSink(), correlation_id="gateway", actor="shopper-portal")
    return HandoffDesk(
        queue=HandoffQueueFake(state=HandoffState()), care=care, audit=audit, runtime=agent_runtime
    )


def cart_reader() -> CartReader | None:
    try:
        return CartReader(mcp_catalog_url(), signing_key())
    except TokenKeyError:
        return None


def _envelope(status: int, category: str, retryable: bool, message: str) -> JSONResponse:
    body = {
        "isError": True,
        "errorCategory": category,
        "isRetryable": retryable,
        "message": message,
    }
    return JSONResponse(status_code=status, content=body, headers=NO_STORE)


async def _cart(reader: CartReader | None, caller: Caller) -> CartSnapshot | None:
    if reader is None:
        return None
    try:
        return CartSnapshot.from_view(await reader.read(caller))
    except Exception as error:  # noqa: BLE001 - no amounts, never a failed handoff
        log.error("handoff cart read failed: %s", type(error).__name__)
        return None


@router.post(HANDOFF_PATH)
async def handoff(
    body: Annotated[HandoffBody, Depends(checked_body)],
    caller: Annotated[Caller, Depends(shopper_caller)],
    desk: Annotated[HandoffDesk, Depends(handoff_desk)],
    reader: Annotated[CartReader | None, Depends(cart_reader)],
) -> JSONResponse:
    if caller.session is None:
        return _envelope(503, "transient", True, UNAVAILABLE)
    try:
        key = signing_key()
    except TokenKeyError:
        log.error("handoff failed: no key")
        return _envelope(503, "transient", True, UNAVAILABLE)
    identity = (
        Identity("shopper", caller.shopper_id, caller.region)
        if caller.shopper_id is not None
        else Identity("session", session_ref(key, caller.session), caller.region)
    )
    try:
        request = HandoffRequest(
            identity,
            body.trigger,
            tuple(Turn(turn.role, turn.text) for turn in body.turns),
            await _cart(reader, caller),
        )
    except ValueError:
        return _envelope(422, "validation", False, "That request could not be accepted.")
    try:
        opened = await desk.request(request)
    except Exception as error:  # noqa: BLE001 - its text must not reach the shopper
        log.error("handoff failed: %s", type(error).__name__)
        return _envelope(503, "transient", True, UNAVAILABLE)
    if not isinstance(opened, Ok):
        log.error("handoff refused: %s", opened.code)
        return _envelope(503, "transient", True, UNAVAILABLE)
    content: dict[str, Any] = {
        "status": "queued",
        "reference": opened.value.reference,
        "notice": NOTICE,
    }
    return JSONResponse(status_code=202, content=content, headers=NO_STORE)
