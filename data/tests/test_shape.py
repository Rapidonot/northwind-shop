"""FOUND-1: sizes, referential integrity, and text that agrees with structured attributes."""

from __future__ import annotations

import re
from collections import Counter

import pytest

from northwind_data.dataset import Dataset
from northwind_data.regions import REGION_BY_CODE, REGION_CODES


@pytest.mark.req("FOUND-1")
def test_dataset_size_matches_scope(ds: Dataset) -> None:
    products = ds.jsonl("products.jsonl")
    categories = Counter(p["category"] for p in products)
    assert 180 <= len(products) <= 220
    assert categories["apparel"] >= 100 and categories["home"] >= 60
    assert 680 <= len(ds.jsonl("variants.jsonl")) <= 920  # ~800, ±15%
    assert len(ds.jsonl("reviews.jsonl")) >= 800
    doc_types = Counter(d["doc_type"] for d in ds.jsonl("documents.jsonl"))
    assert doc_types["size_guide"] >= 6 and doc_types["care_guide"] >= 8
    assert doc_types["policy_returns"] == doc_types["policy_shipping"] == len(REGION_CODES)
    assert doc_types["faq"] >= 25 and doc_types["internal"] >= 5


@pytest.mark.req("FOUND-1")
def test_ids_are_unique(ds: Dataset) -> None:
    for name, key in (
        ("products.jsonl", "product_id"),
        ("variants.jsonl", "variant_id"),
        ("reviews.jsonl", "review_id"),
        ("documents.jsonl", "doc_id"),
    ):
        ids = [r[key] for r in ds.jsonl(name)]
        assert len(ids) == len(set(ids)), name


@pytest.mark.req("FOUND-1")
def test_references_resolve(ds: Dataset) -> None:
    products = {p["product_id"]: p for p in ds.jsonl("products.jsonl")}
    variants = {v["variant_id"]: v for v in ds.jsonl("variants.jsonl")}
    docs = {d["doc_id"] for d in ds.jsonl("documents.jsonl")}
    for v in variants.values():
        p = products[v["product_id"]]
        assert v["colour"] in p["colours"] and v["size"] in p["sizes"]
    for r in ds.jsonl("reviews.jsonl"):
        assert r["product_id"] in products
        if r["variant_id"]:
            assert variants[r["variant_id"]]["product_id"] == r["product_id"]
    for p in products.values():
        assert p["care_guide_id"] in docs
        assert p["size_guide_id"] is None or p["size_guide_id"] in docs
    for row in (*ds.jsonl("prices.jsonl"), *ds.jsonl("stock.jsonl")):
        assert variants[row["variant_id"]]["product_id"] == row["product_id"]


@pytest.mark.req("FOUND-1")
def test_every_public_variant_has_a_price_and_stock_in_each_region_it_is_sold(
    ds: Dataset,
) -> None:
    products = {p["product_id"]: p for p in ds.jsonl("products.jsonl")}
    priced = {(r["variant_id"], r["region"]): r for r in ds.jsonl("prices.jsonl")}
    stocked = {(r["variant_id"], r["region"]) for r in ds.jsonl("stock.jsonl")}
    for v in ds.jsonl("variants.jsonl"):
        p = products[v["product_id"]]
        if p["visibility"] != "public":
            assert not any(k[0] == v["variant_id"] for k in priced)
            continue
        assert p["regions"] and sorted(p["price_bands"]) == sorted(p["regions"])
        for region in p["regions"]:
            row = priced[(v["variant_id"], region)]
            assert row["currency"] == REGION_BY_CODE[region]["currency"]
            assert (v["variant_id"], region) in stocked


NOT_WATERPROOF_CLAIM = re.compile(r"(?<!not )(?<!than )\bwaterproof\b", re.IGNORECASE)


@pytest.mark.req("FOUND-1")
def test_product_copy_agrees_with_attributes(ds: Dataset) -> None:
    for p in ds.jsonl("products.jsonl"):
        text, a = p["description"], p["attributes"]
        if a.get("water_protection") == "waterproof":
            assert "waterproof" in text, p["product_id"]
        else:
            assert not NOT_WATERPROOF_CLAIM.search(text), p["product_id"]
        has_relaxed_copy = bool(re.search(r"relaxed fit", text, re.IGNORECASE))
        assert has_relaxed_copy == ("relaxed" in p["fits"]), p["product_id"]
        if a.get("care") == "dry_clean_only":
            assert "Dry clean only" in text and "Machine washable" not in text
