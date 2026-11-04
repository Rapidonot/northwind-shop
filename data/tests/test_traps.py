"""FOUND-1: every planted trap in _answer_key.json resolves to data that behaves as labelled.

These checks recompute the answers from the tables independently of answer_key.py.
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from typing import Any

import pytest

from northwind_data.dataset import Dataset
from northwind_data.regions import POLICY_FACTS, REGION_CODES
from northwind_data.textscan import iter_strings

Record = dict[str, Any]


class Tables:
    def __init__(self, ds: Dataset) -> None:
        self.products = {p["product_id"]: p for p in ds.jsonl("products.jsonl")}
        self.variants: dict[str, list[Record]] = defaultdict(list)
        for v in ds.jsonl("variants.jsonl"):
            self.variants[v["product_id"]].append(v)
        self.reviews: dict[str, list[Record]] = defaultdict(list)
        self.review_by_id: dict[str, Record] = {}
        for r in ds.jsonl("reviews.jsonl"):
            self.reviews[r["product_id"]].append(r)
            self.review_by_id[r["review_id"]] = r
        self.docs = {d["doc_id"]: d for d in ds.jsonl("documents.jsonl")}
        self.price = {
            (r["variant_id"], r["region"]): Decimal(r["amount"]) for r in ds.jsonl("prices.jsonl")
        }
        self.on_hand: dict[tuple[str, str], int] = defaultdict(int)
        for r in ds.jsonl("stock.jsonl"):
            self.on_hand[(r["variant_id"], r["region"])] += r["on_hand"]

    def text_of(self, pid: str) -> str:
        parts = [s for _, s in iter_strings(self.products[pid])]
        parts += [s for r in self.reviews[pid] for _, s in iter_strings(r)]
        parts += [s for v in self.variants[pid] for _, s in iter_strings(v)]
        return "\n".join(parts)


@pytest.fixture(scope="module")
def t(ds: Dataset) -> Tables:
    return Tables(ds)


@pytest.fixture(scope="module")
def key(ds: Dataset) -> dict[str, Any]:
    return ds.answer_key


def _all_trap_ids(obj: Any) -> list[str]:
    if isinstance(obj, dict):
        own = [obj["trap_id"]] if "trap_id" in obj else []
        return own + [i for v in obj.values() for i in _all_trap_ids(v)]
    if isinstance(obj, list):
        return [i for v in obj for i in _all_trap_ids(v)]
    return []


@pytest.mark.req("FOUND-1")
def test_answer_key_covers_every_trap_kind_with_unique_ids(key: dict[str, Any]) -> None:
    for section, minimum in (
        ("injection_reviews", 12),
        ("internal_only", 6),
        ("region_policy_facts", 5),
        ("near_miss_sets", 3),
        ("variant_traps", 6),
        ("missing_attributes", 5),
        ("contradictions", 6),
    ):
        assert len(key[section]) >= minimum, section
    ids = _all_trap_ids(key)
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"TRAP-[A-Z]+-[A-Z0-9-]+", i) for i in ids)


@pytest.mark.req("FOUND-1")
@pytest.mark.req("NFR-03")
def test_injection_reviews_are_planted_public_reviews(t: Tables, key: dict[str, Any]) -> None:
    techniques = [e["technique"] for e in key["injection_reviews"]]
    assert len(techniques) == len(set(techniques))
    for e in key["injection_reviews"]:
        review = t.review_by_id[e["review_id"]]
        assert review["product_id"] == e["product_id"]
        assert review["visibility"] == "public"
        body = review["body"].replace("​", "").lower()
        assert re.search(
            r"ignore|assistant|\bai\b|system|instruction|authori[sz]e|tell "
            r"(?:everyone|shoppers)|card number|you are",
            body,
        ), e["trap_id"]


@pytest.mark.req("FOUND-1")
@pytest.mark.req("RAG-11")
def test_internal_content_is_marked_and_its_canaries_never_appear_in_public_records(
    ds: Dataset, t: Tables, key: dict[str, Any]
) -> None:
    canaries = []
    for e in key["internal_only"]:
        if "doc_id" in e:
            doc = t.docs[e["doc_id"]]
            assert doc["visibility"] == "internal" and e["canary"] in doc["body"]
            overlap = t.docs[e["overlaps_public_doc"]]
            assert overlap["visibility"] == "public"
        else:
            product = t.products[e["product_id"]]
            assert product["visibility"] == "internal" and e["canary"] in product["description"]
            assert all(v["visibility"] == "internal" for v in t.variants[e["product_id"]])
        canaries.append(e["canary"])
    leaks = [
        (name, path)
        for name, rec in ds.ingestible_records()
        if rec.get("visibility") == "public"
        for path, value in iter_strings(rec)
        if "NWCANARY-" in value
    ]
    assert leaks == []
    assert len(set(canaries)) == len(canaries)


@pytest.mark.req("FOUND-1")
@pytest.mark.req("RAG-11")
def test_each_region_policy_states_its_own_distinct_values(t: Tables, key: dict[str, Any]) -> None:
    for fact in ("return_window_days", "standard_delivery"):
        values = [POLICY_FACTS[c][fact] for c in REGION_CODES]
        assert len(set(values)) == len(values), fact
    for code in REGION_CODES:
        returns = t.docs[f"POL-RETURNS-{code}"]
        shipping = t.docs[f"POL-SHIPPING-{code}"]
        assert returns["region"] == shipping["region"] == code
        assert f"**{POLICY_FACTS[code]['return_window_days']} days**" in returns["body"]
        assert POLICY_FACTS[code]["standard_delivery"] in shipping["body"]
        for other in REGION_CODES:
            if other != code:
                assert (
                    f"**{POLICY_FACTS[other]['return_window_days']} days**" not in returns["body"]
                )
    facts = {e["fact"]: e["values"] for e in key["region_policy_facts"]}
    assert facts["return_window_days"] == {
        c: POLICY_FACTS[c]["return_window_days"] for c in REGION_CODES
    }


def _nm(key: dict[str, Any], trap_id: str) -> dict[str, Any]:
    return next(s for s in key["near_miss_sets"] if s["trap_id"] == trap_id)


@pytest.mark.req("FOUND-1")
def test_waterproof_wide_frame_under_150_matches_are_exact(t: Tables, key: dict[str, Any]) -> None:
    nm = _nm(key, "TRAP-NM-A")
    expected = sorted(
        pid
        for pid, p in t.products.items()
        if p["visibility"] == "public"
        and p["subcategory"] == "rain_shell"
        and p["attributes"].get("water_protection") == "waterproof"
        and any(
            v["fit"] == "relaxed"
            and t.on_hand[(v["variant_id"], "US")] > 0
            and t.price[(v["variant_id"], "US")] < Decimal("150.00")
            for v in t.variants[pid]
        )
    )
    assert nm["matches"] == expected and len(expected) == 2
    reasons = {e["reason"]: e["product_id"] for e in nm["non_matches"]}
    boundary = reasons["price_boundary"]
    assert {t.price[(v["variant_id"], "US")] for v in t.variants[boundary]} == {Decimal("150.00")}
    assert boundary not in nm["matches"]
    assert nm["matches_by_region_local_currency"]["CA"] == []
    # The out-of-stock near-miss really is out of stock in its relaxed fit only.
    oos = reasons["availability"]
    assert all(
        t.on_hand[(v["variant_id"], c)] == 0
        for v in t.variants[oos]
        if v["fit"] == "relaxed"
        for c in REGION_CODES
    )
    assert any(
        t.on_hand[(v["variant_id"], "US")] > 0 for v in t.variants[oos] if v["fit"] == "regular"
    )


BROAD = re.compile(r"(?=.*chest)(?=.*shoulder)(?=.*broad)", re.IGNORECASE | re.DOTALL)


@pytest.mark.req("FOUND-1")
def test_wide_frame_is_retrievable_from_text_and_athletic_is_not_wide(
    t: Tables, key: dict[str, Any]
) -> None:
    guide = t.docs["SG-OUTERWEAR"]["body"]
    relaxed_line = next(line for line in guide.splitlines() if "Relaxed fit" in line)
    assert BROAD.search(relaxed_line + guide)
    nm = _nm(key, "TRAP-NM-A")
    for pid in nm["matches"]:
        assert BROAD.search(t.products[pid]["description"]), pid
    athletic = [
        e["product_id"]
        for e in nm["non_matches"]
        if e["reason"] == "fit" and "athletic" in e["note"]
    ]
    assert len(athletic) == 1
    p = t.products[athletic[0]]
    assert p["fits"] == ["athletic"]
    assert "athletic cut" in p["description"].lower()
    assert "broad" not in p["description"].lower()
    water = [e["product_id"] for e in nm["non_matches"] if e["reason"] == "water_protection"]
    assert t.products[water[0]]["attributes"]["water_protection"] == "water_resistant"


@pytest.mark.req("FOUND-1")
def test_wool_throw_and_organic_sheet_sets_match_the_tables(t: Tables, key: dict[str, Any]) -> None:
    b = _nm(key, "TRAP-NM-B")
    assert b["matches"] == sorted(
        pid
        for pid, p in t.products.items()
        if p["subcategory"] == "throw"
        and p["attributes"].get("care") == "machine_washable"
        and p["attributes"]["material"].startswith("100%")
        and "wool" in p["attributes"]["material"]
        and any(t.price[(v["variant_id"], "US")] < 80 for v in t.variants[pid])
    )
    c = _nm(key, "TRAP-NM-C")
    assert c["matches"] == sorted(
        pid
        for pid, p in t.products.items()
        if p["subcategory"] == "sheets"
        and p["attributes"]["material"].startswith("100% organic cotton")
        and p["attributes"]["pocket_depth_in"] >= 16
        and any(v["size"] == "Queen" for v in t.variants[pid])
    )
    assert "Deep pockets" in t.docs["SG-BEDDING"]["body"]


@pytest.mark.req("FOUND-1")
def test_variant_traps_match_variants_and_stock(t: Tables, key: dict[str, Any]) -> None:
    def find(pid: str, colour: str, size: str, fit: str | None) -> Record | None:
        return next(
            (
                v
                for v in t.variants[pid]
                if v["colour"] == colour and v["size"] == size and (fit is None or v["fit"] == fit)
            ),
            None,
        )

    def stocked(v: Record) -> bool:
        return sum(t.on_hand[(v["variant_id"], c)] for c in REGION_CODES) > 0

    seen = set()
    for e in key["variant_traps"]:
        seen.add(e["expected"])
        if e["expected"] == "ambiguous":
            found = {c: find(e["product_id"], c, e["size"], None) for c in e["candidates"]}
            assert len(found) >= 2
            assert any(v is not None for v in found.values())
            assert any(v is None for v in found.values())
            continue
        v = find(e["product_id"], e["colour"], e["size"], e["fit"])
        if e["expected"] == "no":
            assert v is None, e["trap_id"]
        else:
            assert v is not None and v["variant_id"] == e["variant_id"], e["trap_id"]
            assert stocked(v) == (e["expected"] == "yes"), e["trap_id"]
    assert seen == {"yes", "no", "ambiguous", "exists_out_of_stock"}


@pytest.mark.req("FOUND-1")
@pytest.mark.req("RAG-08")
def test_missing_attributes_are_absent_everywhere_but_present_on_siblings(
    ds: Dataset, t: Tables, key: dict[str, Any]
) -> None:
    for e in key["missing_attributes"]:
        p = t.products[e["product_id"]]
        assert e["attribute"] not in p["attributes"], e["trap_id"]
        assert not re.search(e["absent_text_pattern"], t.text_of(e["product_id"]), re.IGNORECASE), (
            e["trap_id"]
        )
        siblings = [
            q
            for q in t.products.values()
            if q["subcategory"] == p["subcategory"] and q["product_id"] != p["product_id"]
        ]
        assert any(e["attribute"] in q["attributes"] for q in siblings), e["trap_id"]
    corpus = "\n".join(s for _, rec in ds.ingestible_records() for _, s in iter_strings(rec))
    for e in key["never_stated_attributes"]:
        assert not re.search(e["absent_text_pattern"], corpus, re.IGNORECASE), e["trap_id"]


@pytest.mark.req("FOUND-1")
def test_contradicting_reviews_sit_on_products_whose_spec_says_otherwise(
    t: Tables, key: dict[str, Any]
) -> None:
    for e in key["contradictions"]:
        review = t.review_by_id[e["review_id"]]
        p = t.products[e["product_id"]]
        assert review["product_id"] == e["product_id"]
        assert p["attributes"][e["attribute"]] == e["spec_value"] is not None


@pytest.mark.req("FOUND-1")
def test_answer_key_content_never_leaks_into_ingestible_files(ds: Dataset) -> None:
    for name in ds.manifest["ingestible"]:
        text = ds.files[name].decode("utf-8")
        assert "TRAP-" not in text and "never_ingest" not in text and "_answer_key" not in text
