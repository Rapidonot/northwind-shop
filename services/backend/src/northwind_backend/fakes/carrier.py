"""CarrierPort fake.

A new label has no scans, so `track` returns `Ok` with no events: a valid empty result. An outage is
a transient error, and a number the carrier doesn't know is a validation error. All three stay
distinct (hard rule 10).

A postal code starting `XX` is well formed but undeliverable, which is how the generated
address-error orders reach it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from northwind_backend.carriers import (
    TRACKING_NUMBER,
    carrier_for,
    carrier_for_tracking,
    tracking_number_for,
)
from northwind_backend.errors import Ok, Result, ToolError, business, validation
from northwind_backend.fakes._base import (
    CARD_DATA_REFUSED,
    Effect,
    FakeService,
    card_data_in,
    check_order_id,
    matches,
)
from northwind_backend.ports import Shipment, Tracking, TrackingEvent

CARRIER_SCOPES = frozenset({"create_shipment", "track"})
REGIONS = frozenset({"US", "CA", "UK", "EU"})
SERVICES = frozenset({"standard", "express"})
UNDELIVERABLE_PREFIX = "XX"
DRY_RUN_SHIPMENT_ID = "sh_dry_run"

_POSTAL_CODE = re.compile(r"[A-Z]{2}-\d{4}", re.ASCII)

BAD_REGION = validation("invalid_region", "The region is not one Northwind ships to.")
BAD_POSTAL_CODE = validation("invalid_postal_code", "The postal code is malformed.")
BAD_SERVICE = validation("invalid_service", "The delivery service is not known.")
BAD_TRACKING = validation("invalid_tracking_number", "The tracking number is malformed.")
UNKNOWN_TRACKING = validation("unknown_tracking_number", "There is no such tracking number.")
UNDELIVERABLE = business("address_undeliverable", "The carrier cannot deliver to this address.")
ALREADY_SHIPPED = business("already_shipped", "This order already has a shipment.")


class CarrierFake(FakeService):
    def __init__(
        self,
        *,
        tracking: Mapping[str, tuple[TrackingEvent, ...]] | None = None,
        scopes: frozenset[str] = CARRIER_SCOPES,
    ) -> None:
        super().__init__(scopes)
        self._events = dict(tracking or {})
        self._shipments: dict[str, Shipment] = {}

    def create_shipment(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        region: str,
        postal_code: str,
        service: str = "standard",
        dry_run: bool = False,
    ) -> Result[Shipment]:
        refused = (
            self._admit("create_shipment", idempotency_key, order_id, postal_code)
            or check_order_id(order_id)
            or _check_destination(region, postal_code, service)
        )
        if refused is not None:
            return refused

        def effect(dry: bool) -> Result[Shipment]:
            if postal_code.startswith(UNDELIVERABLE_PREFIX):
                return UNDELIVERABLE
            if order_id in self._shipments:
                return ALREADY_SHIPPED
            shipment_id = DRY_RUN_SHIPMENT_ID if dry else f"sh_{len(self._shipments) + 1:06d}"
            shipment = Shipment(
                shipment_id,
                order_id,
                carrier_for(order_id),
                tracking_number_for(order_id),
                service,
                dry,
            )
            if not dry:
                self._shipments[order_id] = shipment
                self._events.setdefault(shipment.tracking_number, ())
                self._effects.append(Effect("create_shipment", shipment_id))
            return Ok(shipment)

        request = {
            "order_id": order_id,
            "region": region,
            "postal_code": postal_code,
            "service": service,
        }
        return self._commit("create_shipment", idempotency_key, request, dry_run, effect)

    def track(self, tracking_number: str) -> Result[Tracking]:
        refused = self._reachable("track")
        if refused is not None:
            return refused
        if card_data_in(tracking_number):
            return CARD_DATA_REFUSED
        if not matches(TRACKING_NUMBER, tracking_number):
            return BAD_TRACKING
        events = self._events.get(tracking_number)
        if events is None:
            return UNKNOWN_TRACKING
        return Ok(Tracking(tracking_number, carrier_for_tracking(tracking_number), events))


def _check_destination(region: object, postal_code: object, service: object) -> ToolError | None:
    # `isinstance` first: looking a list up in a set raises, and this must not (hard rule 10).
    if not isinstance(region, str) or region not in REGIONS:
        return BAD_REGION
    if not matches(_POSTAL_CODE, postal_code):
        return BAD_POSTAL_CODE
    return None if isinstance(service, str) and service in SERVICES else BAD_SERVICE
