"""The plain words for each order and scan status (row #27, D9: the user's decision, 2026-11-25).

One fixed table, pinned by a test to cover exactly `order_tool.STATUSES` and the scan statuses. No
wording confirms an order or gives a hold's reason (decision 0015, row #27 note). Nothing returns
it yet: `get_order` gains it in PR 2's stage B, because that changes what the model sees."""

from __future__ import annotations

from collections.abc import Mapping

STATUS_WORDING: Mapping[str, str] = {
    "processing": "being processed",
    "on_hold": "on hold",
    "confirmed": "not yet with the carrier",
    "label_created": "label created, not yet scanned",
    "picked_up": "picked up by the carrier",
    "in_transit": "in transit",
    "out_for_delivery": "out for delivery",
    "delivered": "delivered",
}
