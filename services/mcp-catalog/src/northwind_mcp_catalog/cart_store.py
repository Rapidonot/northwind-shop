"""The carts themselves (row #16, CHK-01; decision 0019).

One cart per `(session, region)`, both taken from the verified token, never from a tool argument. A
cart holds variant IDs and quantities only: no price, no stock count and no name. Those are read
live from `LiveCatalog` each time a cart is shown (hard rule 2), so a price changed while the server
runs is the one the shopper sees next.

Memory is bounded: at most `MAX_CARTS` carts, the least recently used dropped first, and at most
`MAX_LINES` lines of at most `MAX_QUANTITY` units each. A cart is checked against stock when it
changes, not reserved: reservation belongs to checkout (CHK-05).

Nothing here creates an order, a payment or anything else: the state is the quantity per line, plus
each cart's recent idempotency keys and the results they returned.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from types import TracebackType
from typing import Any

MAX_CARTS = 10_000
MAX_LINES = 20
MAX_QUANTITY = 10
MAX_KEYS = 50  # idempotency keys kept per cart (hard rule 9)

CartKey = tuple[str, str]  # (session id, region)


class Cart:
    """Lines in the order they were first added: variant ID -> quantity. `keys` holds the last
    `MAX_KEYS` idempotency keys of writes that changed this cart: key -> (fingerprint, result)."""

    def __init__(self) -> None:
        self.lines: OrderedDict[str, int] = OrderedDict()
        self.keys: OrderedDict[str, tuple[str, Any]] = OrderedDict()


class _Open:
    """The store's lock, held for one `with` block. A class, not `@contextmanager`: that one writes
    `__traceback__` onto an exception leaving the block, and a refusal (`ToolError`) is a frozen
    dataclass, which would turn every refusal raised inside the block into a failure."""

    def __init__(self, lock: threading.Lock, cart: Cart) -> None:
        self._lock = lock
        self._cart = cart

    def __enter__(self) -> Cart:
        return self._cart

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self._lock.release()


class CartStore:
    def __init__(self, max_carts: int = MAX_CARTS) -> None:
        self._carts: OrderedDict[CartKey, Cart] = OrderedDict()
        self._max = max_carts
        self._lock = threading.Lock()

    def open(self, session: str, region: str) -> _Open:
        """The caller's cart, created empty if new, held under the store's lock for the whole `with`
        block, so a stock check and the write that follows it are one step for that cart."""
        key = (session, region)
        self._lock.acquire()
        try:
            cart = self._carts.get(key)
            if cart is None:
                cart = Cart()
                self._carts[key] = cart
                while len(self._carts) > self._max:
                    self._carts.popitem(last=False)
            else:
                self._carts.move_to_end(key)
        except BaseException:
            self._lock.release()
            raise
        return _Open(self._lock, cart)

    def drop(self, session: str) -> None:
        """Forget every cart of one session (the evals start each item empty, row #16)."""
        with self._lock:
            for key in [k for k in self._carts if k[0] == session]:
                del self._carts[key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._carts)
