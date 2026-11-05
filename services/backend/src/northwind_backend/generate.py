"""Seeded synthetic shoppers, orders and tracking (FOUND-3), written to data/backend/.

Same seed and same fixtures, same bytes. Nothing reads the clock: dates are offsets from the
fixtures' `base_date`. Each record has its own random stream, so one record can't reshuffle the
others.

Products and variants come from data/fixtures by ID only. No name, price or stock figure is copied
(hard rule 2), so an order carries no amount; a later row joins prices.jsonl. Fixtures are only
read: this output is not ingested, and data/fixtures never changes because of it.

Each exception kind reaches its fake through the data:
  soft_decline  a soft-decline sandbox token, which PaymentFake declines
  fraud_flag    listed by `fraud_flagged_order_ids`, for FraudFake
  address_error an undeliverable postal code, which CarrierFake refuses
  stock_out     a first line whose variant has none on hand across the region's nodes
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from northwind_backend.carriers import carrier_for, tracking_number_for
from northwind_backend.fakes.carrier import UNDELIVERABLE_PREFIX
from northwind_backend.fakes.payment import SANDBOX_APPROVED, SANDBOX_SOFT_DECLINE

# Bump when the output changes. Separate from FOUND-1's DATA_VERSION: nothing here is ingested.
BACKEND_DATA_VERSION = 1
DEFAULT_SEED = 20261105
SHOPPER_COUNT = 200
ORDER_COUNT = 1000
EXCEPTION_KINDS = ("address_error", "stock_out", "soft_decline", "fraud_flag")
EXCEPTIONS_PER_KIND = 25  # 100 of 1,000: the "about 10%" in the PRD's backend replay set
MIN_STOCK = 3  # the most any order line asks for
ORDER_WINDOW_DAYS = 28

EMAIL_DOMAIN = "example.com"
_REGIONS = ("US", "UK", "CA", "EU")
_REGION_WEIGHTS = (50, 20, 15, 15)
_FIRST_NAMES = (
    "Arlo", "Bexley", "Calla", "Dorian", "Elowen", "Fenn", "Greer", "Hollis", "Isolde", "Jory",
    "Kestrel", "Linnea", "Marlo", "Nell", "Orin", "Perrin", "Quill", "Rowan", "Sable", "Tamsin",
)  # fmt: skip
_SURNAMES = (
    "Alderwick", "Brackenhurst", "Cinderfell", "Dunmarrow", "Elmsworth", "Fernhallow",
    "Greywater", "Hartlemoor", "Ivyholt", "Juniperlee", "Kettlewood", "Larkspurne", "Mossgarth",
    "Nettlecombe", "Oakenshire", "Pinehollow", "Quarrystone", "Rushmere", "Sedgewick", "Thistlemar",
)  # fmt: skip
_STREETS = (
    "Lantern Row", "Heron Walk", "Bracken Lane", "Tidewater Close", "Copper Mill Road",
    "Saltmarsh Way", "Orchard Rise", "Wren Terrace",
)  # fmt: skip
_CITIES = (
    "Fernhollow", "Mistral Bay", "Eastmere", "Quillhaven", "Larchford", "Thornby Vale",
    "Silverbrook Falls", "Port Harrowmere",
)  # fmt: skip
_SCANS = ("picked_up", "in_transit", "out_for_delivery", "delivered")


def _rng(seed: int, *parts: str) -> random.Random:
    # Reproducible synthetic data, not security: a seeded PRNG is the point.
    return random.Random(":".join((str(seed), *parts)))  # noqa: S311


def _jsonl(records: Iterable[dict[str, Any]]) -> bytes:
    lines = [
        json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for record in records
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@dataclass(frozen=True, slots=True)
class Catalog:
    """Sellable variant IDs per region, as (variant_id, product_id), in a fixed order."""

    base_date: date
    dataset_sha256: str
    in_stock: dict[str, list[tuple[str, str]]]
    out_of_stock: dict[str, list[tuple[str, str]]]


def load_catalog(fixtures: Path) -> Catalog:
    manifest = json.loads((fixtures / "manifest.json").read_text(encoding="utf-8"))
    public_products = {
        p["product_id"]
        for p in _read_jsonl(fixtures / "products.jsonl")
        if p["visibility"] == "public" and p["status"] == "active"
    }
    variants = {
        v["variant_id"]: v["product_id"]
        for v in _read_jsonl(fixtures / "variants.jsonl")
        if v["visibility"] == "public" and v["product_id"] in public_products
    }
    priced: dict[str, set[str]] = defaultdict(set)
    for price in _read_jsonl(fixtures / "prices.jsonl"):
        priced[price["region"]].add(price["variant_id"])
    # Summed first: a region can have two nodes, and one empty node isn't a stock-out.
    on_hand: dict[tuple[str, str], int] = defaultdict(int)
    for stock in _read_jsonl(fixtures / "stock.jsonl"):
        on_hand[(stock["region"], stock["variant_id"])] += stock["on_hand"]

    in_stock: dict[str, list[tuple[str, str]]] = defaultdict(list)
    out_of_stock: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (region, variant_id), units in sorted(on_hand.items()):
        if variant_id not in variants or variant_id not in priced[region]:
            continue
        if units == 0:
            out_of_stock[region].append((variant_id, variants[variant_id]))
        elif units >= MIN_STOCK:
            in_stock[region].append((variant_id, variants[variant_id]))
    return Catalog(
        base_date=date.fromisoformat(manifest["base_date"]),
        dataset_sha256=manifest["dataset_sha256"],
        in_stock=dict(in_stock),
        out_of_stock=dict(out_of_stock),
    )


def _shopper(seed: int, number: int) -> dict[str, Any]:
    shopper_id = f"S{number:04d}"
    rng = _rng(seed, "shopper", shopper_id)
    first, last = rng.choice(_FIRST_NAMES), rng.choice(_SURNAMES)
    region = rng.choices(_REGIONS, _REGION_WEIGHTS)[0]
    return {
        "address": {
            "city": rng.choice(_CITIES),
            "line1": f"{rng.randint(1, 980)} {rng.choice(_STREETS)}",
            "postal_code": f"{region}-{rng.randint(1000, 9899)}",
            "region": region,
        },
        "email": f"{first}.{last}.{number:04d}@{EMAIL_DOMAIN}".lower(),
        "name": f"{first} {last}",
        "region": region,
        "shopper_id": shopper_id,
    }


def _exception_plan(seed: int) -> dict[int, str]:
    kinds = [kind for kind in EXCEPTION_KINDS for _ in range(EXCEPTIONS_PER_KIND)]
    positions = list(range(1, ORDER_COUNT + 1))
    _rng(seed, "exceptions").shuffle(positions)
    return dict(zip(positions, kinds, strict=False))


def _line(pick: tuple[str, str], quantity: int) -> dict[str, Any]:
    return {"product_id": pick[1], "quantity": quantity, "variant_id": pick[0]}


def _order(
    seed: int, number: int, shopper: dict[str, Any], exception: str | None, catalog: Catalog
) -> dict[str, Any]:
    order_id = f"O{number:06d}"
    rng = _rng(seed, "order", order_id)
    region = shopper["region"]
    picks = rng.sample(catalog.in_stock[region], rng.randint(1, 3))
    lines = [_line(pick, rng.randint(1, MIN_STOCK)) for pick in picks]
    if exception == "stock_out":
        lines[0] = _line(rng.choice(catalog.out_of_stock[region]), 1)

    postal_code = shopper["address"]["postal_code"]
    if exception == "address_error":
        postal_code = f"{UNDELIVERABLE_PREFIX}-{rng.randint(1000, 9899)}"
    suffix = shopper["shopper_id"][1:]
    token = SANDBOX_SOFT_DECLINE if exception == "soft_decline" else SANDBOX_APPROVED
    placed = catalog.base_date + timedelta(days=rng.randrange(ORDER_WINDOW_DAYS))
    return {
        "exception": exception,
        "lines": lines,
        "order_id": order_id,
        "payment_token": f"{token}_{suffix}",
        "placed_on": placed.isoformat(),
        "region": region,
        "shipping_postal_code": postal_code,
        "shopper_id": shopper["shopper_id"],
    }


def _tracking(seed: int, order: dict[str, Any]) -> dict[str, Any]:
    """Zero scans is a real state here: a label printed and not yet collected."""
    order_id = order["order_id"]
    rng = _rng(seed, "tracking", order_id)
    placed = date.fromisoformat(order["placed_on"])
    day = placed + timedelta(days=1)
    events = []
    for status in _SCANS[: rng.randint(0, len(_SCANS))]:
        events.append({"on": day.isoformat(), "status": status})
        day += timedelta(days=rng.randint(1, 2))
    return {
        "carrier": carrier_for(order_id),
        "events": events,
        "order_id": order_id,
        "tracking_number": tracking_number_for(order_id),
    }


def fraud_flagged_order_ids(orders: Iterable[dict[str, Any]]) -> frozenset[str]:
    return frozenset(o["order_id"] for o in orders if o["exception"] == "fraud_flag")


def build(fixtures: Path, seed: int = DEFAULT_SEED) -> dict[str, bytes]:
    """Every output file by name. Writes nothing."""
    catalog = load_catalog(fixtures)
    shoppers = [_shopper(seed, n) for n in range(1, SHOPPER_COUNT + 1)]
    plan = _exception_plan(seed)
    pick = _rng(seed, "order-shoppers")
    orders = [
        _order(seed, n, pick.choice(shoppers), plan.get(n), catalog)
        for n in range(1, ORDER_COUNT + 1)
    ]
    # An order held on an exception never reaches a carrier.
    tracking = [_tracking(seed, o) for o in orders if o["exception"] is None]

    files = {
        "orders.jsonl": _jsonl(orders),
        "shoppers.jsonl": _jsonl(shoppers),
        "tracking.jsonl": _jsonl(tracking),
    }
    manifest = {
        "backend_data_version": BACKEND_DATA_VERSION,
        "counts": {"orders": len(orders), "shoppers": len(shoppers), "tracking": len(tracking)},
        "files": {name: hashlib.sha256(body).hexdigest() for name, body in sorted(files.items())},
        "generator": "northwind_backend",
        "seed": seed,
        "source_dataset_sha256": catalog.dataset_sha256,
    }
    text = json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    return {**files, "manifest.json": text.encode("utf-8")}
