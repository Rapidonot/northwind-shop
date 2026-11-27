"""BE-04 (row #25): stock reservation, fulfilment-node choice and substitutes, all deterministic
code (the user's constraint 1). `InventoryFake` is the in-memory double; `PostgresInventory` is the
store that guarantees no oversell across processes."""

from northwind_backend.inventory.memory import InventoryFake, InventoryState
from northwind_backend.inventory.model import (
    HOLD,
    Allocation,
    Clock,
    HoldStatus,
    InventoryPort,
    Line,
    Reservation,
    Shortfall,
    StockOut,
    Substitute,
)

__all__ = [
    "HOLD",
    "Allocation",
    "Clock",
    "HoldStatus",
    "InventoryFake",
    "InventoryPort",
    "InventoryState",
    "Line",
    "Reservation",
    "Shortfall",
    "StockOut",
    "Substitute",
]
