"""FOUND-1 / hard rule 3: fictional brands only; links and emails only on reserved domains."""

from __future__ import annotations

import pytest

from northwind_data.catalog import APPAREL_BRANDS, HOME_BRANDS
from northwind_data.dataset import Dataset
from northwind_data.textscan import find_real_brands, find_unsafe_hosts, iter_strings


@pytest.mark.req("FOUND-1")
def test_no_real_brand_or_trademark_names_in_text(ds: Dataset) -> None:
    hits = [
        f"{name}{path}: {hit!r}"
        for name, rec in ds.ingestible_records()
        for path, value in iter_strings(rec)
        for hit in find_real_brands(value)
    ]
    assert hits == []


@pytest.mark.req("FOUND-1")
def test_all_brands_are_northwind_house_brands(ds: Dataset) -> None:
    brands = {p["brand"] for p in ds.jsonl("products.jsonl")}
    assert brands <= set(APPAREL_BRANDS) | set(HOME_BRANDS)


@pytest.mark.req("FOUND-1")
def test_urls_and_emails_use_reserved_test_domains(ds: Dataset) -> None:
    hits = [
        f"{name}{path}: {hit!r}"
        for name, rec in ds.ingestible_records()
        for path, value in iter_strings(rec)
        for hit in find_unsafe_hosts(value)
    ]
    assert hits == []


@pytest.mark.req("FOUND-1")
@pytest.mark.parametrize(
    "text", ["a Gore-Tex shell", "like my Patagonia", "PrimaLoft fill", "from IKEA"]
)
def test_brand_scanner_catches_real_brands(text: str) -> None:
    assert find_real_brands(text)


@pytest.mark.req("FOUND-1")
def test_host_scanner_flags_real_domains() -> None:
    assert find_unsafe_hosts("see https://shop.real-store.com/x or mail a@gmail.com") == [
        "shop.real-store.com",
        "gmail.com",
    ]
    assert find_unsafe_hosts("https://northwind-deals.example/claim care@northwind.example") == []
