"""Scanners for text that may be ingested (hard rules 2 and 3).

`find_money_or_stock` flags currency amounts and stock counts; `find_real_brands` flags real
brand and trademark names; `find_unsafe_hosts` flags URLs/emails outside reserved test domains.
RAG-01 can reuse these on chunk text.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

_NUM = r"\d[\d,]*(?:\.\d+)?"
MONEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[$£€¥]\s?\d"),
    re.compile(r"\b(?:US|CA|C|A|AU|NZ)\$\s?\d"),
    re.compile(rf"\b(?:USD|CAD|GBP|EUR)\s?{_NUM}"),
    re.compile(
        rf"{_NUM}\s?(?:USD|CAD|GBP|EUR|dollars?|bucks|pounds?|quid|euros?|cents?)\b", re.IGNORECASE
    ),
)
STOCK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bonly\s+\d+\s+(?:left|remaining|available|in stock)\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+(?:left|remaining)\b", re.IGNORECASE),
    re.compile(
        r"\b\d+\s+(?:units?|pcs|pieces)\s+(?:left|in stock|available|on hand)\b", re.IGNORECASE
    ),
    re.compile(r"\b\d+\s+(?:in stock|on hand)\b", re.IGNORECASE),
    re.compile(r"\b(?:in stock|stock level|on hand|inventory)\s*[:=]?\s*\d+\b", re.IGNORECASE),
)

# Real brands and trademarks that must not appear in synthetic text (hard rule 3).
REAL_BRANDS: tuple[str, ...] = (
    "Patagonia",
    "The North Face",
    "North Face",
    "Columbia Sportswear",
    "Arc'teryx",
    "Arcteryx",
    "Marmot",
    "Mammut",
    "Salomon",
    "Helly Hansen",
    "Fjallraven",
    "Fjällräven",
    "Canada Goose",
    "Mountain Hardwear",
    "Outdoor Research",
    "REI",
    "L.L.Bean",
    "LL Bean",
    "Eddie Bauer",
    "Lululemon",
    "Uniqlo",
    "Zara",
    "H&M",
    "Nike",
    "Adidas",
    "Under Armour",
    "Gore-Tex",
    "GORE-TEX",
    "Polartec",
    "PrimaLoft",
    "Thinsulate",
    "Cordura",
    "Pertex",
    "Sympatex",
    "Smartwool",
    "Icebreaker",
    "IKEA",
    "West Elm",
    "Pottery Barn",
    "Brooklinen",
    "Crate & Barrel",
    "Williams Sonoma",
    "Le Creuset",
    "Coyuchi",
    "Boll & Branch",
    "Frette",
    "Ralph Lauren",
    "Levi's",
    "Carhartt",
    "Barbour",
    "Burberry",
    "Amazon",
    "Walmart",
    "Responsible Down Standard",
    "Bluesign",
    "OEKO-TEX",
)
_BRAND_RE = re.compile(
    r"(?<![\w])(?:" + "|".join(re.escape(b) for b in REAL_BRANDS) + r")(?![\w])",
    re.IGNORECASE,
)
ALLOWED_HOST_SUFFIXES: tuple[str, ...] = (
    ".example",
    ".invalid",
    ".test",
    "example.com",
    "example.org",
)
_URL_HOST = re.compile(r"https?://([^/\s)\]]+)", re.IGNORECASE)
_EMAIL_HOST = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")


def find_money_or_stock(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in (*MONEY_PATTERNS, *STOCK_PATTERNS):
        hits.extend(m.group(0) for m in pattern.finditer(text))
    return hits


def find_real_brands(text: str) -> list[str]:
    return [m.group(0) for m in _BRAND_RE.finditer(text)]


def find_unsafe_hosts(text: str) -> list[str]:
    hosts = [m.group(1) for m in _URL_HOST.finditer(text)]
    hosts += [m.group(1) for m in _EMAIL_HOST.finditer(text)]
    return [h for h in hosts if not h.lower().endswith(ALLOWED_HOST_SUFFIXES)]


def iter_strings(obj: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Yields (json_path, value) for every string inside nested dicts/lists."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for key in sorted(obj):
            yield from iter_strings(obj[key], f"{path}.{key}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from iter_strings(item, f"{path}[{i}]")
