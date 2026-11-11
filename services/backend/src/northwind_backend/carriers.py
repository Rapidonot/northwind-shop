"""Fictional carriers and tracking numbers, shared by the carrier fake and the generator.

Tracking numbers are letters only, so they can never read as a card number.
"""

from __future__ import annotations

import hashlib
import re

CARRIERS = ("Northlark Parcel", "Fernway Freight", "Mossgate Couriers")
_PREFIXES = ("NLP", "FWF", "MGC")
_LETTERS = "ABCDEFGHJKLMNPQR"  # one per hex digit
# A postal code starting `XX` is well formed but undeliverable: how the generated address-error
# orders reach the carrier fake and the order store alike (row #21).
UNDELIVERABLE_PREFIX = "XX"
TRACKING_NUMBER = re.compile(rf"(?:{'|'.join(_PREFIXES)})-[A-Z]{{8}}")


def _digest(order_id: str) -> str:
    return hashlib.sha256(order_id.encode("utf-8")).hexdigest()


def carrier_index(order_id: str) -> int:
    return int(_digest(order_id)[:8], 16) % len(CARRIERS)


def carrier_for(order_id: str) -> str:
    return CARRIERS[carrier_index(order_id)]


def carrier_for_tracking(tracking_number: str) -> str:
    return CARRIERS[_PREFIXES.index(tracking_number[:3])]


def tracking_number_for(order_id: str) -> str:
    letters = "".join(_LETTERS[int(ch, 16)] for ch in _digest(order_id)[8:16])
    return f"{_PREFIXES[carrier_index(order_id)]}-{letters}"


def is_deliverable(postal_code: str) -> bool:
    return not postal_code.startswith(UNDELIVERABLE_PREFIX)
