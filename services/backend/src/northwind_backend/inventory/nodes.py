"""Fulfilment-node choice for BE-04 (row #25): deterministic code, never a model decision.

The candidates are the region's `stock_nodes` from `data/fixtures/regions.json`, in a fixed order of
preference:
- US: a postal code (after an optional `US-` prefix) starting 0–4 prefers DC-EAST, 5–9 prefers
  DC-WEST; anything else uses the file's order.
- Other regions: the file's order. Each has one node today.

The rule:
1. The first node, in preference order, that can fill every line ships the whole order.
2. Otherwise each line goes to the first node that can fill that line alone.
3. A line no single node can fill is short: a line is never split across nodes.

`available(variant, node)` must already leave out live holds (`model.live`); callers pass a reading
taken under the stock rows' locks when the answer must hold.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from functools import cache
from pathlib import Path

from northwind_backend.inventory.model import Allocation, Line, Shortfall

REGIONS = Path(__file__).resolve().parents[5] / "data" / "fixtures" / "regions.json"
_US_EAST = frozenset("01234")
_US_WEST = frozenset("56789")


@cache
def region_nodes(path: Path = REGIONS) -> dict[str, tuple[str, ...]]:
    with path.open(encoding="utf-8") as handle:
        rows = json.load(handle)
    return {row["code"]: tuple(row["stock_nodes"]) for row in rows}


def preference(
    region: str, postal_code: str, nodes: Sequence[str] | None = None
) -> tuple[str, ...]:
    """The region's nodes, most preferred first."""
    ordered = tuple(nodes) if nodes is not None else region_nodes().get(region, ())
    digits = postal_code.removeprefix("US-")  # the synthetic data writes US codes as `US-7494`
    if region != "US" or not digits:
        return ordered
    first = digits[0]
    if first in _US_EAST:
        front = "DC-EAST"
    elif first in _US_WEST:
        front = "DC-WEST"
    else:
        return ordered
    return tuple(sorted(ordered, key=lambda node: node != front))


def choose(
    lines: Sequence[Line],
    nodes: Sequence[str],
    available: Callable[[str, str], int],
) -> tuple[tuple[Allocation, ...], tuple[Shortfall, ...]]:
    """`(allocations, ())` when every line can be filled, else `((), shortfalls)`."""
    for node in nodes:
        if all(available(line.variant_id, node) >= line.quantity for line in lines):
            whole = (Allocation(line.variant_id, node, line.quantity) for line in lines)
            return tuple(sorted(whole)), ()
    allocations: list[Allocation] = []
    short: list[Shortfall] = []
    for line in lines:
        chosen = next((n for n in nodes if available(line.variant_id, n) >= line.quantity), None)
        if chosen is None:
            best = max((available(line.variant_id, n) for n in nodes), default=0)
            short.append(Shortfall(line.variant_id, line.quantity, max(best, 0)))
        else:
            allocations.append(Allocation(line.variant_id, chosen, line.quantity))
    if short:
        return (), tuple(short)
    return tuple(sorted(allocations)), ()
