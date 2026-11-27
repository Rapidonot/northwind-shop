"""The orders Pay (demo) placed, for the order history (SA-01 PR 2; decision 0027).

Demo orders are kept apart from the seeded store behind mcp-orders: the assistant's `get_order`
does not see them (a known limit, decision 0027). Each is held by the shopper it belongs to, with
the allow-listed fields of `list_orders` only: no token, address, amount moved or escalation reason.
`checkout.py` records them; `orders.py` reads them. Neither this module nor `orders.py` can place,
pay or confirm anything.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any

# The same plain words as mcp-orders' `STATUS_WORDING` for the two statuses a demo order can have
# (pinned equal by `test_sa01_pr2_orders.py`).
STATUS_TEXT: Mapping[str, str] = {"confirmed": "not yet with the carrier", "on_hold": "on hold"}


@dataclass
class DemoOrders:
    _by_shopper: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(
        self,
        *,
        shopper: str,
        order_id: str,
        placed_on: str,
        status: str,
        lines: Sequence[Mapping[str, Any]],
        total: Mapping[str, str],
    ) -> None:
        if status not in STATUS_TEXT:
            raise ValueError("a demo order is confirmed or on hold")
        entry = {
            "order_id": order_id,
            "placed_on": placed_on,
            "status": status,
            "status_text": STATUS_TEXT[status],
            "lines": [
                {
                    "product_id": str(line["product_id"]),
                    "variant_id": str(line["variant_id"]),
                    "quantity": int(line["quantity"]),
                }
                for line in lines
            ],
            "total": {"amount": str(total["amount"]), "currency": str(total["currency"])},
            "demo": True,
        }
        with self._lock:
            self._by_shopper.setdefault(shopper, []).append(entry)

    def everyone(self) -> list[tuple[str, dict[str, Any]]]:
        """OPS-1: every shopper's demo orders, as (shopper, copy of the entry), for the read-only
        demo staff console. Copies, so a reader cannot change the history."""
        with self._lock:
            return [
                (shopper, dict(entry))
                for shopper, entries in self._by_shopper.items()
                for entry in entries
            ]

    def of(self, shopper: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._by_shopper.get(shopper, [])]


@cache
def demo_orders() -> DemoOrders:
    """One store for the process, as the checkout desk is one."""
    return DemoOrders()
