"""Test-only harness for row #21's Orders placement (BE-05, CHK-05). Not product code: nothing under
`src` may import it.

`World` is one set of shared fake states for many orders, as one sandbox provider would hold them.
`confirm` is the stand-in for the portal control (CHK-02 builds the real one): it issues the
shopper's confirmation for the order's live-price total, the amount the shopper would see.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from northwind_backend.agents.orders_place import OrderPlacementTask, Ports
from northwind_backend.audit import AuditContext, InMemoryAuditSink
from northwind_backend.fakes.catalog import CatalogState, CatalogTableFake
from northwind_backend.fakes.fraud import FraudFake, FraudState
from northwind_backend.fakes.orders import OrderState, OrderStoreFake
from northwind_backend.fakes.payment import PaymentFake, PaymentState
from northwind_backend.generate import fraud_flagged_order_ids
from northwind_backend.ports import Money

DATA = Path(__file__).resolve().parents[4] / "data" / "backend"
FIXED_CLOCK = "2026-11-11T00:00:00+00:00"
KINDS = ("clean", "address_error", "soft_decline", "stock_out", "fraud_flag")


@cache
def orders() -> tuple[dict[str, Any], ...]:
    with (DATA / "orders.jsonl").open(encoding="utf-8") as handle:
        return tuple(json.loads(line) for line in handle if line.strip())


@cache
def catalog_state() -> CatalogState:
    return CatalogState()


def first_of(kind: str) -> dict[str, Any]:
    exception = None if kind == "clean" else kind
    return next(o for o in orders() if o["exception"] == exception)


def total_for(order: dict[str, Any]) -> Money:
    """The live-price total, as the portal would show it (hard rule 2: the price table)."""
    prices = catalog_state().prices
    quotes = [prices[(line["variant_id"], order["region"])] for line in order["lines"]]
    amount = sum(
        q.amount_minor * line["quantity"] for q, line in zip(quotes, order["lines"], strict=True)
    )
    return Money(amount, quotes[0].currency)


def fixed_clock() -> str:
    return FIXED_CLOCK


@dataclass
class World:
    payment: PaymentState
    fraud: FraudState
    orders: OrderState
    sink: InMemoryAuditSink = field(default_factory=InMemoryAuditSink)

    @classmethod
    def make(cls, *, soft_decline_retry_approves: bool = True) -> World:
        return cls(
            PaymentState(soft_decline_retry_approves=soft_decline_retry_approves),
            FraudState(flagged_order_ids=fraud_flagged_order_ids(orders())),
            OrderState.from_data(DATA),
        )

    def ports(self) -> Ports:
        """A fresh set of instances over the shared states, as each run would get."""
        return Ports(
            payment=PaymentFake(state=self.payment),
            fraud=FraudFake(state=self.fraud),
            catalog=CatalogTableFake(state=catalog_state()),
            orders=OrderStoreFake(state=self.orders),
        )

    def confirm(self, order: dict[str, Any]) -> str:
        """The portal control's stand-in: the shopper confirms the summary."""
        return self.payment.confirmations.issue(order["order_id"], total_for(order))

    def task(self, order: dict[str, Any], ref: str | None = None) -> OrderPlacementTask:
        return OrderPlacementTask(order["order_id"], ref or self.confirm(order))

    def audit(self) -> AuditContext:
        return AuditContext(
            self.sink, correlation_id="checkout-1", actor="test-portal", clock=fixed_clock
        )

    def effects(self) -> Iterator[tuple[str, str]]:
        for state in (self.payment, self.fraud, self.orders):
            for effect in state.effects:
                yield effect.operation, effect.reference


def _strings_in(value: Any) -> list[str]:
    """Every string, key and number in a JSON value, unescaped, in order."""
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (str(k), *_strings_in(v))]
    if isinstance(value, list):
        return [s for v in value for s in _strings_in(v)]
    return [str(value)]


def model_text(requests: Any) -> str:
    """Everything in the recorded request bodies except the top-level `metadata`, one piece per
    line. `metadata.user_id` is the CLI's random device hash and session UUID, which a whole-body
    search mistook for card digits (row #27, bundled; it failed #11's `make ci`); it is the only
    random field. Nothing else is left out, so a new block type or field is searched too."""
    return "\n".join(
        s
        for request in requests
        for key, value in request["body"].items()
        if key != "metadata"
        for s in (key, *_strings_in(value))
    )
