"""Who is asking. Built by the server, never from anything the shopper or the model sends."""

from __future__ import annotations

from dataclasses import dataclass

from northwind_rag.retrieval import Audience, ShopperRegion


@dataclass(frozen=True)
class Caller:
    region: ShopperRegion
    audience: Audience = "shopper"
