"""The plain words for each order and scan status (row #27, D9: the user's decision, 2026-11-25).

One fixed table, pinned by a test to cover exactly `order_tool.STATUSES` and the scan statuses. No
wording confirms an order or gives a hold's reason (decision 0015, row #27 note). `get_order`
returns it as `status_text` beside each code."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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


def worded(view: Mapping[str, Any]) -> dict[str, Any]:
    """An order view with the plain words beside each status code. A status outside the table is a
    KeyError, which the server turns into its fixed failure: never a guess."""
    tracking = view["tracking"]
    return {
        **view,
        "status_text": STATUS_WORDING[view["status"]],
        "tracking": None
        if tracking is None
        else {
            **tracking,
            "scans": [{**s, "status_text": STATUS_WORDING[s["status"]]} for s in tracking["scans"]],
        },
    }
