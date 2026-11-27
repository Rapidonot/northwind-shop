"""GET /api/orders: the signed-in shopper's order history (SA-01 PR 2; hard rules 7, 8, 10).

The seeded orders come from mcp-orders' portal-only `list_orders`, read by the gateway as its own
agent, `shopper-portal`, with the shopper ID from the signed sign-in cookie in the token (`shp`),
never from a request value. Orders placed with Pay (demo) come from `demo_orders` for the same
shopper. Newest first.

- Not signed in: 200 `{"signed_in": false, "message": "Sign in to see your orders."}`, with no
  `orders` key, so it can never be read as an empty list (hard rule 10). Signed out is a normal
  state of a normal page, not an error: a 4xx would make the browser log a failed request on every
  anonymous visit (the e2e "no errors" journey found it). The portal shows a sign-in prompt.
- Any failure: one fixed `transient` 503; nothing beyond its type name reaches a log.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Annotated, Any

import httpx2
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import PORTAL_AGENT
from northwind_assistant.rag_client import quiet_client_logs
from northwind_common.mcp_token import ORDERS_SERVER, TokenKeyError, bearer, mint, signing_key
from northwind_common.order_tool import ORDER_ID, STATUSES, mcp_orders_url
from northwind_common.tool_scopes import LIST_ORDERS
from northwind_gateway.demo_orders import demo_orders
from northwind_gateway.portal_write import NO_STORE, envelope
from northwind_gateway.settings import shopper_caller
from northwind_gateway.shop import PRODUCT_ID, VARIANT_ID

log = logging.getLogger(__name__)

ORDERS_PATH = "/api/orders"
SIGN_IN = "Sign in to see your orders."
UNAVAILABLE = "Your orders aren't available right now."
DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)

router = APIRouter()


class Unreadable(Exception):
    """A result that is not what the tool returns; holds no text on purpose."""


def _line(line: Any) -> dict[str, Any]:
    if not isinstance(line, dict):
        raise Unreadable
    product, variant, quantity = (
        line.get("product_id"),
        line.get("variant_id"),
        line.get("quantity"),
    )
    if not isinstance(product, str) or not PRODUCT_ID.fullmatch(product):
        raise Unreadable
    if not isinstance(variant, str) or not VARIANT_ID.fullmatch(variant):
        raise Unreadable
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise Unreadable
    return {"product_id": product, "variant_id": variant, "quantity": quantity}


def _order(order: Any) -> dict[str, Any]:
    """The allow-listed fields only, each type-checked; anything else fails closed."""
    if not isinstance(order, dict):
        raise Unreadable
    order_id, placed_on, status = order.get("order_id"), order.get("placed_on"), order.get("status")
    if not isinstance(order_id, str) or not ORDER_ID.fullmatch(order_id):
        raise Unreadable
    if placed_on is not None and (not isinstance(placed_on, str) or not DATE.fullmatch(placed_on)):
        raise Unreadable
    if status not in STATUSES or not isinstance(order.get("status_text"), str):
        raise Unreadable
    lines = order.get("lines")
    if not isinstance(lines, list):
        raise Unreadable
    return {
        "order_id": order_id,
        "placed_on": placed_on,
        "status": status,
        "status_text": order["status_text"],
        "lines": [_line(line) for line in lines],
    }


@dataclass(frozen=True)
class OrdersLister:
    url: str
    key: bytes = field(repr=False)

    async def list(self, caller: Caller) -> list[dict[str, Any]]:
        if caller.shopper_id is None:
            raise Unreadable
        token, _ = mint(
            audience="shopper",
            region=caller.region,
            agent=PORTAL_AGENT,
            key=self.key,
            server=ORDERS_SERVER,
            shopper=caller.shopper_id,
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
            result = await client.call_tool(LIST_ORDERS, {})
        if result.is_error or len(result.content) != 1:
            raise Unreadable
        try:
            body = json.loads(getattr(result.content[0], "text", ""))
        except ValueError:
            raise Unreadable from None
        if not isinstance(body, dict) or not isinstance(body.get("orders"), list):
            raise Unreadable
        return [_order(order) for order in body["orders"]]


def orders_lister() -> OrdersLister | None:
    try:
        return OrdersLister(mcp_orders_url(), signing_key())
    except TokenKeyError:
        return None


@router.get(ORDERS_PATH)
async def orders(
    caller: Annotated[Caller, Depends(shopper_caller)],
    lister: Annotated[OrdersLister | None, Depends(orders_lister)],
) -> JSONResponse:
    if caller.shopper_id is None:
        return JSONResponse(content={"signed_in": False, "message": SIGN_IN}, headers=NO_STORE)
    if lister is None:
        log.error("orders read failed: no key")
        return envelope(503, "transient", True, UNAVAILABLE)
    try:
        seeded = await lister.list(caller)
    except Exception as error:  # noqa: BLE001 - its text must not reach the shopper or a log
        log.error("orders read failed: %s", type(error).__name__)
        return envelope(503, "transient", True, UNAVAILABLE)
    placed = demo_orders().of(caller.shopper_id)
    every = [*placed, *seeded]
    every.sort(key=lambda o: (o["placed_on"] or "", o["order_id"]), reverse=True)
    body = {"signed_in": True, "orders": every, "total": len(every)}
    return JSONResponse(content=body, headers=NO_STORE)
