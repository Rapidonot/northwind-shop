"""Gateway configuration, read from the environment."""

from __future__ import annotations

import os
from typing import cast

from northwind_assistant.caller import Caller
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
