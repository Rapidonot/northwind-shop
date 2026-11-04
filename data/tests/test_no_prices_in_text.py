"""FOUND-1 / hard rule 2: no currency amounts or stock counts in any text that can be ingested.

Price and stock live only in prices.jsonl / stock.jsonl, which are never ingestible.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from northwind_data.dataset import Dataset
from northwind_data.textscan import find_money_or_stock, iter_strings

TEXT_FILES = ["documents.jsonl", "products.jsonl", "reviews.jsonl", "variants.jsonl"]


@pytest.mark.req("FOUND-1")
def test_no_currency_amounts_or_stock_counts_in_any_text_field(ds: Dataset) -> None:
    hits = [
        f"{name}:{rec.get('product_id') or rec.get('doc_id')}{path}: {hit!r}"
        for name in TEXT_FILES
        for rec in ds.jsonl(name)
        for path, value in iter_strings(rec)
        for hit in find_money_or_stock(value)
    ]
    assert hits == []


@pytest.mark.req("FOUND-1")
def test_every_text_file_is_scanned_and_nothing_else_is_ingestible(ds: Dataset) -> None:
    assert sorted(ds.manifest["ingestible"]) == TEXT_FILES


@pytest.mark.req("FOUND-1")
@pytest.mark.parametrize(
    "text",
    [
        "$150",
        "now $ 149.99",
        "149.99 USD",
        "USD 99",
        "£20 off",
        "€5 shipping",
        "C$30",
        "CA$45.00",
        "costs 40 dollars",
        "12 bucks",
        "5 euros",
        "only 3 left",
        "Only 2 remaining",
        "12 in stock",
        "4 units left",
        "stock level: 7",
        "inventory = 12",
    ],
)
def test_scanner_catches_money_and_stock(text: str) -> None:
    assert find_money_or_stock(text), text


@pytest.mark.req("FOUND-1")
@pytest.mark.parametrize(
    "text",
    [
        "rated to 20,000 mm",
        "100% merino wool",
        "5 x 8 ft",
        "within 30 days of delivery",
        "UPF 50",
        "2XL",
        "60% wool, 40% acrylic",
        "fits mattresses up to 18 inches deep",
        "Prices are shown live on each product page",
        "out of stock",
        "a 90% markdown",
    ],
)
def test_scanner_allows_ordinary_numbers(text: str) -> None:
    assert find_money_or_stock(text) == [], text


@pytest.mark.req("FOUND-1")
def test_live_tables_hold_exact_prices_and_stock(ds: Dataset) -> None:
    prices = ds.jsonl("prices.jsonl")
    stock = ds.jsonl("stock.jsonl")
    assert prices and stock
    assert all(Decimal(p["amount"]) > 0 and p["currency"] for p in prices)
    assert all(isinstance(s["on_hand"], int) and s["on_hand"] >= 0 for s in stock)
    assert any(s["on_hand"] == 0 for s in stock)
