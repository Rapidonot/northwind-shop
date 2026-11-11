"""Test-only confirmations for BE-08's and row #21's tests: the stand-in for the portal control
(hard rule 9). Nothing under `src` may import this module."""

from __future__ import annotations

from typing import Any


def _payment_state(port: Any) -> Any:
    while not hasattr(port, "state"):
        port = port._fake
    return port.state


def confirm(port: Any, order_id: Any, amount: Any, key: Any = None) -> str:
    """The portal confirmation a test's `authorize` presents (row #21, hard rule 9). One per
    (key, order, amount), so a same-key replay presents the same reference."""
    state = _payment_state(port)

    def memo() -> str:
        refs = state.__dict__.setdefault("_test_refs", {})
        slot = repr((key, order_id, amount))
        if slot not in refs:
            refs[slot] = state.confirmations.issue(order_id, amount)
        return str(refs[slot])

    # Under the state's own lock: concurrent replays must present one reference.
    return str(state.store.exclusive(memo))


def ref_of(port: Any, intent_id: Any) -> str:
    """The reference that authorized this intent; a well-formed, never-issued one otherwise."""
    found = _payment_state(port).confirmation_of(intent_id) if isinstance(intent_id, str) else None
    return found or "cf_ZZZZZZZZZZZZZZZZ"
