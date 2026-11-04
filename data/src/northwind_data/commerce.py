"""Live commercial tables: prices and stock (FOUND-1, hard rule 2).

These are the only place exact prices and stock counts exist. They are never ingestible; the
assistant reads them through live tools (mcp-catalog, RAG-09).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from northwind_data.catalog import Product
from northwind_data.regions import REGION_BY_CODE
from northwind_data.rng import rng

Record = dict[str, Any]


def local_price(us: Decimal, region: str) -> Decimal:
    mult: Decimal = REGION_BY_CODE[region]["price_multiplier"]
    return (us * mult).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_prices(products: list[Product]) -> list[Record]:
    rows: list[Record] = []
    for p in products:
        for v in p.variants:
            us = p.us_prices.get(v["variant_id"])
            if us is None:
                continue
            for region in p.record["regions"]:
                rows.append(
                    {
                        "variant_id": v["variant_id"],
                        "product_id": p.record["product_id"],
                        "region": region,
                        "currency": REGION_BY_CODE[region]["currency"],
                        "amount": str(local_price(us, region)),
                    }
                )
    return sorted(rows, key=lambda row: (row["variant_id"], row["region"]))


def build_stock(products: list[Product], seed: int) -> list[Record]:
    rows: list[Record] = []
    for p in products:
        for v in p.variants:
            vid = v["variant_id"]
            for region in p.record["regions"]:
                for node in REGION_BY_CODE[region]["stock_nodes"]:
                    r = rng(seed, "stock", vid, node)
                    if vid in p.zero_stock:
                        qty = 0
                    elif not p.planted and r.random() < 0.08:
                        qty = 0
                    else:
                        qty = r.randint(1, 80)
                    rows.append(
                        {
                            "variant_id": vid,
                            "product_id": p.record["product_id"],
                            "node": node,
                            "region": region,
                            "on_hand": qty,
                        }
                    )
    return sorted(rows, key=lambda row: (row["variant_id"], row["node"]))
