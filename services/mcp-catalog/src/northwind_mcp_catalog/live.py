"""The catalog's live store (row #10b, decision D2 as amended by the user, 2026-11-13; hard rule 2).

`LiveCatalog` is what the tools read: products, variants, and the live price and stock per region.
`FixtureCatalog` seeds an in-memory store from `data/fixtures`, after `verify_dataset` has checked
the files against their manifest; it refuses to start on a mismatch. After that the store, not the
files, is the live state: `set_price` and `set_stock` change it while the server runs (tests now,
the backend later), and every tool call reads it afresh. There is no snapshot cache.

`set_price` and `set_stock` are not tools. Nothing in `tools.py` or `server.py` calls them, so no
MCP request can reach them.

Never the index: nothing here imports the RAG package or a database driver, or queries the
index's chunk table (pinned by `test_the_catalog_source_never_touches_the_index`). Exact prices and
stock live only here, never in an embedding or retrieved text (hard rule 2).

Visibility is stored, not filtered: the tools decide what a caller may see.
"""

from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from northwind_data.dataset import FIXTURES_DIR
from northwind_data.ingest_guard import load_verified

_CURRENCY = re.compile(r"[A-Z]{3}", re.ASCII)
_REGION = re.compile(r"[A-Z]{2,3}", re.ASCII)


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    brand: str
    category: str
    department: str
    subcategory: str
    gender: str | None  # null for home goods
    description: str
    attributes: Mapping[str, Any]
    colours: tuple[str, ...]
    sizes: tuple[str, ...]
    fits: tuple[str, ...]
    regions: tuple[str, ...]
    care_guide_id: str | None
    size_guide_id: str | None
    visibility: str


@dataclass(frozen=True)
class Variant:
    variant_id: str
    product_id: str
    sku: str
    colour: str
    colour_family: str
    fit: str | None  # null where fit does not apply
    size: str
    visibility: str


@dataclass(frozen=True)
class Price:
    variant_id: str
    region: str
    minor_units: int
    currency: str

    @property
    def amount(self) -> str:
        """Two decimal places, exactly, from integer minor units."""
        units, cents = divmod(self.minor_units, 100)
        return f"{units}.{cents:02d}"


@dataclass(frozen=True)
class Review:
    """A shopper review, as the fixture holds it (SA-01 PR 2). Shopper-written text: data, never
    an instruction (hard rule 6). It holds no price or stock (hard rule 2)."""

    review_id: str
    product_id: str
    title: str
    body: str
    reviewer: str
    date: str
    verified: bool
    visibility: str


@runtime_checkable
class ReviewSource(Protocol):
    """The reviews behind the portal's `list_reviews`. Apart from `LiveCatalog` on purpose: a store
    that has none still serves every other tool."""

    def reviews_of(self, product_id: str) -> Sequence[Review]: ...


class LiveCatalog(Protocol):
    """What the catalog tools read, plus the two writes that change live state."""

    def products(self) -> Sequence[Product]: ...

    def product(self, product_id: str) -> Product | None: ...

    def variant(self, variant_id: str) -> Variant | None: ...

    def variants_of(self, product_id: str) -> Sequence[Variant]: ...

    def price(self, variant_id: str, region: str) -> Price | None: ...

    def stock(self, variant_id: str, region: str) -> int | None: ...

    def set_price(self, variant_id: str, region: str, amount: str, currency: str) -> None: ...

    def set_stock(self, variant_id: str, region: str, on_hand: int) -> None: ...


def _minor_units(amount: str) -> int:
    try:
        value = Decimal(amount)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("price amount is not a decimal") from None
    minor = value * 100
    if not value.is_finite() or value <= 0 or minor != minor.to_integral_value():
        raise ValueError("price amount must be positive with at most two decimal places")
    return int(minor)


def _jsonl(data: bytes) -> list[dict[str, Any]]:
    """One record per `\\n`-terminated line, as the writer writes them. Not `str.splitlines()`,
    which also splits on U+2028, U+2029 and U+0085 inside a value (re-review N5)."""
    return [json.loads(line.decode("utf-8")) for line in data.split(b"\n") if line.strip()]


def _optional(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


class FixtureCatalog:
    """The in-memory live store, seeded from verified fixtures."""

    def __init__(self, fixtures_dir: Path = FIXTURES_DIR) -> None:
        fixtures_dir = Path(fixtures_dir)
        # Q4: raises `DatasetMismatchError` before any file is parsed, so a tampered dataset never
        # seeds the store and the server does not start. The store parses the very bytes that were
        # verified, never a second read of the files (security review L2).
        verified = load_verified(fixtures_dir).files
        self._lock = threading.Lock()
        self._products: dict[str, Product] = {}
        for row in _jsonl(verified["products.jsonl"]):
            self._products[row["product_id"]] = Product(
                product_id=row["product_id"],
                name=row["name"],
                brand=row["brand"],
                category=row["category"],
                department=row["department"],
                subcategory=row["subcategory"],
                gender=_optional(row.get("gender")),
                description=row["description"],
                attributes=dict(row.get("attributes") or {}),
                colours=tuple(row.get("colours") or ()),
                sizes=tuple(row.get("sizes") or ()),
                fits=tuple(row.get("fits") or ()),
                regions=tuple(row.get("regions") or ()),
                care_guide_id=_optional(row.get("care_guide_id")),
                size_guide_id=_optional(row.get("size_guide_id")),
                visibility=row["visibility"],
            )
        self._variants: dict[str, Variant] = {}
        by_product: dict[str, list[Variant]] = defaultdict(list)
        for row in _jsonl(verified["variants.jsonl"]):
            variant = Variant(
                variant_id=row["variant_id"],
                product_id=row["product_id"],
                sku=row["sku"],
                colour=row["colour"],
                colour_family=row["colour_family"],
                fit=_optional(row.get("fit")),
                size=row["size"],
                visibility=row["visibility"],
            )
            self._variants[variant.variant_id] = variant
            by_product[variant.product_id].append(variant)
        self._by_product = {
            pid: tuple(sorted(vs, key=lambda v: v.variant_id)) for pid, vs in by_product.items()
        }
        self._prices: dict[tuple[str, str], Price] = {}
        for row in _jsonl(verified["prices.jsonl"]):
            key = (row["variant_id"], row["region"])
            self._prices[key] = Price(
                row["variant_id"], row["region"], _minor_units(row["amount"]), row["currency"]
            )
        stock: dict[tuple[str, str], int] = defaultdict(int)
        for row in _jsonl(verified["stock.jsonl"]):
            stock[(row["variant_id"], row["region"])] += int(row["on_hand"])
        self._stock = dict(stock)
        self._ordered = tuple(sorted(self._products.values(), key=lambda p: p.product_id))
        reviews: dict[str, list[Review]] = defaultdict(list)
        for row in _jsonl(verified["reviews.jsonl"]):
            review = Review(
                review_id=str(row["review_id"]),
                product_id=str(row["product_id"]),
                title=str(row.get("title") or ""),
                body=str(row.get("body") or ""),
                reviewer=str(row.get("reviewer") or ""),
                date=str(row.get("date") or ""),
                verified=row.get("verified") is True,
                visibility=str(row["visibility"]),
            )
            reviews[review.product_id].append(review)
        # Newest first, then by ID, so the order never depends on the file's.
        self._reviews = {
            pid: tuple(sorted(rs, key=lambda r: (r.date, r.review_id), reverse=True))
            for pid, rs in reviews.items()
        }

    # ---- reads: every call reads the current state -------------------------------------------

    def products(self) -> Sequence[Product]:
        return self._ordered

    def product(self, product_id: str) -> Product | None:
        return self._products.get(product_id)

    def variant(self, variant_id: str) -> Variant | None:
        return self._variants.get(variant_id)

    def variants_of(self, product_id: str) -> Sequence[Variant]:
        return self._by_product.get(product_id, ())

    def reviews_of(self, product_id: str) -> Sequence[Review]:
        return self._reviews.get(product_id, ())

    def price(self, variant_id: str, region: str) -> Price | None:
        with self._lock:
            return self._prices.get((variant_id, region))

    def stock(self, variant_id: str, region: str) -> int | None:
        with self._lock:
            return self._stock.get((variant_id, region))

    # ---- writes: not tools; tests now, the backend later -------------------------------------

    def set_price(self, variant_id: str, region: str, amount: str, currency: str) -> None:
        if variant_id not in self._variants:
            raise KeyError("unknown variant")
        if not _REGION.fullmatch(region) or not _CURRENCY.fullmatch(currency):
            raise ValueError("bad region or currency")
        price = Price(variant_id, region, _minor_units(amount), currency)
        with self._lock:
            self._prices[(variant_id, region)] = price

    def set_stock(self, variant_id: str, region: str, on_hand: int) -> None:
        if variant_id not in self._variants:
            raise KeyError("unknown variant")
        if not _REGION.fullmatch(region):
            raise ValueError("bad region")
        if isinstance(on_hand, bool) or not isinstance(on_hand, int) or on_hand < 0:
            raise ValueError("on_hand must be a non-negative integer")
        with self._lock:
            self._stock[(variant_id, region)] = on_hand


def eligible_products(
    catalog: LiveCatalog,
    region: str,
    *,
    currency: str,
    price_min: int | None = None,
    price_max: int | None = None,
    in_stock: bool = False,
    held: Mapping[str, int] | None = None,
) -> list[str]:
    """Public products with at least one variant sold in `region` whose live price, in `currency`,
    is within the bounds (minor units, inclusive) and, with `in_stock`, that has stock on hand less
    `held`, the units BE-04 holds live in the region.
    Price
    and stock must hold for the same variant. IDs only, never a price or a count (row #11, RAG-04;
    hard rule 2: read live on every call, never from the index)."""
    found: list[str] = []
    for product in catalog.products():
        if product.visibility != "public":
            continue
        for variant in catalog.variants_of(product.product_id):
            if variant.visibility != "public":
                continue
            price = catalog.price(variant.variant_id, region)
            if price is None or price.currency != currency:
                continue
            if price_min is not None and price.minor_units < price_min:
                continue
            if price_max is not None and price.minor_units > price_max:
                continue
            stock = catalog.stock(variant.variant_id, region) or 0
            # Less BE-04's live holds in the region (`holds.py`; row #18, step 0 (c)).
            if in_stock and stock - (held or {}).get(variant.variant_id, 0) <= 0:
                continue
            found.append(product.product_id)
            break
    return sorted(found)
