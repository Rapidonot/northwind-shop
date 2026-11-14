"""Row #32 (RAG-12): the grown 300-item eval set. Written before the 198 new items (EV-103..EV-300)
exist, from docs/reviews/2026-11-14-32-plan.md sections 2 and 3."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from northwind_evals.items import EvalItem, load_items

REPO = Path(__file__).resolve().parents[2]
SET_PATH = REPO / "evals/fixtures/starter_set.jsonl"
FIXTURES = REPO / "data/fixtures"
ITEMS = load_items(SET_PATH)

OLD_COUNT = 102
OLD_HASH = "e3d6d6f9119ca5281c9e8846661d683380bbd5325987310080617713e57fd404"
NEW = ITEMS[OLD_COUNT:]
BY_ID = {item.id: item for item in ITEMS}

# (first id, last id, segment), inclusive, from the plan's ID-layout table.
LAYOUT = (
    (103, 152, "specification"),
    (153, 177, "comparison"),
    (178, 202, "intent_filter"),
    (203, 224, "variant"),
    (225, 250, "policy"),
    (251, 264, "ambiguous"),
    (265, 282, "unanswerable"),
    (283, 300, "adversarial"),
)

_PRODUCT_SOURCE = re.compile(r"^(P\d{4})(?:-|$)")


def _eid(n: int) -> str:
    return f"EV-{n:03d}"


def _records(name: str) -> list[dict[str, Any]]:
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def _product_of(source: str) -> str | None:
    match = _PRODUCT_SOURCE.match(source)
    return match.group(1) if match else None


def _segment_range(segment: str) -> list[str]:
    return [_eid(n) for lo, hi, seg in LAYOUT if seg == segment for n in range(lo, hi + 1)]


@pytest.mark.req("RAG-12")
def test_first_102_lines_are_byte_identical_to_main() -> None:
    raw = SET_PATH.read_bytes().splitlines(keepends=True)
    head = b"".join(raw[:OLD_COUNT])
    assert len(raw) >= OLD_COUNT
    assert hashlib.sha256(head).hexdigest() == OLD_HASH


@pytest.mark.req("RAG-12")
def test_there_are_300_items_and_the_new_ids_run_ev103_to_ev300_in_order() -> None:
    assert len(ITEMS) == 300
    assert [item.id for item in NEW] == [_eid(n) for n in range(103, 301)]


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize(("lo", "hi", "segment"), LAYOUT)
def test_new_items_follow_the_id_layout(lo: int, hi: int, segment: str) -> None:
    wrong = []
    for n in range(lo, hi + 1):
        item = BY_ID.get(_eid(n))
        if item is None:
            wrong.append(f"{_eid(n)}: missing")
        elif item.segment != segment:
            wrong.append(f"{item.id}: segment {item.segment}, want {segment}")
    assert not wrong, wrong


@pytest.mark.req("RAG-12")
def test_new_items_hold_exactly_18_unanswerable_and_18_adversarial() -> None:
    assert sum(item.segment == "unanswerable" for item in NEW) == 18
    assert sum(item.segment == "adversarial" for item in NEW) == 18


@pytest.mark.req("RAG-12")
def test_no_new_item_uses_or_names_a_table_linen_product_or_says_tablecloth() -> None:
    assert NEW, "no new items yet"
    products = _records("products.jsonl")
    subcategory = {p["product_id"]: p["subcategory"] for p in products}
    linen_names = [p["name"].lower() for p in products if p["subcategory"] == "table_linen"]
    bad = []
    for item in NEW:
        bad += [f"{item.id}: query names {n}" for n in linen_names if n in item.query.lower()]
        for source in item.all_sources:
            pid = _product_of(source)
            if pid is not None and subcategory.get(pid) == "table_linen":
                bad.append(f"{item.id}: {source} is table_linen")
        if "tablecloth" in item.query.lower():
            bad.append(f"{item.id}: query says tablecloth")
    assert not bad, bad


# Plan section 2: (product id or None for "never stated anywhere", absence regex).
UNANSWERABLE: dict[str, tuple[str | None, str]] = {
    "EV-265": ("P0005", r"\bmm\b|hydrostatic|waterproof rating"),
    "EV-266": ("P0005", r"seam"),
    "EV-267": ("P0071", r"pocket"),
    **{
        eid: (pid, r"\bUPF\b|sun protection")
        for eid, pid in zip(
            ("EV-268", "EV-269", "EV-270", "EV-271", "EV-272"),
            ("P0087", "P0091", "P0094", "P0095", "P0100"),
            strict=True,
        )
    },
    **{
        eid: (pid, r"thread[- ]count|\b\d+\s?tc\b")
        for eid, pid in zip(
            ("EV-274", "EV-275", "EV-276", "EV-277"),
            ("P0129", "P0131", "P0133", "P0136"),
            strict=True,
        )
    },
    "EV-273": ("P0029", r"temperature|degrees|comfort rating|°|rated to"),
    "EV-278": ("P0137", r"loop|ties\b|corner"),
    "EV-279": (None, r"bluesign"),
    "EV-280": (None, r"\bGOTS\b"),
    "EV-281": ("P0164", r"mules"),
    "EV-282": (None, r"flame|fire[- ]?(retardant|resistant)"),
}


@pytest.mark.req("RAG-12")
def test_every_unanswerable_id_has_a_regex() -> None:
    assert set(UNANSWERABLE) == set(_segment_range("unanswerable"))


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("item_id", sorted(UNANSWERABLE))
def test_unanswerable_attribute_is_absent_from_the_fixtures(item_id: str) -> None:
    product, pattern = UNANSWERABLE[item_id]
    regex = re.compile(pattern, re.IGNORECASE)
    item = BY_ID.get(item_id)
    assert item is not None, f"{item_id} does not exist yet"
    assert item.segment == "unanswerable", item_id
    assert item.expected_behaviour == "abstain", item_id

    if product is not None:
        assert product in item.all_sources, f"{item_id}: {product} not in relevant_sources"
        texts = (
            [json.dumps(r) for r in _records("products.jsonl") if r["product_id"] == product]
            + [json.dumps(r) for r in _records("variants.jsonl") if r["product_id"] == product]
            + [json.dumps(r) for r in _records("reviews.jsonl") if r["product_id"] == product]
        )
        assert texts, f"{product} has no fixture text"
    else:
        manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        texts = []
        for name in manifest["ingestible"]:
            texts.extend(json.dumps(r) for r in _records(name))
    hits = [t[max(0, m.start() - 30) : m.end() + 30] for t in texts if (m := regex.search(t))]
    assert not hits, f"{item_id} /{pattern}/ found in fixtures: {hits[:3]}"


_MONEY = re.compile(
    r"[$£€]\s?\d|\d\s?(?:USD|GBP|EUR|CAD|dollars?|pounds?|euros?)\b",
    re.IGNORECASE,
)
_STOCK = re.compile(r"\d+\s+(?:in stock|left|units?|available)\b", re.IGNORECASE)


@pytest.mark.req("RAG-12")
def test_new_reference_facts_hold_no_price_stock_or_product_page() -> None:
    assert NEW, "no new items yet"
    bad = []
    for item in NEW:
        for fact in item.reference_facts:
            if _MONEY.search(fact):
                bad.append(f"{item.id}: currency amount in {fact!r}")
            if _STOCK.search(fact):
                bad.append(f"{item.id}: stock count in {fact!r}")
            if "product page" in fact.lower():
                bad.append(f"{item.id}: 'product page' in {fact!r}")
    assert not bad, bad


PLANTED_PRODUCTS = (
    "P0001",
    "P0006",
    "P0020",
    "P0052",
    "P0071",
    "P0088",
    "P0121",
    "P0149",
    "P0178",
    "P0156",
)


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("product", PLANTED_PRODUCTS)
def test_planted_review_product_has_two_spec_items(product: str) -> None:
    ids = _segment_range("specification")[:20]  # EV-103..EV-122
    assert ids[0] == "EV-103"
    assert ids[-1] == "EV-122"
    found = [
        BY_ID[i].id
        for i in ids
        if i in BY_ID
        and BY_ID[i].segment == "specification"
        and any(_product_of(s) == product for s in BY_ID[i].all_sources)
    ]
    assert len(found) >= 2, f"{product}: only {found} among EV-103..EV-122"


@pytest.mark.req("RAG-12")
def test_new_policy_items_only_label_documents_for_their_own_region() -> None:
    docs = {d["doc_id"]: d for d in _records("documents.jsonl")}
    policy_ids = _segment_range("policy")
    missing = [i for i in policy_ids if i not in BY_ID]
    assert not missing, f"policy items missing: {missing}"
    bad = []
    for item_id in policy_ids:
        item: EvalItem = BY_ID[item_id]
        for source in item.all_sources:
            doc = docs.get(source)
            if doc is None:
                continue
            regional = str(doc["doc_type"]).startswith("policy_") or doc["doc_type"] == "faq"
            if regional and doc["region"] != "ALL" and doc["region"] != item.region:
                bad.append(f"{item.id}: {source} is {doc['region']}, item is {item.region}")
    assert not bad, bad
