"""The order store and the carrier fake behind northwind-orders (row #27, CHK-06; hard rules 2, 7,
10).

Both are seeded once, read-only, from `data/backend`, after each file has been checked against its
manifest (the server refuses to start on a mismatch). Each order's labelled outcome is applied
(`replay_labels.jsonl`): `placed` becomes `confirmed` and `escalated` stays `escalated`, so store
status and tracking agree (a placed order is the one the carrier has a label or scans for). Without
it, every fixture order would load as `pending`.

`view` reads the store and the carrier on every call, so a status or scan changed while the server
runs shows in the next result (hard rule 2). It returns only the allow-listed fields: never the
`payment_token`, the postal code, the `exception`, the escalation reason or the `shopper_id`.

Shopper status (`shopper_status`), from the store status and the last scan:
- `escalated` -> `on_hold` (no reason is given);
- `pending` or `paying` -> `processing`;
- `confirmed` with no shipment -> `confirmed`; with a label and no scans -> `label_created` (a valid
  empty result, not an error); else the last scan: `picked_up`, `in_transit`, `out_for_delivery`,
  `delivered`. A scan status outside those fails closed, as an unavailable carrier does.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from northwind_backend.errors import ToolError as BackendError
from northwind_backend.fakes.carrier import CarrierFake, CarrierState
from northwind_backend.fakes.orders import DATA, OrderState, OrderStoreFake
from northwind_backend.ports import Order, OrderStatus, Tracking, TrackingEvent
from northwind_common.errors import ToolError
from northwind_common.order_tool import ORDER_ID

FILES = ("orders.jsonl", "replay_labels.jsonl", "shoppers.jsonl", "tracking.jsonl")
SCAN_STATUSES = ("picked_up", "in_transit", "out_for_delivery", "delivered")
_OUTCOMES = {"placed": OrderStatus.CONFIRMED, "escalated": OrderStatus.ESCALATED}


class DataMismatch(RuntimeError):
    """A file in `data/backend` does not match its manifest. Names the file, nothing else."""


def not_found() -> ToolError:
    """The one result for an unknown, malformed or another shopper's order: the same bytes, so the
    tool cannot tell which order numbers exist (NFR-01)."""
    return ToolError("validation", False, "not_found", "No order with that number on this account.")


def store_unavailable() -> ToolError:
    return ToolError(
        "transient", True, "orders_unavailable", "Orders could not be read. Try again later."
    )


def tracking_unavailable() -> ToolError:
    return ToolError(
        "transient", True, "tracking_unavailable", "Tracking could not be read. Try again later."
    )


def _jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def verify_files(data: Path) -> None:
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    for name in FILES:
        digest = hashlib.sha256((data / name).read_bytes()).hexdigest()
        if manifest.get("files", {}).get(name) != digest:
            raise DataMismatch(f"{name} does not match data/backend/manifest.json")


def shopper_status(status: OrderStatus, tracking: Tracking | None) -> str:
    if status is OrderStatus.ESCALATED:
        return "on_hold"
    if status in (OrderStatus.PENDING, OrderStatus.PAYING):
        return "processing"
    if status is not OrderStatus.CONFIRMED:
        raise tracking_unavailable()  # fail closed on a status this mapping does not know
    if tracking is None:
        return "confirmed"
    if not tracking.events:
        return "label_created"
    last = tracking.events[-1].status
    if last not in SCAN_STATUSES:
        raise tracking_unavailable()
    return last


@dataclass
class OrderBook:
    orders: OrderStoreFake
    carrier: CarrierFake
    tracking_numbers: dict[str, str]  # order ID -> tracking number, for orders with a shipment
    placed_on: dict[str, str]

    @classmethod
    def from_data(cls, data: Path = DATA) -> OrderBook:
        verify_files(data)
        state = OrderState.from_data(data)
        for label in _jsonl(data / "replay_labels.jsonl"):
            order = state.orders[label["order_id"]]
            outcome = _OUTCOMES[label["expected_outcome"]]
            reason = label["expected_reason"] if outcome is OrderStatus.ESCALATED else None
            state.orders[order.order_id] = replace(order, status=outcome, escalation=reason)
        rows = _jsonl(data / "tracking.jsonl")
        events = {
            row["tracking_number"]: tuple(
                TrackingEvent(e["on"], e["status"]) for e in row["events"]
            )
            for row in rows
        }
        return cls(
            orders=OrderStoreFake(state=state, scopes=frozenset({"get"})),
            carrier=CarrierFake(state=CarrierState(tracking=events), scopes=frozenset({"track"})),
            tracking_numbers={row["order_id"]: row["tracking_number"] for row in rows},
            placed_on={row["order_id"]: row["placed_on"] for row in _jsonl(data / "orders.jsonl")},
        )

    # -- not tools: how a test (or a later backend row) changes the store while the server runs --

    def set_status(self, order_id: str, status: OrderStatus) -> None:
        state = self.orders.state
        state.orders[order_id] = replace(state.orders[order_id], status=status)

    def add_scan(self, order_id: str, on: str, status: str) -> None:
        number = self.tracking_numbers[order_id]
        events = self.carrier.state.events
        events[number] = (*events.get(number, ()), TrackingEvent(on, status))

    # -- the read --------------------------------------------------------------------------------

    def _order(self, order_id: str) -> Order | None:
        found = self.orders.get(order_id)
        if isinstance(found, BackendError):
            if found.code in ("invalid_order_id", "unknown_order"):
                return None
            raise store_unavailable()
        return found.value

    def _tracking(self, order_id: str) -> Tracking | None:
        number = self.tracking_numbers.get(order_id)
        if number is None:
            return None
        found = self.carrier.track(number)
        if isinstance(found, BackendError):
            raise tracking_unavailable()
        return found.value

    def history(self, *, shopper: str, region: str | None) -> list[dict[str, Any]]:
        """SA-01 PR 2: every order of the caller's own in their region, newest first, with the
        same allow-listed fields as `view` and no tracking (the order page's history list).
        `shopper` and `region` come from the verified token, never from an argument."""
        state = self.orders.state
        own = [o for o in state.orders.values() if o.shopper_id == shopper and o.region == region]
        out = []
        for order in own:
            tracking = self._tracking(order.order_id)
            out.append(
                {
                    "order_id": order.order_id,
                    "placed_on": self.placed_on.get(order.order_id),
                    "status": shopper_status(order.status, tracking),
                    "lines": [
                        {
                            "product_id": line.product_id,
                            "variant_id": line.variant_id,
                            "quantity": line.quantity,
                        }
                        for line in order.lines
                    ],
                }
            )
        out.sort(key=lambda o: (o["placed_on"] or "", o["order_id"]), reverse=True)
        return out

    def view(self, order_id: str, *, shopper: str, region: str | None) -> Mapping[str, Any]:
        """The caller's own order, allow-listed fields only, or `not_found`. `shopper` and `region`
        come from the verified token, never from an argument."""
        if not ORDER_ID.fullmatch(order_id):
            raise not_found()
        order = self._order(order_id)
        if order is None or order.shopper_id != shopper or order.region != region:
            raise not_found()
        tracking = self._tracking(order_id)
        return {
            "order_id": order.order_id,
            "placed_on": self.placed_on.get(order.order_id),
            "status": shopper_status(order.status, tracking),
            "lines": [
                {
                    "product_id": line.product_id,
                    "variant_id": line.variant_id,
                    "quantity": line.quantity,
                }
                for line in order.lines
            ],
            "tracking": None
            if tracking is None
            else {
                "carrier": tracking.carrier,
                "tracking_number": tracking.tracking_number,
                "scans": [{"on": e.on, "status": e.status} for e in tracking.events],
            },
        }
