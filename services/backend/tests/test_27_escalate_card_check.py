"""H5, bundled into row #27 (decision 0021 rule 6): `OrderStoreFake.escalate` screens its context
for card data on the raw keys and values, not on a JSON encoding of them (hard rule 1, PAY-05).

`json.dumps` writes a newline between digit groups as `\\n` and a fullwidth digit as `\\uff14`, and
both hide a card number from the check. `northwind_common.cards.holds_card_data` reads the raw
texts, as every MCP server's argument check does (CC-10 security re-review M1)."""

from __future__ import annotations

from typing import Any

import pytest

from northwind_backend.fakes._base import CARD_DATA_REFUSED
from northwind_backend.fakes.orders import OrderState, OrderStoreFake
from northwind_backend.ports import OrderStatus

pytestmark = [pytest.mark.req("PAY-05"), pytest.mark.req("FOUND-3")]

PAN = "4242424242424242"  # the sandbox test card: synthetic (hard rule 3)
FULLWIDTH = PAN.translate(str.maketrans("0123456789", "０１２３４５６７８９"))


def _pending(store: OrderStoreFake) -> str:
    return next(o for o, order in store.state.orders.items() if order.status is OrderStatus.PENDING)


@pytest.mark.parametrize(
    "context",
    [
        {"note": "4242 4242\n4242 4242"},  # a newline between groups: `\n` once JSON-encoded
        {"note": FULLWIDTH},  # fullwidth digits: `４…` once JSON-encoded
        {"a": "4242 4242", "b": {"c": ["4242\t4242"]}},  # split across keys, a tab between
        {PAN: "key, not value"},
    ],
)
def test_card_data_in_an_escalation_context_is_refused(context: dict[str, Any]) -> None:
    store = OrderStoreFake(state=OrderState.from_data())
    order_id = _pending(store)
    result = store.escalate(
        idempotency_key="k-27-h5", order_id=order_id, reason="fraud_review", context=context
    )
    assert result == CARD_DATA_REFUSED
    assert store.state.orders[order_id].status is OrderStatus.PENDING  # nothing was written
    assert store.effects == ()


def test_an_ordinary_context_is_still_kept() -> None:
    store = OrderStoreFake(state=OrderState.from_data())
    order_id = _pending(store)
    result = store.escalate(
        idempotency_key="k-27-h5-ok",
        order_id=order_id,
        reason="fraud_review",
        context={"attempts": 2, "sizes": "10, 12, 14"},
    )
    assert not result.is_error
    assert store.state.orders[order_id].status is OrderStatus.ESCALATED
