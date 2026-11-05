"""FOUND-3: the generated data holds no card number, nothing real, and refers to products by ID."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.carriers import CARRIERS
from northwind_common.cards import contains_pan
from northwind_data.textscan import (
    find_money_or_stock,
    find_real_brands,
    find_unsafe_hosts,
    iter_strings,
)
from northwind_llm_replay.scan import scan_text

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "data" / "fixtures"
BACKEND = ROOT / "data" / "backend"
FILES = ("orders.jsonl", "shoppers.jsonl", "tracking.jsonl", "manifest.json")
REAL_CARRIERS = (
    "UPS", "FedEx", "Federal Express", "DHL", "USPS", "Royal Mail", "Canada Post", "Purolator",
    "Canpar", "Parcelforce", "Evri", "Hermes", "Yodel", "DPD", "GLS", "TNT", "OnTrac",
    "LaserShip", "Amazon Logistics", "Aramex", "Australia Post", "Deutsche Post",
)  # fmt: skip
_CARRIER_RE = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(re.escape(c) for c in REAL_CARRIERS) + r")(?![A-Za-z])",
    re.IGNORECASE,
)
FORBIDDEN_KEYS = {"name", "title", "price", "amount", "total", "stock", "on_hand", "currency"}


def _text(name: str) -> str:
    return (BACKEND / name).read_text(encoding="utf-8")


def _records(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in _text(name).splitlines()]


def _all_strings() -> Iterator[tuple[str, str]]:
    for name in FILES:
        text = _text(name)
        docs = [json.loads(text)] if name.endswith(".json") else _records(name)
        for i, doc in enumerate(docs):
            for path, value in iter_strings(doc):
                yield f"{name}[{i}]{path}", value


def _keys(obj: Any) -> Iterator[str]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _keys(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _keys(item)


# ---- no PAN -------------------------------------------------------------------------------


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize("name", FILES)
def test_the_cassette_scanner_finds_nothing_in_data_backend(name: str) -> None:
    assert scan_text(_text(name), path=name) == []


@pytest.mark.req("FOUND-3")
def test_no_string_field_contains_a_pan() -> None:
    strings = list(_all_strings())
    assert len(strings) > 5000  # the walk really visited the data
    assert [path for path, value in strings if contains_pan(value)] == []


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize("name", FILES)
def test_no_digit_run_of_13_or_more_anywhere(name: str) -> None:
    assert re.findall(r"\d{13,}", _text(name)) == []
    assert re.findall(r"\d(?:[ -]?\d){12,}", _text(name)) == []  # nor a spaced or dashed one


@pytest.mark.req("FOUND-3")
def test_the_no_pan_checks_can_fail() -> None:
    planted = _text("orders.jsonl").replace("tok_sandbox_ok_0103", "4111111111111111", 1)
    assert planted != _text("orders.jsonl")
    assert any(f.kind == "luhn-valid-pan" for f in scan_text(planted, path="orders.jsonl"))
    assert re.findall(r"\d{13,}", planted) == ["4111111111111111"]
    assert contains_pan("card 4111 1111 1111 1111")
    assert contains_pan("4242-4242-4242-4242")
    assert not contains_pan("tok_sandbox_ok_0103")


# ---- synthetic only -----------------------------------------------------------------------


@pytest.mark.req("FOUND-3")
def test_emails_use_only_reserved_domains() -> None:
    emails = 0
    for path, value in _all_strings():
        assert find_unsafe_hosts(value) == [], path
        emails += "@" in value
    assert emails == 200  # one per shopper, so the check was exercised
    for shopper in _records("shoppers.jsonl"):
        assert shopper["email"].endswith("@example.com")


@pytest.mark.req("FOUND-3")
def test_no_real_brand_or_carrier_name_appears_anywhere() -> None:
    for name in FILES:
        text = _text(name)
        assert find_real_brands(text) == [], name
        assert _CARRIER_RE.findall(text) == [], name
    for path, value in _all_strings():
        assert find_real_brands(value) == [], path
        assert _CARRIER_RE.findall(value) == [], path
    assert _CARRIER_RE.findall("Shipped with FedEx and UPS") == ["FedEx", "UPS"]  # it can fail


@pytest.mark.req("FOUND-3")
def test_every_carrier_is_a_fictional_one_from_carriers() -> None:
    tracking = _records("tracking.jsonl")
    assert tracking
    used = {t["carrier"] for t in tracking}
    assert used <= set(CARRIERS)
    assert len(used) == len(CARRIERS)  # all of them appear


# ---- by ID only ---------------------------------------------------------------------------


def _fixture_rows(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()]


@pytest.mark.req("FOUND-3")
def test_every_product_and_variant_resolves_in_fixtures_and_is_public_and_priced() -> None:
    products = {p["product_id"]: p for p in _fixture_rows("products.jsonl")}
    variants = {v["variant_id"]: v for v in _fixture_rows("variants.jsonl")}
    priced = {(p["region"], p["variant_id"]) for p in _fixture_rows("prices.jsonl")}
    orders = _records("orders.jsonl")
    seen_lines = 0
    for order in orders:
        assert order["lines"], order["order_id"]
        for line in order["lines"]:
            seen_lines += 1
            product = products[line["product_id"]]
            variant = variants[line["variant_id"]]
            assert variant["product_id"] == line["product_id"]
            assert product["visibility"] == "public"
            assert product["status"] == "active"
            assert variant["visibility"] == "public"
            assert (order["region"], line["variant_id"]) in priced, order["order_id"]
    assert seen_lines >= len(orders)


@pytest.mark.req("FOUND-3")
def test_a_line_carries_only_ids_and_a_quantity_and_orders_carry_no_money_or_stock() -> None:
    orders = _records("orders.jsonl")
    for order in orders:
        for line in order["lines"]:
            assert set(line) == {"product_id", "variant_id", "quantity"}
            assert isinstance(line["quantity"], int)
            assert line["quantity"] >= 1
        assert not FORBIDDEN_KEYS & set(_keys(order)), order["order_id"]
    for name in ("orders.jsonl", "tracking.jsonl"):
        assert not FORBIDDEN_KEYS & set(_keys(_records(name)))
        for line in _text(name).splitlines():
            for _, value in iter_strings(json.loads(line)):
                assert find_money_or_stock(value) == [], value


@pytest.mark.req("FOUND-3")
def test_every_order_refers_to_a_generated_shopper_in_the_same_region() -> None:
    shoppers = {s["shopper_id"]: s for s in _records("shoppers.jsonl")}
    assert len(shoppers) == 200
    for order in _records("orders.jsonl"):
        assert shoppers[order["shopper_id"]]["region"] == order["region"]
