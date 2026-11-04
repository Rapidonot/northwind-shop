"""Regions, live commercial settings and per-region policy facts (RAG-11).

Money (thresholds, fees, price multipliers) lives only here and in the live price table, as
numbers. Policy text refers to "the threshold shown at checkout" instead (hard rule 2).
Every policy fact that tests rely on has a distinct value per region, so an answer drawn from
the wrong region's policy is detectable.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

REGION_CODES: tuple[str, ...] = ("US", "CA", "UK", "EU")

REGIONS: tuple[dict[str, Any], ...] = (
    {
        "code": "US",
        "name": "United States",
        "currency": "USD",
        "price_multiplier": Decimal("1.00"),
        "free_shipping_threshold": Decimal("75.00"),
        "standard_shipping_fee": Decimal("6.95"),
        "express_shipping_fee": Decimal("19.00"),
        "return_shipping_fee": Decimal("0.00"),
        "stock_nodes": ("DC-EAST", "DC-WEST"),
    },
    {
        "code": "CA",
        "name": "Canada",
        "currency": "CAD",
        "price_multiplier": Decimal("1.36"),
        "free_shipping_threshold": Decimal("99.00"),
        "standard_shipping_fee": Decimal("9.95"),
        "express_shipping_fee": Decimal("24.00"),
        "return_shipping_fee": Decimal("9.00"),
        "stock_nodes": ("DC-CA",),
    },
    {
        "code": "UK",
        "name": "United Kingdom",
        "currency": "GBP",
        "price_multiplier": Decimal("0.82"),
        "free_shipping_threshold": Decimal("60.00"),
        "standard_shipping_fee": Decimal("4.95"),
        "express_shipping_fee": Decimal("12.00"),
        "return_shipping_fee": Decimal("0.00"),
        "stock_nodes": ("DC-UK",),
    },
    {
        "code": "EU",
        "name": "European Union",
        "currency": "EUR",
        "price_multiplier": Decimal("0.94"),
        "free_shipping_threshold": Decimal("70.00"),
        "standard_shipping_fee": Decimal("5.95"),
        "express_shipping_fee": Decimal("15.00"),
        "return_shipping_fee": Decimal("7.50"),
        "stock_nodes": ("DC-EU",),
    },
)

REGION_BY_CODE: dict[str, dict[str, Any]] = {r["code"]: r for r in REGIONS}

# Facts stated in each region's policy documents. The answer key copies these for RAG-11 tests.
POLICY_FACTS: dict[str, dict[str, str]] = {
    "US": {
        "return_window_days": "30",
        "standard_delivery": "3–5 business days",
        "express_delivery": "1–2 business days",
        "return_shipping": "free prepaid return label",
        "clearance": "final sale, not returnable",
        "opened_pillows": "not returnable once removed from packaging",
        "statutory_cancellation": "none",
    },
    "CA": {
        "return_window_days": "45",
        "standard_delivery": "5–8 business days",
        "express_delivery": "2–3 business days",
        "return_shipping": "return-shipping fee deducted from the refund",
        "clearance": "final sale, not returnable",
        "opened_pillows": "returnable within the return window if unused",
        "statutory_cancellation": "none",
    },
    "UK": {
        "return_window_days": "28",
        "standard_delivery": "2–4 business days",
        "express_delivery": "next business day",
        "return_shipping": "free drop-off at a partner point",
        "clearance": "returnable within the 14-day cancellation period",
        "opened_pillows": "returnable only if still sealed",
        "statutory_cancellation": "14-day right to cancel from delivery",
    },
    "EU": {
        "return_window_days": "60",
        "standard_delivery": "4–7 business days",
        "express_delivery": "2–3 business days",
        "return_shipping": "paid by the shopper",
        "clearance": "returnable within the 14-day withdrawal period",
        "opened_pillows": "returnable only if still sealed",
        "statutory_cancellation": "14-day right of withdrawal from delivery",
    },
}
