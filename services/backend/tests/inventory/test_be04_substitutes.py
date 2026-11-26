"""BE-04 (row #25): substitutes on a stock-out are built by code only. Same department and
subcategory, in stock for the quantity, price within +-20% of the short variant's live price, a
fixed order, at most 3 and one per product; internal products never (hard rule 7); and nothing
in the output is a price or a stock figure (hard rule 2). Run on the committed fixtures and on small
hand-built catalogs."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.errors import ToolError
from northwind_backend.fakes.catalog import CatalogState
from northwind_backend.inventory import (
    InventoryFake,
    InventoryState,
    Line,
    StockOut,
    Substitute,
)
from northwind_backend.inventory.substitutes import (
    MAX_SUBSTITUTES,
    TIERS,
    SubstituteCatalog,
    find,
)

pytestmark = pytest.mark.req("BE-04")

REPO = Path(__file__).resolve().parents[4]
FIXTURES = REPO / "data" / "fixtures"
PLENTY: Callable[[str], int] = lambda variant: 10**6  # noqa: E731


# ---- a small hand-built catalog ------------------------------------------------------------


@dataclass
class Prod:
    pid: str
    department: str = "Jackets"
    subcategory: str = "rain_shell"
    visibility: str = "public"
    attributes: dict[str, Any] = field(default_factory=lambda: {"material": "nylon"})


@dataclass
class Var:
    vid: str
    pid: str
    colour: str = "Black"
    size: str = "M"
    fit: str = "regular"
    visibility: str = "public"


def _write(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def build(
    root: Path,
    products: Sequence[Prod],
    variants: Sequence[Var],
    prices: Mapping[str, str],
    region: str = "US",
) -> tuple[SubstituteCatalog, CatalogState]:
    root.mkdir(parents=True, exist_ok=True)
    _write(
        root / "products.jsonl",
        [
            {
                "product_id": p.pid,
                "department": p.department,
                "subcategory": p.subcategory,
                "visibility": p.visibility,
                "attributes": p.attributes,
            }
            for p in products
        ],
    )
    _write(
        root / "variants.jsonl",
        [
            {
                "variant_id": v.vid,
                "product_id": v.pid,
                "colour": v.colour,
                "size": v.size,
                "fit": v.fit,
                "visibility": v.visibility,
            }
            for v in variants
        ],
    )
    owner = {v.vid: v.pid for v in variants}
    _write(
        root / "prices.jsonl",
        [
            {
                "variant_id": vid,
                "product_id": owner[vid],
                "region": region,
                "currency": "USD",
                "amount": amount,
            }
            for vid, amount in prices.items()
        ],
    )
    _write(root / "stock.jsonl", [])
    return SubstituteCatalog.load(root), CatalogState(root)


WANTED = "P0001-BLK-M"


def ids(found: Sequence[Substitute]) -> list[str]:
    return [s.variant_id for s in found]


def one_candidate(
    tmp_path: Path,
    *,
    price: str = "100.00",
    department: str = "Jackets",
    subcategory: str = "rain_shell",
    visibility: str = "public",
    stock: int = 5,
    quantity: int = 1,
    region: str = "US",
) -> list[str]:
    """Whether a lone candidate survives, given how it differs from the short variant."""
    cat, prices = build(
        tmp_path,
        [Prod("P0001"), Prod("P0002", department, subcategory, visibility)],
        [Var(WANTED, "P0001"), Var("P0002-BLK-M", "P0002", visibility=visibility)],
        {WANTED: "100.00", "P0002-BLK-M": price},
    )
    found = find(
        WANTED,
        region,
        quantity,
        lambda v: stock if v == "P0002-BLK-M" else 0,
        catalog=cat,
        prices=prices,
    )
    return ids(found)


# ---- department, subcategory, price band, stock --------------------------------------------


def test_a_candidate_in_the_same_department_and_subcategory_is_offered(tmp_path: Path) -> None:
    assert one_candidate(tmp_path) == ["P0002-BLK-M"]


@pytest.mark.parametrize(
    ("department", "subcategory"),
    [
        ("Jackets", "insulated_jacket"),  # same department, other subcategory
        ("Pants", "rain_shell"),  # same subcategory name, other department
        ("Pants", "pants"),
    ],
)
def test_a_candidate_in_another_department_or_subcategory_is_never_offered(
    tmp_path: Path, department: str, subcategory: str
) -> None:
    assert one_candidate(tmp_path, department=department, subcategory=subcategory) == []


@pytest.mark.parametrize(
    ("price", "offered"),
    [
        ("100.00", True),
        ("120.00", True),  # +20% exactly is inside the band
        ("80.00", True),  # -20% exactly is inside the band
        ("120.01", False),
        ("79.99", False),
        ("200.00", False),
        ("10.00", False),
    ],
)
def test_the_price_band_is_plus_or_minus_twenty_percent_of_the_short_variants_price(
    tmp_path: Path, price: str, offered: bool
) -> None:
    assert (one_candidate(tmp_path, price=price) == ["P0002-BLK-M"]) is offered


@pytest.mark.parametrize(
    ("stock", "quantity", "offered"),
    [(5, 5, True), (5, 6, False), (0, 1, False), (1, 1, True), (4, 5, False)],
)
def test_a_candidate_must_be_in_stock_for_the_quantity_wanted(
    tmp_path: Path, stock: int, quantity: int, offered: bool
) -> None:
    assert (one_candidate(tmp_path, stock=stock, quantity=quantity) == ["P0002-BLK-M"]) is offered


def test_a_candidate_with_no_price_in_the_region_is_not_offered(tmp_path: Path) -> None:
    cat, prices = build(
        tmp_path,
        [Prod("P0001"), Prod("P0002")],
        [Var(WANTED, "P0001"), Var("P0002-BLK-M", "P0002")],
        {WANTED: "100.00"},  # P0002-BLK-M has no price at all
    )
    assert find(WANTED, "US", 1, PLENTY, catalog=cat, prices=prices) == ()


def test_a_short_variant_with_no_price_in_the_region_or_unknown_gets_no_substitutes(
    tmp_path: Path,
) -> None:
    cat, prices = build(
        tmp_path,
        [Prod("P0001"), Prod("P0002")],
        [Var(WANTED, "P0001"), Var("P0002-BLK-M", "P0002")],
        {WANTED: "100.00", "P0002-BLK-M": "100.00"},
    )
    assert find(WANTED, "CA", 1, PLENTY, catalog=cat, prices=prices) == ()
    assert find("P9999-NOPE", "US", 1, PLENTY, catalog=cat, prices=prices) == ()


def test_the_short_variant_itself_is_never_its_own_substitute(tmp_path: Path) -> None:
    cat, prices = build(
        tmp_path,
        [Prod("P0001")],
        [Var(WANTED, "P0001"), Var("P0001-RED-M", "P0001", colour="Red")],
        {WANTED: "100.00", "P0001-RED-M": "100.00"},
    )
    assert ids(find(WANTED, "US", 1, PLENTY, catalog=cat, prices=prices)) == ["P0001-RED-M"]


# ---- internal products (hard rule 7) -------------------------------------------------------


def test_an_internal_product_is_never_offered_even_in_stock_and_in_the_band(
    tmp_path: Path,
) -> None:
    assert one_candidate(tmp_path, visibility="internal") == []


def test_an_internal_variant_of_a_public_product_is_never_offered(tmp_path: Path) -> None:
    cat, prices = build(
        tmp_path,
        [Prod("P0001")],
        [Var(WANTED, "P0001"), Var("P0001-RED-M", "P0001", colour="Red", visibility="internal")],
        {WANTED: "100.00", "P0001-RED-M": "100.00"},
    )
    assert find(WANTED, "US", 1, PLENTY, catalog=cat, prices=prices) == ()


def test_an_internal_short_variant_gets_no_substitutes(tmp_path: Path) -> None:
    cat, prices = build(
        tmp_path,
        [Prod("P0001", visibility="internal"), Prod("P0002")],
        [Var(WANTED, "P0001", visibility="internal"), Var("P0002-BLK-M", "P0002")],
        {WANTED: "100.00", "P0002-BLK-M": "100.00"},
    )
    assert find(WANTED, "US", 1, PLENTY, catalog=cat, prices=prices) == ()


def test_the_real_internal_product_never_appears_and_gets_none() -> None:
    internal = {
        row["product_id"]
        for row in _jsonl(FIXTURES / "products.jsonl")
        if row["visibility"] != "public"
    }
    assert internal, "the fixtures no longer hold an internal product to test against"
    cat = SubstituteCatalog.load(FIXTURES)
    assert not internal & set(cat.products)
    internal_variants = {
        row["variant_id"]
        for row in _jsonl(FIXTURES / "variants.jsonl")
        if row["product_id"] in internal
    }
    assert not internal_variants & set(cat.variants)
    for variant in internal_variants:
        assert find(variant, "US", 1, PLENTY, catalog=cat) == ()
    for wanted in sorted(cat.variants)[::25]:
        assert not internal_variants & set(ids(find(wanted, "US", 1, PLENTY, catalog=cat)))


# ---- order, determinism, limits ------------------------------------------------------------


def _ranking_world(tmp_path: Path, *, reverse: bool = False, with_other_colour: bool = True) -> Any:
    products = [Prod("P0001"), Prod("P0002"), Prod("P0003"), Prod("P0004")]
    variants = [
        Var(WANTED, "P0001"),
        Var("P0001-BLK-L", "P0001", size="L"),
        Var("P0002-BLK-M", "P0002"),
        Var("P0003-BLK-L", "P0003", size="L"),
        Var("P0004-BLK-L", "P0004", size="L"),
    ]
    if with_other_colour:
        variants.append(Var("P0001-RED-M", "P0001", colour="Red"))
    prices = {
        WANTED: "100.00",
        "P0001-BLK-L": "100.00",
        "P0001-RED-M": "100.00",
        "P0002-BLK-M": "115.00",
        "P0003-BLK-L": "90.00",
        "P0004-BLK-L": "95.00",
    }
    known = {v.vid for v in variants}
    prices = {vid: amount for vid, amount in prices.items() if vid in known}
    if reverse:
        products.reverse()
        variants.reverse()
        prices = dict(reversed(list(prices.items())))
    return build(tmp_path, products, variants, prices)


def test_substitutes_come_in_the_fixed_tier_order(tmp_path: Path) -> None:
    cat, prices = _ranking_world(tmp_path)
    found = find(WANTED, "US", 1, PLENTY, catalog=cat, prices=prices)
    # Same product in another colour, then another product in the same size and fit, then other
    # products in other sizes: closer price first.
    assert ids(found) == ["P0001-RED-M", "P0002-BLK-M", "P0004-BLK-L"]
    assert [s.reason for s in found] == [TIERS[0], TIERS[1], TIERS[3]]


def test_the_same_product_in_another_size_ranks_after_another_product_in_the_same_size(
    tmp_path: Path,
) -> None:
    cat, prices = _ranking_world(tmp_path, with_other_colour=False)
    found = find(WANTED, "US", 1, PLENTY, catalog=cat, prices=prices)
    assert ids(found) == ["P0002-BLK-M", "P0001-BLK-L", "P0004-BLK-L"]
    assert [s.reason for s in found[:2]] == [TIERS[1], TIERS[2]]


def test_ranking_does_not_depend_on_the_order_of_the_catalog_files(tmp_path: Path) -> None:
    cat, prices = _ranking_world(tmp_path / "a")
    flipped_cat, flipped_prices = _ranking_world(tmp_path / "b", reverse=True)
    one = find(WANTED, "US", 1, PLENTY, catalog=cat, prices=prices)
    two = find(WANTED, "US", 1, PLENTY, catalog=flipped_cat, prices=flipped_prices)
    assert one == two


def test_the_same_call_gives_the_same_answer_every_time(tmp_path: Path) -> None:
    cat, prices = _ranking_world(tmp_path)
    runs = {find(WANTED, "US", 1, PLENTY, catalog=cat, prices=prices) for _ in range(5)}
    assert len(runs) == 1


def test_more_matching_attributes_rank_first_then_the_smaller_price_gap_then_the_variant_id(
    tmp_path: Path,
) -> None:
    same = {"material": "nylon", "hood": "yes"}
    products = [
        Prod("P0001", attributes=same),
        Prod("P0002", attributes={"material": "nylon", "hood": "no"}),  # one match
        Prod("P0003", attributes=same),  # two matches, bigger gap
        Prod("P0004", attributes={"material": "nylon", "hood": "no"}),  # one match, smaller gap
        Prod("P0005", attributes={"material": "nylon", "hood": "no"}),  # tie with P0004
    ]
    variants = [Var(WANTED, "P0001")] + [Var(f"P000{n}-BLK-M", f"P000{n}") for n in range(2, 6)]
    cat, prices = build(
        tmp_path,
        products,
        variants,
        {
            WANTED: "100.00",
            "P0002-BLK-M": "101.00",
            "P0003-BLK-M": "115.00",
            "P0004-BLK-M": "102.00",
            "P0005-BLK-M": "98.00",
        },
    )
    found = find(WANTED, "US", 1, PLENTY, catalog=cat, prices=prices)
    assert ids(found) == ["P0003-BLK-M", "P0002-BLK-M", "P0004-BLK-M"]
    assert "P0005-BLK-M" not in ids(found)  # 4th: the cap, not a filter
    # Gap 2.00 each way: the variant ID breaks the tie, the smaller ID first.
    cat2, prices2 = build(
        tmp_path / "tie",
        [Prod("P0001"), Prod("P0004"), Prod("P0005")],
        [Var(WANTED, "P0001"), Var("P0004-BLK-M", "P0004"), Var("P0005-BLK-M", "P0005")],
        {WANTED: "100.00", "P0004-BLK-M": "102.00", "P0005-BLK-M": "98.00"},
    )
    assert ids(find(WANTED, "US", 1, PLENTY, catalog=cat2, prices=prices2)) == [
        "P0004-BLK-M",
        "P0005-BLK-M",
    ]


def test_at_most_three_substitutes_and_at_most_one_per_product(tmp_path: Path) -> None:
    products = [Prod("P0001")] + [Prod(f"P000{n}") for n in range(2, 8)]
    variants = [Var(WANTED, "P0001")]
    for n in range(2, 8):
        variants += [
            Var(f"P000{n}-BLK-M", f"P000{n}"),
            Var(f"P000{n}-RED-M", f"P000{n}", colour="Red"),
        ]
    cat, prices = build(tmp_path, products, variants, {v.vid: "100.00" for v in variants})
    found = find(WANTED, "US", 1, PLENTY, catalog=cat, prices=prices)
    assert MAX_SUBSTITUTES == 3 and len(found) == 3
    assert len({s.product_id for s in found}) == 3


def test_a_product_with_many_variants_in_the_band_is_offered_once(tmp_path: Path) -> None:
    cat, prices = build(
        tmp_path,
        [Prod("P0001"), Prod("P0002")],
        [
            Var(WANTED, "P0001"),
            Var("P0002-BLK-M", "P0002"),
            Var("P0002-BLK-L", "P0002", size="L"),
            Var("P0002-RED-M", "P0002", colour="Red"),
        ],
        {v: "100.00" for v in (WANTED, "P0002-BLK-M", "P0002-BLK-L", "P0002-RED-M")},
    )
    found = find(WANTED, "US", 1, PLENTY, catalog=cat, prices=prices)
    assert len(found) == 1 and found[0].product_id == "P0002"


def test_a_product_whose_best_variant_is_out_of_stock_offers_its_next_best(
    tmp_path: Path,
) -> None:
    cat, prices = build(
        tmp_path,
        [Prod("P0001"), Prod("P0002")],
        [Var(WANTED, "P0001"), Var("P0002-BLK-M", "P0002"), Var("P0002-BLK-L", "P0002", size="L")],
        {v: "100.00" for v in (WANTED, "P0002-BLK-M", "P0002-BLK-L")},
    )
    found = find(
        WANTED, "US", 1, lambda v: 0 if v == "P0002-BLK-M" else 9, catalog=cat, prices=prices
    )
    assert ids(found) == ["P0002-BLK-L"]


# ---- the real fixtures ---------------------------------------------------------------------


def _jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _sample_us_variants() -> list[str]:
    rows = [r for r in _jsonl(FIXTURES / "prices.jsonl") if r["region"] == "US"]
    return sorted(r["variant_id"] for r in rows)[::9]


@pytest.fixture(scope="module")
def real() -> dict[str, Any]:
    products = {r["product_id"]: r for r in _jsonl(FIXTURES / "products.jsonl")}
    variants = {r["variant_id"]: r for r in _jsonl(FIXTURES / "variants.jsonl")}
    prices = {
        r["variant_id"]: Decimal(r["amount"])
        for r in _jsonl(FIXTURES / "prices.jsonl")
        if r["region"] == "US"
    }
    return {"products": products, "variants": variants, "prices": prices}


def test_on_the_real_fixtures_every_substitute_meets_every_rule(real: dict[str, Any]) -> None:
    products, variants, prices = real["products"], real["variants"], real["prices"]
    cat = SubstituteCatalog.load(FIXTURES)
    offered_somewhere = 0
    for wanted in _sample_us_variants():
        found = find(wanted, "US", 1, PLENTY, catalog=cat)
        base_product = products[variants[wanted]["product_id"]]
        assert len(found) <= 3
        assert len({s.product_id for s in found}) == len(found)
        for s in found:
            assert s.variant_id != wanted
            other = products[s.product_id]
            assert variants[s.variant_id]["product_id"] == s.product_id
            assert (
                other["visibility"] == "public" and variants[s.variant_id]["visibility"] == "public"
            )
            assert (other["department"], other["subcategory"]) == (
                base_product["department"],
                base_product["subcategory"],
            )
            gap = abs(prices[s.variant_id] - prices[wanted])
            assert gap <= prices[wanted] * Decimal("0.20")
        offered_somewhere += bool(found)
    assert offered_somewhere, (
        "no sampled variant got any substitute: the fixtures or the rule changed"
    )


def test_on_the_real_fixtures_a_substitute_that_is_out_of_stock_is_dropped(
    real: dict[str, Any],
) -> None:
    cat = SubstituteCatalog.load(FIXTURES)
    wanted = _sample_us_variants()[0]
    everything = find(wanted, "US", 1, PLENTY, catalog=cat)
    assert everything
    banned = {s.variant_id for s in everything}
    without = find(wanted, "US", 1, lambda v: 0 if v in banned else 10, catalog=cat)
    assert not banned & set(ids(without))


def test_on_the_real_fixtures_the_answer_is_the_same_every_time() -> None:
    cat = SubstituteCatalog.load(FIXTURES)
    for wanted in _sample_us_variants()[:20]:
        assert find(wanted, "US", 2, PLENTY, catalog=cat) == find(
            wanted, "US", 2, PLENTY, catalog=cat
        )


# ---- the stock-out envelope: no price, no stock (hard rule 2) ------------------------------


def _short_real_variant() -> tuple[str, list[dict[str, Any]]]:
    """A real US variant whose stock is zero everywhere, with the fixtures' stock for the rest."""
    wanted = _sample_us_variants()[0]
    rows = [r for r in _jsonl(FIXTURES / "stock.jsonl") if r["region"] == "US"]
    for row in rows:
        if row["variant_id"] == wanted:
            row["on_hand"] = 0
    return wanted, rows


def _stock_out() -> tuple[str, StockOut]:
    wanted, rows = _short_real_variant()
    inv = InventoryFake(
        lambda: datetime(2026, 11, 26, 12, 0, tzinfo=UTC), state=InventoryState.from_rows(rows)
    )
    refused = inv.reserve(
        idempotency_key="key-1",
        order_id="O000001",
        region="US",
        postal_code="10001",
        lines=[Line(wanted, 1)],
    )
    assert isinstance(refused, ToolError) and isinstance(refused, StockOut), refused
    return wanted, refused


def test_a_stock_out_carries_substitutes_for_the_short_variant() -> None:
    wanted, refused = _stock_out()
    assert refused.code == "out_of_stock" and refused.error_category.value == "business"
    subs = refused.to_envelope()["substitutes"]
    assert list(subs) == [wanted] and 0 < len(subs[wanted]) <= 3


FORBIDDEN = (
    "price",
    "amount",
    "cost",
    "currency",
    "stock",
    "on_hand",
    "available",
    "quantity",
    "count",
)


def _keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k in value] + [k for v in value.values() for k in _keys(v)]
    if isinstance(value, list):
        return [k for v in value for k in _keys(v)]
    return []


def test_the_substitutes_in_a_stock_out_envelope_have_no_price_or_stock_field() -> None:
    _, refused = _stock_out()
    substitutes = refused.to_envelope()["substitutes"]
    candidates = [c for found in substitutes.values() for c in found]
    assert candidates
    for candidate in candidates:
        assert set(candidate) == {"variant_id", "product_id", "reason"}
        assert candidate["reason"] in TIERS
    keys = [k.lower() for k in _keys(substitutes)]
    assert not [k for k in keys if any(bad in k for bad in FORBIDDEN)]


def test_no_substitute_text_carries_a_price_or_a_stock_figure() -> None:
    wanted, refused = _stock_out()
    prices = _jsonl(FIXTURES / "prices.jsonl")
    text = json.dumps(refused.to_envelope()["substitutes"])
    for row in prices:
        if row["variant_id"] in text:
            assert row["amount"] not in text, "a live price reached the substitutes"


def test_a_stock_out_with_nothing_to_offer_says_so_with_an_empty_list_not_an_error() -> None:
    wanted = "P9999-ZZZ"
    inv = InventoryFake(
        lambda: datetime(2026, 11, 26, 12, 0, tzinfo=UTC),
        state=InventoryState.from_rows(
            [{"variant_id": wanted, "node": "DC-EAST", "region": "US", "on_hand": 0}]
        ),
    )
    refused = inv.reserve(
        idempotency_key="key-1",
        order_id="O000001",
        region="US",
        postal_code="10001",
        lines=[Line(wanted, 1)],
    )
    assert isinstance(refused, StockOut)
    assert refused.to_envelope()["substitutes"] == {wanted: []}
