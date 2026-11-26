"""BE-04 (row #25): fulfilment-node choice is deterministic code. A US postal code starting 0-4
prefers DC-EAST and 5-9 prefers DC-WEST; one node that fills the whole order beats a split; a line
is never split across nodes; a one-node region has one choice."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from northwind_backend.errors import Ok, ToolError
from northwind_backend.inventory import InventoryFake, InventoryState, Line
from northwind_backend.inventory.nodes import choose, preference

pytestmark = pytest.mark.req("BE-04")

EAST, WEST = "DC-EAST", "DC-WEST"
A, B = "P0001-AAA", "P0002-BBB"


def stock(table: Mapping[tuple[str, str], int]) -> Callable[[str, str], int]:
    return lambda variant, node: table.get((variant, node), 0)


@pytest.mark.parametrize("digit", "01234")
def test_us_postal_codes_0_to_4_prefer_the_east_dc(digit: str) -> None:
    assert preference("US", f"{digit}1234") == (EAST, WEST)


@pytest.mark.parametrize("digit", "56789")
def test_us_postal_codes_5_to_9_prefer_the_west_dc(digit: str) -> None:
    assert preference("US", f"{digit}1234") == (WEST, EAST)


def test_the_preference_is_a_reordering_of_the_regions_nodes_never_a_new_set() -> None:
    assert set(preference("US", "10001")) == set(preference("US", "90001")) == {EAST, WEST}


def test_a_us_postal_code_the_rule_does_not_cover_uses_the_files_order() -> None:
    assert preference("US", "") == preference("US", "A1B2C3") == (EAST, WEST)


def test_the_preference_follows_the_nodes_it_is_given() -> None:
    assert preference("US", "10001", (WEST, EAST)) == (EAST, WEST)
    assert preference("US", "90001", (EAST, WEST)) == (WEST, EAST)


@pytest.mark.parametrize(("region", "node"), [("CA", "DC-CA"), ("UK", "DC-UK"), ("EU", "DC-EU")])
@pytest.mark.parametrize("postal", ["", "1", "9"])
def test_a_one_node_region_has_that_node_whatever_the_postal_code(
    region: str, node: str, postal: str
) -> None:
    assert preference(region, postal) == (node,)


def test_a_region_with_no_nodes_has_no_preference() -> None:
    assert preference("MARS", "1") == ()


def test_the_real_us_order_postal_code_format_picks_a_dc_by_its_digits() -> None:
    """Orders and shoppers in data/backend carry postal codes as `US-7494`, so the digits that
    count are the ones after the region prefix. This fails if `preference` reads the first
    character only: every real order would then get the file's order."""
    assert preference("US", "US-2000")[0] == EAST
    assert preference("US", "US-7494")[0] == WEST


# ---- choose --------------------------------------------------------------------------------


def test_the_first_preferred_node_that_fills_every_line_ships_the_whole_order() -> None:
    both = stock({(A, EAST): 5, (B, EAST): 5, (A, WEST): 5, (B, WEST): 5})
    allocations, short = choose([Line(A, 2), Line(B, 1)], (WEST, EAST), both)
    assert short == ()
    assert {a.node for a in allocations} == {WEST}
    assert {(a.variant_id, a.quantity) for a in allocations} == {(A, 2), (B, 1)}


def test_one_node_that_fills_the_order_beats_a_split_even_when_it_is_not_preferred() -> None:
    table = stock({(A, EAST): 5, (B, EAST): 0, (A, WEST): 5, (B, WEST): 5})
    allocations, short = choose([Line(A, 1), Line(B, 1)], (EAST, WEST), table)
    assert short == ()
    assert {a.node for a in allocations} == {WEST}, "the order was split to keep the preferred node"


def test_when_no_node_fills_everything_each_line_goes_whole_to_a_node_that_can() -> None:
    table = stock({(A, EAST): 5, (B, WEST): 5})
    allocations, short = choose([Line(A, 2), Line(B, 3)], (EAST, WEST), table)
    assert short == ()
    assert {(a.variant_id, a.node, a.quantity) for a in allocations} == {(A, EAST, 2), (B, WEST, 3)}


def test_a_line_is_never_split_across_nodes() -> None:
    table = stock({(A, EAST): 3, (A, WEST): 3})  # 6 in all, but 3 at each
    allocations, short = choose([Line(A, 5)], (EAST, WEST), table)
    assert allocations == ()
    assert [(s.variant_id, s.wanted, s.available) for s in short] == [(A, 5, 3)]


def test_a_short_line_makes_the_whole_choice_empty() -> None:
    table = stock({(A, EAST): 5, (B, EAST): 1})
    allocations, short = choose([Line(A, 1), Line(B, 4)], (EAST, WEST), table)
    assert allocations == (), "a fillable line was allocated although the order is short"
    assert [s.variant_id for s in short] == [B]


def test_a_one_node_region_fills_or_is_short() -> None:
    table = stock({(A, "DC-CA"): 2})
    allocations, short = choose([Line(A, 2)], ("DC-CA",), table)
    assert [(a.node, a.quantity) for a in allocations] == [("DC-CA", 2)] and short == ()
    allocations, short = choose([Line(A, 3)], ("DC-CA",), table)
    assert allocations == () and [(s.wanted, s.available) for s in short] == [(3, 2)]


def test_no_nodes_means_every_line_is_short_with_none_available() -> None:
    allocations, short = choose([Line(A, 1)], (), stock({}))
    assert allocations == () and [(s.available) for s in short] == [0]


def test_choose_is_deterministic() -> None:
    table = stock({(A, EAST): 5, (B, EAST): 5, (A, WEST): 5, (B, WEST): 5})
    lines = [Line(B, 1), Line(A, 1)]
    assert choose(lines, (EAST, WEST), table) == choose(lines, (EAST, WEST), table)
    assert choose(lines, (EAST, WEST), table) == choose(list(reversed(lines)), (EAST, WEST), table)


# ---- through the inventory -----------------------------------------------------------------


def _inventory(
    rows: list[dict[str, object]], regions: Mapping[str, tuple[str, ...]]
) -> InventoryFake:
    return InventoryFake(
        lambda: datetime(2026, 11, 26, 12, 0, tzinfo=UTC),
        state=InventoryState.from_rows(rows),
        regions=regions,
    )


def _row(variant: str, node: str, region: str, on_hand: int) -> dict[str, object]:
    return {"variant_id": variant, "node": node, "region": region, "on_hand": on_hand}


def _reserve(inv: InventoryFake, postal: str, region: str, lines: list[Line]) -> tuple[str, ...]:
    result = inv.reserve(
        idempotency_key="key-1", order_id="O000001", region=region, postal_code=postal, lines=lines
    )
    assert isinstance(result, Ok), result
    return result.value.nodes()


def test_reserve_ships_from_the_preferred_dc_when_both_have_stock() -> None:
    rows = [_row(A, EAST, "US", 5), _row(A, WEST, "US", 5)]
    regions = {"US": (EAST, WEST)}
    assert _reserve(_inventory(rows, regions), "10001", "US", [Line(A, 1)]) == (EAST,)
    assert _reserve(_inventory(rows, regions), "90001", "US", [Line(A, 1)]) == (WEST,)


def test_reserve_falls_back_to_the_other_dc_when_the_preferred_one_is_short() -> None:
    rows = [_row(A, EAST, "US", 0), _row(A, WEST, "US", 5)]
    inv = _inventory(rows, {"US": (EAST, WEST)})
    assert _reserve(inv, "10001", "US", [Line(A, 2)]) == (WEST,)


def test_reserve_never_splits_a_line_across_dcs() -> None:
    rows = [_row(A, EAST, "US", 3), _row(A, WEST, "US", 3)]
    inv = _inventory(rows, {"US": (EAST, WEST)})
    refused = inv.reserve(
        idempotency_key="key-1",
        order_id="O000001",
        region="US",
        postal_code="10001",
        lines=[Line(A, 5)],
    )
    assert isinstance(refused, ToolError) and refused.code == "out_of_stock"


def test_reserve_in_a_one_node_region_uses_that_node() -> None:
    rows = [_row(A, "DC-CA", "CA", 4)]
    inv = _inventory(rows, {"CA": ("DC-CA",)})
    assert _reserve(inv, "CA-1234", "CA", [Line(A, 4)]) == ("DC-CA",)
