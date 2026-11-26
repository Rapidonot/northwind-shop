"""Substitutes on a stock-out (BE-04, row #25): built by code, never chosen or worded by a model
(the user's constraint 2). There is no model call in this module.

A candidate for variant V, in region R, for quantity q, must:
- be public (variant and product), so an internal product never reaches a shopper (hard rule 7);
- belong to a product with V's department and subcategory (V's own product included);
- have a live price in R, in V's currency, within `PRICE_BAND` (±20%) of V's live price. Prices come
  from the live price table, never the index (hard rule 2);
- be reservable now: some single node of R has at least q available, live holds subtracted.

They are ranked by a fixed key:
1. the tier: the same product in the same size and fit (another colour), then another product in the
   same size and fit, then the same product in another size or fit, then another product;
2. then more matching product attributes;
3. then the smaller price gap;
4. then the variant ID.

At most one variant per product and `MAX_SUBSTITUTES` in all. The output carries IDs and a reason
code only: no price, no stock count and no text.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from northwind_backend.fakes.catalog import FIXTURES, CatalogState
from northwind_backend.inventory.model import Substitute

MAX_SUBSTITUTES = 3
PRICE_BAND_PERCENT = 20

TIERS = (
    "same_product_other_colour",
    "similar_product_same_size",
    "same_product_other_size",
    "similar_product",
)


@dataclass(frozen=True, slots=True)
class _Variant:
    variant_id: str
    product_id: str
    colour: str
    size: str
    fit: str


@dataclass(frozen=True, slots=True)
class _Product:
    product_id: str
    department: str
    subcategory: str
    attributes: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class SubstituteCatalog:
    """The public catalog's shape, from the fixtures: what substitutes are matched on."""

    variants: dict[str, _Variant]
    products: dict[str, _Product]
    by_product: dict[str, tuple[str, ...]]

    @classmethod
    def load(cls, fixtures: Path = FIXTURES) -> SubstituteCatalog:
        products: dict[str, _Product] = {}
        for row in _jsonl(fixtures / "products.jsonl"):
            if row.get("visibility") != "public":
                continue
            attributes = tuple(
                sorted(
                    (str(k), json.dumps(v, sort_keys=True)) for k, v in row["attributes"].items()
                )
            )
            products[row["product_id"]] = _Product(
                row["product_id"], row["department"], row["subcategory"], attributes
            )
        variants: dict[str, _Variant] = {}
        by_product: dict[str, list[str]] = {}
        for row in _jsonl(fixtures / "variants.jsonl"):
            if row.get("visibility") != "public" or row["product_id"] not in products:
                continue
            variants[row["variant_id"]] = _Variant(
                row["variant_id"], row["product_id"], row["colour"], row["size"], row["fit"]
            )
            by_product.setdefault(row["product_id"], []).append(row["variant_id"])
        return cls(variants, products, {p: tuple(sorted(v)) for p, v in sorted(by_product.items())})


@cache
def default_catalog() -> SubstituteCatalog:
    return SubstituteCatalog.load()


@cache
def default_prices() -> CatalogState:
    return CatalogState()


def find(
    variant_id: str,
    region: str,
    quantity: int,
    reservable: Callable[[str], int],
    *,
    catalog: SubstituteCatalog | None = None,
    prices: CatalogState | None = None,
) -> tuple[Substitute, ...]:
    """`reservable(variant)` is the most any single node of `region` can give now, live holds
    subtracted. An unknown or internal variant, or one with no price in `region`, gets none."""
    catalog = catalog or default_catalog()
    prices = prices or default_prices()
    wanted = catalog.variants.get(variant_id)
    base = prices.prices.get((variant_id, region))
    if wanted is None or base is None:
        return ()
    product = catalog.products[wanted.product_id]
    ranked: list[tuple[tuple[int, int, int, str], Substitute]] = []
    for other_id, other_product in catalog.products.items():
        if (other_product.department, other_product.subcategory) != (
            product.department,
            product.subcategory,
        ):
            continue
        same_product = other_id == product.product_id
        overlap = len(set(product.attributes) & set(other_product.attributes))
        best: tuple[tuple[int, int, int, str], Substitute] | None = None
        for candidate_id in catalog.by_product.get(other_id, ()):
            if candidate_id == variant_id:
                continue
            quote = prices.prices.get((candidate_id, region))
            if quote is None or quote.currency != base.currency:
                continue
            gap = abs(quote.amount_minor - base.amount_minor)
            if gap * 100 > base.amount_minor * PRICE_BAND_PERCENT:
                continue
            if reservable(candidate_id) < quantity:
                continue
            candidate = catalog.variants[candidate_id]
            same_size = (candidate.size, candidate.fit) == (wanted.size, wanted.fit)
            tier = _tier(same_product, same_size)
            key = (tier, -overlap, gap, candidate_id)
            if best is None or key < best[0]:
                best = (key, Substitute(candidate_id, other_id, TIERS[tier]))
        if best is not None:
            ranked.append(best)
    ranked.sort(key=lambda pair: pair[0])
    return tuple(found for _, found in ranked[:MAX_SUBSTITUTES])


def _tier(same_product: bool, same_size: bool) -> int:
    if same_product:
        return 0 if same_size else 2
    return 1 if same_size else 3


def _jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
