"""Row #18, step 0 (b) and (c), written from the criteria: every stock figure the catalog gives is
the live store's region stock less BE-04's live holds (floored at 0), and the cart's alternatives
are BE-04's substitutes by the stated rules.

The holds come from BE-04's `InventoryFake` through a small adapter, so "live" is BE-04's meaning.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from northwind_backend.inventory import InventoryFake, Line
from northwind_backend.inventory import substitutes as subs
from northwind_backend.inventory.nodes import region_nodes
from northwind_common.catalog_tool import labelled_result, unlabel
from northwind_common.errors import ENVELOPE_KEYS
from northwind_common.mcp_token import CATALOG_SERVER, Claims
from northwind_mcp_catalog import cart, tools
from northwind_mcp_catalog.holds import NoHolds, subtract, unavailable
from northwind_mcp_catalog.live import FixtureCatalog, eligible_products
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.testing import KEY, call, running, token

pytestmark = pytest.mark.req("BE-04")

REGION = "US"  # DC-EAST + DC-WEST
V = "P0001-BLK-REG-M"
T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class FakeHolds:
    """BE-04's `InventoryFake` as the catalog's `Holds` port."""

    def __init__(self, inventory: InventoryFake) -> None:
        self.inventory = inventory

    def live(self, region: str) -> Mapping[str, int]:
        nodes = set(region_nodes().get(region, ()))
        held: dict[str, int] = {}
        for (variant, node), row in self.inventory.state.rows.items():
            if node in nodes:
                avail = self.inventory.available(variant, node)
                units = row.on_hand - avail.value  # type: ignore[union-attr]
                if units:
                    held[variant] = held.get(variant, 0) + units
        return held


class Fixed:
    def __init__(self, held: Mapping[str, int]) -> None:
        self.held = dict(held)

    def live(self, region: str) -> Mapping[str, int]:
        return self.held


class Broken:
    def live(self, region: str) -> Mapping[str, int]:
        raise unavailable()


def claims(
    *, aud: str = "shopper", region: str | None = REGION, sid: str | None = "s-tw"
) -> Claims:
    return Claims(
        aud=aud,  # type: ignore[arg-type]
        region=region,
        agent="shopper-assistant" if aud == "shopper" else "catalog-subagent",
        turn="t-tw",
        exp=int(time.time()) + 600,
        srv=CATALOG_SERVER,
        sid=sid,
    )


def make(holds: Any = None) -> tuple[CatalogServer, InventoryFake]:
    inventory = InventoryFake(Clock())
    server = CatalogServer(
        FixtureCatalog(), key=KEY, tools=tools.TOOLS, holds=holds or FakeHolds(inventory)
    )
    return server, inventory


def reserve(inventory: InventoryFake, key: str, variant: str, quantity: int) -> None:
    done = inventory.reserve(
        idempotency_key=key,
        order_id="O900101",
        region=REGION,
        postal_code="10001",
        lines=[Line(variant, quantity)],
    )
    assert getattr(done, "value", None) is not None, done


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def data_of(result: Any, tool: str) -> Any:
    found = unlabel(result.content[-1].text)
    assert found is not None and found[0] == tool, result.content
    return found[1]


def add(server: CatalogServer, variant: str, quantity: int, key: str, sid: str = "s-tw") -> Any:
    return run(
        cart.add_to_cart(
            server,
            claims(sid=sid),
            {"variant_id": variant, "quantity": quantity, "idempotency_key": key},
        )
    )


def check(server: CatalogServer, variant: str = V, region: str = REGION) -> int:
    res = run(tools.check_stock(server, claims(region=region), {"variant_id": variant}))
    found = labelled_result(res.content[0].text, bool(res.is_error))
    assert found is not None
    return int(found[1]["on_hand"])


# ---- subtract --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("on_hand", "held", "expected"),
    [
        (10, {V: 3}, 7),
        (10, {}, 10),
        (3, {V: 3}, 0),
        (3, {V: 9}, 0),
        (None, {V: 1}, 0),
        (None, {}, 0),
    ],
)
def test_subtract_is_stock_less_holds_floored_at_zero(
    on_hand: int | None, held: dict[str, int], expected: int
) -> None:
    assert subtract(on_hand, held, V) == expected


def test_a_hold_on_another_variant_is_ignored() -> None:
    assert subtract(10, {"P0002-CHR-REL-2XL": 4}, V) == 10


# ---- (c) check_stock -------------------------------------------------------------------------


def test_check_stock_sums_a_multi_node_region_less_holds() -> None:
    server, inventory = make()
    total = server.catalog.stock(V, REGION)
    assert total == 157  # DC-EAST 78 + DC-WEST 79
    reserve(inventory, "tw-k1", V, 79)  # only DC-WEST has 79
    assert check(server) == 78
    reserve(inventory, "tw-k2", V, 78)  # now only DC-EAST has 78 free
    assert check(server) == 0
    assert server.on_hand(V, REGION) == 0


def test_check_stock_reports_in_stock_false_when_fully_held() -> None:
    server, _ = make(Fixed({V: 10_000}))
    res = run(tools.check_stock(server, claims(), {"variant_id": V}))
    value = data_of(res, "check_stock")
    assert value["on_hand"] == 0 and value["in_stock"] is False


def test_on_hand_with_an_explicit_held_reading_uses_it_not_the_port() -> None:
    server, _ = make(Broken())
    assert server.on_hand(V, REGION, {V: 5}) == 157 - 5


def test_the_default_server_holds_nothing_and_subtracts_nothing() -> None:
    server = CatalogServer(FixtureCatalog(), key=KEY, tools=tools.TOOLS)
    assert isinstance(server.holds, NoHolds)
    assert server.on_hand(V, REGION) == 157


def test_an_unavailable_reader_is_a_transient_retryable_error_not_a_zero() -> None:
    assert (unavailable().category, unavailable().retryable) == ("transient", True)
    server, _ = make(Broken())
    with pytest.raises(type(unavailable())) as err:
        run(tools.check_stock(server, claims(), {"variant_id": V}))
    assert err.value.category == "transient" and err.value.retryable is True


def test_an_unavailable_reader_surfaces_as_an_error_envelope_over_mcp() -> None:
    server, _ = make(Broken())
    with running(server) as url:
        bearer, _claims = token("shopper-assistant", REGION)
        is_error, body = asyncio.run(call(url, bearer, "check_stock", {"variant_id": V}))
    assert is_error is True
    assert set(body) == ENVELOPE_KEYS
    assert body["isError"] is True
    assert body["errorCategory"] == "transient"
    assert body["isRetryable"] is True
    assert "on_hand" not in body


def test_an_unavailable_reader_never_turns_a_cart_add_into_a_quiet_zero() -> None:
    server, _ = make(Broken())
    with pytest.raises(type(unavailable())):
        add(server, V, 1, "tw-add-broke")


def test_an_unavailable_reader_fails_get_cart_with_the_error_not_zero_lines() -> None:
    server, _ = make()
    add(server, V, 1, "tw-add-okay", sid="s-broken")
    server.holds = Broken()
    with pytest.raises(type(unavailable())):
        run(cart.get_cart(server, claims(sid="s-broken"), {}))


# ---- (c) cart add / set quantity -------------------------------------------------------------


def test_a_hold_turns_a_successful_add_into_an_out_of_stock_refusal() -> None:
    server, _ = make(Fixed({}))
    server.catalog.set_stock(V, REGION, 2)  # sets the store; any node layout, region total 2
    ok = add(server, V, 2, "tw-add-0001", sid="s-a1")
    assert not ok.is_error
    server.holds = Fixed({V: 2})
    refused = add(server, V, 1, "tw-add-0002", sid="s-a2")
    assert refused.is_error
    envelope = refused.content[0].text
    assert '"code": "out_of_stock"' in envelope
    assert '"errorCategory": "business"' in envelope
    block = data_of(refused, "add_to_cart")
    assert block["requested"] == {"variant_id": V, "on_hand": 0, "in_stock": False}


def test_a_partial_hold_gives_insufficient_stock_and_the_reduced_figure() -> None:
    server, _ = make(Fixed({V: 1}))
    server.catalog.set_stock(V, REGION, 3)
    refused = add(server, V, 3, "tw-add-0003", sid="s-a3")
    assert refused.is_error
    assert '"code": "insufficient_stock"' in refused.content[0].text
    block = data_of(refused, "add_to_cart")
    assert block["requested"] == {"variant_id": V, "on_hand": 2, "in_stock": True}
    allowed = add(server, V, 2, "tw-add-0004", sid="s-a3")
    assert not allowed.is_error


def test_without_the_hold_the_same_add_succeeds() -> None:
    server, _ = make(Fixed({}))
    server.catalog.set_stock(V, REGION, 3)
    assert not add(server, V, 3, "tw-add-0005", sid="s-a4").is_error


def test_an_expired_hold_lets_the_add_through() -> None:
    clock = Clock()
    inventory = InventoryFake(clock)
    server = CatalogServer(FixtureCatalog(), key=KEY, tools=tools.TOOLS, holds=FakeHolds(inventory))
    server.catalog.set_stock(V, REGION, 157)
    reserve(inventory, "tw-k3", V, 79)
    reserve(inventory, "tw-k4", V, 78)
    assert check(server) == 0
    clock.now = T0 + timedelta(minutes=15)  # at expiry: not live
    assert check(server) == 157


def test_set_cart_quantity_increase_is_refused_by_a_hold() -> None:
    server, _ = make(Fixed({}))
    server.catalog.set_stock(V, REGION, 5)
    assert not add(server, V, 1, "tw-set-0001", sid="s-set").is_error
    server.holds = Fixed({V: 3})  # 2 free now
    refused = run(
        cart.set_cart_quantity(
            server,
            claims(sid="s-set"),
            {"variant_id": V, "quantity": 3, "idempotency_key": "tw-set-0002"},
        )
    )
    assert refused.is_error
    assert '"code": "insufficient_stock"' in refused.content[0].text
    ok = run(
        cart.set_cart_quantity(
            server,
            claims(sid="s-set"),
            {"variant_id": V, "quantity": 2, "idempotency_key": "tw-set-0003"},
        )
    )
    assert not ok.is_error


def test_set_cart_quantity_decrease_is_never_refused_even_when_fully_held() -> None:
    server, _ = make(Fixed({}))
    server.catalog.set_stock(V, REGION, 5)
    assert not add(server, V, 3, "tw-dec-0001", sid="s-dec").is_error
    server.holds = Fixed({V: 5})
    ok = run(
        cart.set_cart_quantity(
            server,
            claims(sid="s-dec"),
            {"variant_id": V, "quantity": 1, "idempotency_key": "tw-dec-0002"},
        )
    )
    assert not ok.is_error


def test_get_cart_line_on_hand_reflects_holds() -> None:
    server, _ = make(Fixed({}))
    server.catalog.set_stock(V, REGION, 6)
    add(server, V, 1, "tw-get-0001", sid="s-get")
    server.holds = Fixed({V: 4})
    view = data_of(run(cart.get_cart(server, claims(sid="s-get"), {})), "get_cart")
    [line] = view["lines"]
    assert (line["on_hand"], line["in_stock"]) == (2, True)
    server.holds = Fixed({V: 6})
    view = data_of(run(cart.get_cart(server, claims(sid="s-get"), {})), "get_cart")
    [line] = view["lines"]
    assert (line["on_hand"], line["in_stock"]) == (0, False)


# ---- (c) the in_stock filter -----------------------------------------------------------------


def test_the_in_stock_filter_drops_a_product_whose_every_variant_is_held() -> None:
    catalog = FixtureCatalog()
    every = eligible_products(catalog, REGION, currency="USD", in_stock=True)
    product = every[0]
    held = {
        v.variant_id: catalog.stock(v.variant_id, REGION) or 0 for v in catalog.variants_of(product)
    }
    assert product not in eligible_products(
        catalog, REGION, currency="USD", in_stock=True, held=held
    )


def test_the_in_stock_filter_keeps_a_product_with_one_variant_left() -> None:
    catalog = FixtureCatalog()
    product = eligible_products(catalog, REGION, currency="USD", in_stock=True)[0]
    held = {
        v.variant_id: catalog.stock(v.variant_id, REGION) or 0 for v in catalog.variants_of(product)
    }
    spared = next(iter(held))
    held[spared] -= 1  # one unit left
    assert product in eligible_products(catalog, REGION, currency="USD", in_stock=True, held=held)


def test_holds_do_not_change_a_filter_without_in_stock() -> None:
    catalog = FixtureCatalog()
    plain = eligible_products(catalog, REGION, currency="USD")
    held = {
        v.variant_id: 10_000 for p in catalog.products() for v in catalog.variants_of(p.product_id)
    }
    assert eligible_products(catalog, REGION, currency="USD", held=held) == plain


# ---- (b) alternatives ------------------------------------------------------------------------

ALT_KEYS = {"variant_id", "product_id", "name", "colour", "size", "fit", "on_hand", "in_stock"}
SAMPLES = ["P0001-BLK-REG-M", "P0002-CHR-REL-2XL", "P0010-CHR-REG-L", "P0003-SLT-REL-L"]


def refuse(server: CatalogServer, variant: str) -> Any:
    server.catalog.set_stock(variant, REGION, 0)
    refused = server.catalog.variant(variant)
    assert refused is not None
    return refused


def tier(server: CatalogServer, refused: Any, alt: dict[str, Any]) -> int:
    same_product = alt["product_id"] == refused.product_id
    same_shape = (alt["size"], alt["fit"]) == (refused.size, refused.fit)
    if same_product and same_shape:
        return 0
    if same_shape:
        return 1
    if same_product:
        return 2
    return 3


@pytest.mark.parametrize("wanted", [1, 3])
@pytest.mark.parametrize("variant", SAMPLES)
def test_alternatives_obey_the_substitute_rules(variant: str, wanted: int) -> None:
    server, _ = make(Fixed({}))
    refused = refuse(server, variant)
    alts = cart.alternatives(server, claims(), REGION, refused, wanted)
    assert len(alts) <= 3 == subs.MAX_SUBSTITUTES == cart.MAX_ALTERNATIVES
    base = server.catalog.price(variant, REGION)
    assert base is not None
    product = server.catalog.product(refused.product_id)
    assert product is not None
    seen_products: set[str] = set()
    tiers: list[int] = []
    for alt in alts:
        assert set(alt) == ALT_KEYS  # shape unchanged, and no price
        v = server.catalog.variant(alt["variant_id"])
        p = server.catalog.product(alt["product_id"])
        assert v is not None and p is not None
        assert v.visibility == "public" and p.visibility == "public"
        assert (p.department, p.subcategory) == (product.department, product.subcategory)
        price = server.catalog.price(alt["variant_id"], REGION)
        assert price is not None and price.currency == base.currency
        assert abs(price.minor_units - base.minor_units) * 5 <= base.minor_units  # within +-20%
        assert alt["product_id"] not in seen_products  # one variant per product
        seen_products.add(alt["product_id"])
        assert alt["on_hand"] >= wanted and alt["in_stock"] is True
        assert alt["variant_id"] != variant
        tiers.append(tier(server, refused, alt))
    assert tiers == sorted(tiers)


def test_a_candidate_whose_holds_leave_less_than_asked_is_not_offered() -> None:
    server, _ = make(Fixed({}))
    refused = refuse(server, V)
    first = cart.alternatives(server, claims(), REGION, refused, 2)
    assert first
    top = first[0]["variant_id"]
    stock = server.catalog.stock(top, REGION) or 0
    server.holds = Fixed({top: stock - 1})  # one unit left, two wanted
    again = cart.alternatives(server, claims(), REGION, refused, 2)
    assert top not in [a["variant_id"] for a in again]
    for alt in again:
        assert alt["on_hand"] >= 2


def test_alternatives_use_the_live_price_band() -> None:
    server, _ = make(Fixed({}))
    refused = refuse(server, V)
    first = cart.alternatives(server, claims(), REGION, refused, 1)
    assert first
    server.catalog.set_price(V, REGION, "1.00", "USD")  # the requested one's price collapses live
    again = cart.alternatives(server, claims(), REGION, refused, 1)
    assert not {a["variant_id"] for a in again} & {a["variant_id"] for a in first}


def test_a_candidate_in_another_currency_is_not_offered() -> None:
    server, _ = make(Fixed({}))
    refused = refuse(server, V)
    first = cart.alternatives(server, claims(), REGION, refused, 1)
    assert first
    top = first[0]["variant_id"]
    price = server.catalog.price(top, REGION)
    assert price is not None
    server.catalog.set_price(top, REGION, price.amount, "EUR")  # same amount, other currency
    again = [a["variant_id"] for a in cart.alternatives(server, claims(), REGION, refused, 1)]
    assert top not in again


def test_an_internal_product_never_appears_even_for_a_staff_caller() -> None:
    server, _ = make(Fixed({}))
    internal = {v.variant_id for v in server.catalog.variants_of("P0009")}
    assert internal
    for who in (claims(), claims(aud="staff", sid=None)):
        for variant in ("P0001-BLK-REG-M", "P0002-CHR-REL-2XL", "P0010-CHR-REG-L"):
            refused = refuse(server, variant)
            ids = {a["variant_id"] for a in cart.alternatives(server, who, REGION, refused, 1)}
            assert not ids & internal
            assert not {
                a["product_id"] for a in cart.alternatives(server, who, REGION, refused, 1)
            } & {"P0009"}


def test_the_refusal_block_has_the_unchanged_shape_and_no_price() -> None:
    server, _ = make(Fixed({}))
    server.catalog.set_stock(V, REGION, 0)
    refused = add(server, V, 1, "tw-shape", sid="s-shape")
    assert refused.is_error
    assert len(refused.content) == 2
    block = data_of(refused, "add_to_cart")
    assert set(block) == {"region", "requested", "alternatives"}
    assert set(block["requested"]) == {"variant_id", "on_hand", "in_stock"}
    assert block["alternatives"]
    for alt in block["alternatives"]:
        assert set(alt) == ALT_KEYS


def test_the_refusal_block_alternatives_see_holds() -> None:
    server, _ = make(Fixed({}))
    server.catalog.set_stock(V, REGION, 0)
    first = data_of(add(server, V, 1, "tw-ref-0001", sid="s-ref1"), "add_to_cart")["alternatives"]
    assert first
    top = first[0]["variant_id"]
    server.holds = Fixed({top: server.catalog.stock(top, REGION) or 0})
    again = data_of(add(server, V, 1, "tw-ref-0002", sid="s-ref2"), "add_to_cart")["alternatives"]
    assert top not in [a["variant_id"] for a in again]


def test_a_caller_who_cannot_see_the_refused_product_gets_no_alternatives() -> None:
    server, _ = make(Fixed({}))
    internal = server.catalog.variant("P0009-BLK-REG-L")
    assert internal is not None
    assert cart.alternatives(server, claims(), REGION, internal, 1) == []


def _unused() -> Iterator[None]:  # keep Iterator import honest for type checkers
    yield
