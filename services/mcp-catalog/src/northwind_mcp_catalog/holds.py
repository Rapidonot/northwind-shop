"""Live stock holds, read by the catalog's stock (row #18, step 0 (c); BE-04's holds, row #25).

BE-04 reserves stock at checkout: a reservation holds units at one node per line until it is
committed, released or past its `expires_at`. Units held live are not free to sell, so every stock
figure the catalog tools give (`check_stock`, the cart's lines, an add's check, its alternatives
and `search_products`'s `in_stock`) is the live store's stock less the units held live in the
caller's region, never below zero.

A hold counts exactly when BE-04's own `available` counts it (`inventory/postgres.py`,
`_AVAILABLE`): not swept, its reservation `held`, and `expires_at` after now (`expires_at == now`
does not count).

`Holds` is the port. `NoHolds`, the default, holds nothing: tests and replay use it, so a recording
never depends on the database. A reader that fails must raise `unavailable()` (hard rule 10:
`transient`, retryable, never a quiet zero).

The Postgres reader for the running server waits on the user (row #18, step 0 (c)): this package
must not open a database connection
(`test_tools.py::test_the_catalog_source_never_touches_the_index`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from northwind_common.errors import ToolError


def unavailable() -> ToolError:
    """A fresh error per raise: one shared instance would carry the last request's traceback and
    frames from call to call (security review L2)."""
    return ToolError(
        "transient",
        True,
        "inventory_unavailable",
        "Stock can't be checked right now. Try again shortly.",
    )


class Holds(Protocol):
    def live(self, region: str) -> Mapping[str, int]:
        """Units held live now at the nodes of `region`, by variant. Raises `unavailable()`."""
        ...


class NoHolds:
    def live(self, region: str) -> Mapping[str, int]:
        return {}


def subtract(on_hand: int | None, held: Mapping[str, int], variant_id: str) -> int:
    """The live store's stock less the units held live, never below zero."""
    return max(0, (on_hand or 0) - held.get(variant_id, 0))
