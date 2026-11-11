"""CatalogPort fake over the live price and stock tables (row #21; a stub for `mcp-catalog`, #10b).

It reads `data/fixtures/prices.jsonl` and `stock.jsonl`, the only place exact prices and stock live
(FOUND-1, hard rule 2), never the index. Stock is summed over a region's nodes. Prices are decimal
strings with two places in every currency the fixtures use, so minor units are exact.

A variant with no price or stock row in a region is a validation error, not an empty result: the
order names a variant that isn't sold there (hard rule 10).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from northwind_backend.errors import Ok, Result, ToolError, validation
from northwind_backend.fakes._base import (
    CARD_DATA_REFUSED,
    FakeService,
    FakeState,
    card_data_in,
    matches,
)
from northwind_backend.ports import PriceQuote, StockLevel

CATALOG_SCOPES = frozenset({"price", "stock"})
FIXTURES = Path(__file__).resolve().parents[5] / "data" / "fixtures"
_VARIANT = re.compile(r"P\d{4}(?:-[A-Z0-9]{1,16}){1,4}", re.ASCII)
_REGION = re.compile(r"US|CA|UK|EU", re.ASCII)

BAD_VARIANT = validation("invalid_variant_id", "The variant ID is malformed.")
BAD_REGION = validation("invalid_region", "The region is not one Northwind sells in.")
NOT_SOLD = validation("not_sold_here", "This variant is not sold in this region.")


class CatalogState(FakeState):
    def __init__(self, fixtures: Path = FIXTURES) -> None:
        super().__init__()
        self.prices: dict[tuple[str, str], PriceQuote] = {}
        for row in _jsonl(fixtures / "prices.jsonl"):
            minor = Decimal(row["amount"]) * 100
            if minor != minor.to_integral_value():
                raise ValueError("a price with more than two decimal places")
            key = (row["variant_id"], row["region"])
            self.prices[key] = PriceQuote(
                row["variant_id"], row["region"], int(minor), row["currency"]
            )
        on_hand: dict[tuple[str, str], int] = defaultdict(int)
        for row in _jsonl(fixtures / "stock.jsonl"):
            on_hand[(row["variant_id"], row["region"])] += int(row["on_hand"])
        self.stock = {
            key: StockLevel(key[0], key[1], units) for key, units in sorted(on_hand.items())
        }


class CatalogTableFake(FakeService):
    def __init__(
        self, *, state: CatalogState | None = None, scopes: frozenset[str] = CATALOG_SCOPES
    ) -> None:
        state = state or CatalogState()
        super().__init__(scopes, state)
        self.state = state

    def price(self, variant_id: str, region: str) -> Result[PriceQuote]:
        refused = self._reachable("price") or _check(variant_id, region)
        if refused is not None:
            return refused
        found = self.state.prices.get((variant_id, region))
        return NOT_SOLD if found is None else Ok(found)

    def stock(self, variant_id: str, region: str) -> Result[StockLevel]:
        refused = self._reachable("stock") or _check(variant_id, region)
        if refused is not None:
            return refused
        found = self.state.stock.get((variant_id, region))
        return NOT_SOLD if found is None else Ok(found)


def _check(variant_id: object, region: object) -> ToolError | None:
    if card_data_in(variant_id, region):
        return CARD_DATA_REFUSED
    if not matches(_VARIANT, variant_id):
        return BAD_VARIANT
    return None if matches(_REGION, region) else BAD_REGION


def _jsonl(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
