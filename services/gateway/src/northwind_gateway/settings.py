"""Gateway configuration, read from the environment."""

from __future__ import annotations

import dataclasses
import os
from typing import Annotated, cast

from fastapi import Depends, Request

from northwind_assistant.caller import Caller
from northwind_gateway.session import STATE_KEY
from northwind_rag.retrieval import SHOPPER_REGIONS, ShopperRegion

DEFAULT_DATABASE_URL = "postgresql://northwind:northwind-local@localhost:5432/northwind"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def current_caller() -> Caller:
    """A stub for the shopper's identity until sessions exist (FOUND-3). It reads server config
    only: nothing in the request can set the audience or region (hard rule 7)."""
    region = os.environ.get("DEV_SHOPPER_REGION", "US")
    if region not in SHOPPER_REGIONS:
        raise ValueError("DEV_SHOPPER_REGION is not a shopper region")
    return Caller(region=cast(ShopperRegion, region))


def shopper_caller(request: Request, caller: Annotated[Caller, Depends(current_caller)]) -> Caller:
    """`current_caller` plus the session the middleware bound to this request (row #16, CHK-01).
    Only the middleware sets it, from a cookie it signed; no header, body or query value can. With
    no session, the cart tools refuse (`session_required`), which fails closed."""
    session = getattr(request.state, STATE_KEY, None)
    return dataclasses.replace(caller, session=session if isinstance(session, str) else None)
