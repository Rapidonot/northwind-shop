"""GET /api/cart: the shopper's cart for the chat panel (row #16, CHK-01; decision 0019).

The gateway reads the cart itself, from mcp-catalog's `get_cart`, as its own agent,
`shopper-portal`. That agent has `get_cart` and nothing else (`tool_scopes`). The session and region
come from the server: the middleware's signed cookie and `current_caller`. So the panel shows what
the server holds, never text a model wrote.

A failure of any kind (no session, mcp-catalog down, a refused token, an error result, an unreadable
result) is one fixed `transient` envelope with status 503. The portal shows "Cart unavailable" for
it, never an empty cart. Nothing from the failure reaches the response or a log beyond its type
name.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CartReader
from northwind_common.localenv import mcp_catalog_url
from northwind_common.mcp_token import signing_key
from northwind_gateway.settings import shopper_caller

log = logging.getLogger(__name__)

UNAVAILABLE = "The cart isn't available right now."
NO_STORE = {"Cache-Control": "no-store"}

router = APIRouter()


@lru_cache(maxsize=1)
def cart_reader() -> CartReader:
    """Once per process. The key comes only from the environment (hard rule 4)."""
    return CartReader(mcp_catalog_url(), signing_key())


def _unavailable() -> JSONResponse:
    body = {
        "isError": True,
        "errorCategory": "transient",
        "isRetryable": True,
        "message": UNAVAILABLE,
    }
    return JSONResponse(status_code=503, content=body, headers=NO_STORE)


@router.get("/api/cart")
async def cart(
    caller: Annotated[Caller, Depends(shopper_caller)],
    reader: Annotated[CartReader, Depends(cart_reader)],
) -> JSONResponse:
    try:
        view = await reader.read(caller)
    except Exception as error:  # noqa: BLE001 - its text must not reach the shopper or a log
        log.error("cart read failed: %s", type(error).__name__)
        return _unavailable()
    if view is None:
        log.error("cart read failed: refused")
        return _unavailable()
    return JSONResponse(content={"cart": view}, headers=NO_STORE)
