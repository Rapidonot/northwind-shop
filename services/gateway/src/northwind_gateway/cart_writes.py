"""The bag's cart writes (SA-01 PR 2; hard rules 1, 2, 9, 10; the user's stop 5 yes, 2026-11-27).

- `POST /api/cart/items` {variant_id, quantity, idempotency_key}: ADD TO BAG (`add_to_cart`);
- `POST /api/cart/quantity` {variant_id, quantity, idempotency_key}: the − / + controls
  (`set_cart_quantity`);
- `POST /api/cart/remove` {variant_id, idempotency_key}: Remove (`remove_from_cart`).

Exact paths, because the session middleware binds sessions to exact paths. The gateway calls the
cart tools itself, as `shopper-portal`, with the session and region from the signed cookie and one
turn per session (`portal_write.portal_turn`), never through a model. Live stock is checked by the
tool (hard rule 2). Every request passes `portal_write.from_portal` first.

Results: `{"result", "variant_id", "quantity"}` from the tool's own outcome; the portal then reads
the bag again (GET /api/cart). A refusal passes on the tool's fixed category, code and message only
(its stock-out alternatives are left out: the bag has no use for them). Any other failure is one
fixed `transient` 503. Nothing from a failure reaches a log beyond its type name.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Annotated, Any

import httpx2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT
from pydantic import BaseModel, ConfigDict, Field

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import PORTAL_AGENT
from northwind_assistant.rag_client import quiet_client_logs
from northwind_common import catalog_tool
from northwind_common.cards import contains_card_data
from northwind_common.localenv import mcp_catalog_url
from northwind_common.mcp_token import CATALOG_SERVER, TokenKeyError, bearer, mint, signing_key
from northwind_gateway.chat import CardNumberRefused
from northwind_gateway.portal_write import (
    KEY,
    NO_STORE,
    envelope,
    from_portal,
    portal_turn,
    refused,
)
from northwind_gateway.settings import shopper_caller
from northwind_gateway.shop import VARIANT_ID

log = logging.getLogger(__name__)

ADD_PATH = "/api/cart/items"
QUANTITY_PATH = "/api/cart/quantity"
REMOVE_PATH = "/api/cart/remove"
CART_WRITE_PATHS = (ADD_PATH, QUANTITY_PATH, REMOVE_PATH)

UNAVAILABLE = "The bag can't be changed right now. Please try again."
INVALID = "That request could not be accepted."
CODE = re.compile(r"[a-z_]{1,48}", re.ASCII)
CATEGORY_STATUS = {"validation": 422, "business": 409, "permission": 403, "transient": 503}
MAX_QUANTITY = 10

router = APIRouter()


class Unreadable(Exception):
    """A result that is not what the tool returns; holds no text on purpose."""


@dataclass(frozen=True)
class Refusal:
    category: str
    retryable: bool
    code: str
    message: str


def _refusal(text: str) -> Refusal:
    try:
        body = json.loads(text)
    except ValueError:
        raise Unreadable from None
    if not isinstance(body, dict):
        raise Unreadable
    category, code, message = body.get("errorCategory"), body.get("code"), body.get("message")
    if category not in CATEGORY_STATUS or not isinstance(code, str) or not CODE.fullmatch(code):
        raise Unreadable
    if not isinstance(message, str) or contains_card_data(message) or len(message) > 300:
        raise Unreadable
    return Refusal(str(category), body.get("isRetryable") is True, code, message)


@dataclass(frozen=True)
class CartWriter:
    """The gateway's cart writes, as `shopper-portal`, with the caller's session and region."""

    url: str
    key: bytes = field(repr=False)

    async def write(
        self, caller: Caller, tool: str, args: dict[str, Any]
    ) -> dict[str, Any] | Refusal:
        if caller.session is None:
            raise Unreadable
        token, _ = mint(
            audience="shopper",
            region=caller.region,
            agent=PORTAL_AGENT,
            key=self.key,
            server=CATALOG_SERVER,
            session=caller.session,
            turn=portal_turn(caller.session),
        )
        quiet_client_logs()
        async with (
            httpx2.AsyncClient(
                headers=bearer(token),
                timeout=httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
                trust_env=False,  # an HTTP_PROXY in the environment must never carry the token
            ) as http,
            Client(streamable_http_client(self.url, http_client=http)) as client,
        ):
            result = await client.call_tool(tool, args)
        if result.is_error:
            # The envelope comes first; a stock refusal adds its alternatives after it, which the
            # bag doesn't show (the add came from a product page the shopper chose on).
            if not result.content:
                raise Unreadable
            return _refusal(getattr(result.content[0], "text", ""))
        if len(result.content) != 1:
            raise Unreadable
        text = getattr(result.content[0], "text", "")
        labelled = catalog_tool.unlabel(text)
        if labelled is None or labelled[0] != tool or not isinstance(labelled[1], dict):
            raise Unreadable
        return labelled[1]


def cart_writer() -> CartWriter | None:
    try:
        return CartWriter(mcp_catalog_url(), signing_key())
    except TokenKeyError:
        return None


Writer = Annotated[CartWriter | None, Depends(cart_writer)]
Shopper = Annotated[Caller, Depends(shopper_caller)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    variant_id: str = Field(max_length=64)
    idempotency_key: str = Field(max_length=64)


class AddBody(_Body):
    quantity: int = Field(default=1, ge=1, le=MAX_QUANTITY)


class QuantityBody(_Body):
    quantity: int = Field(ge=1, le=MAX_QUANTITY)


class RemoveBody(_Body):
    pass


def _outcome(value: dict[str, Any], variant_id: str) -> dict[str, Any]:
    """The tool's own outcome, or its replay's original one (`already_applied`)."""
    result = value.get("result")
    if result == "already_applied" and isinstance(value.get("original"), dict):
        original = value["original"]
        return {
            "result": "already_applied",
            "variant_id": variant_id,
            "quantity": original.get("quantity")
            if isinstance(original.get("quantity"), int)
            else None,
        }
    if not isinstance(result, str) or not CODE.fullmatch(result):
        raise Unreadable
    quantity = value.get("quantity")
    return {
        "result": result,
        "variant_id": variant_id,
        "quantity": quantity
        if isinstance(quantity, int) and not isinstance(quantity, bool)
        else None,
    }


async def _write(
    request: Request,
    caller: Caller,
    writer: CartWriter | None,
    tool: str,
    body: _Body,
    args: dict[str, Any],
) -> JSONResponse:
    if not from_portal(request):
        return refused()
    texts = [body.variant_id, body.idempotency_key]
    if any(contains_card_data(t) for t in texts) or contains_card_data(" ".join(texts)):
        raise CardNumberRefused
    if not VARIANT_ID.fullmatch(body.variant_id) or not KEY.fullmatch(body.idempotency_key):
        return envelope(422, "validation", False, INVALID)
    if writer is None or caller.session is None:
        log.error("cart write failed: %s", "no key" if writer is None else "no session")
        return envelope(503, "transient", True, UNAVAILABLE)
    try:
        written = await writer.write(
            caller, tool, {**args, "idempotency_key": body.idempotency_key}
        )
        if isinstance(written, Refusal):
            status = CATEGORY_STATUS[written.category]
            return envelope(
                status, written.category, written.retryable, written.message, code=written.code
            )
        outcome = _outcome(written, body.variant_id)
    except Exception as error:  # noqa: BLE001 - its text must not reach the shopper or a log
        log.error("cart write failed: %s", type(error).__name__)
        return envelope(503, "transient", True, UNAVAILABLE)
    return JSONResponse(content=outcome, headers=NO_STORE)


@router.post(ADD_PATH)
async def add_item(
    request: Request, body: AddBody, caller: Shopper, writer: Writer
) -> JSONResponse:
    args = {"variant_id": body.variant_id, "quantity": body.quantity}
    return await _write(request, caller, writer, "add_to_cart", body, args)


@router.post(QUANTITY_PATH)
async def set_quantity(
    request: Request, body: QuantityBody, caller: Shopper, writer: Writer
) -> JSONResponse:
    args = {"variant_id": body.variant_id, "quantity": body.quantity}
    return await _write(request, caller, writer, "set_cart_quantity", body, args)


@router.post(REMOVE_PATH)
async def remove_item(
    request: Request, body: RemoveBody, caller: Shopper, writer: Writer
) -> JSONResponse:
    return await _write(
        request, caller, writer, "remove_from_cart", body, {"variant_id": body.variant_id}
    )
