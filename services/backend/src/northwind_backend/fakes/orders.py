"""OrderStorePort fake (row #21; a stub for `mcp-orders`, FOUND-3 rest).

Loaded from `data/backend/orders.jsonl` and `shoppers.jsonl`. Every write takes an idempotency key
and `dry_run` (hard rule 9). The orders, the addresses on file, the idempotency store and its lock
live on an `OrderState`, which several instances can share, so one confirmed order and one
escalation per order hold across instances (P3, CHK-05).

`confirm` records the payment intent and amount it was given; the Orders agent's handler gates it on
a capture of exactly the order's total in the same run. `escalate` keeps the context it was given,
but hashes only the order and reason, so a rerun whose context differs in an attempt count still
replays the first result instead of conflicting.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from northwind_backend.carriers import is_deliverable
from northwind_backend.errors import TIMEOUT, Ok, Result, business, validation
from northwind_backend.fakes._base import (
    CARD_DATA_REFUSED,
    Effect,
    FakeService,
    FakeState,
    FaultKind,
    card_data_in,
    check_money,
    check_order_id,
    matches,
)
from northwind_backend.ports import (
    Money,
    Order,
    OrderLine,
    OrderStatus,
    PaymentIntent,
    claim_payment_view,
    further_along,
)

ORDER_SCOPES = frozenset({"get", "use_address_on_file", "confirm", "escalate", "claim_payment"})
DATA = Path(__file__).resolve().parents[5] / "data" / "backend"
_INTENT = re.compile(r"pi_\d{6}", re.ASCII)
_REASON = re.compile(r"[a-z][a-z0-9_]{0,47}", re.ASCII)
_RUN = re.compile(r"[A-Za-z0-9_-]{8,64}", re.ASCII)

UNKNOWN_ORDER = validation("unknown_order", "There is no such order.")
BAD_INTENT = validation("invalid_intent", "The payment intent ID is malformed.")
BAD_REASON = validation("invalid_reason", "The escalation reason is not a known code.")
NOT_PENDING = business("order_not_pending", "This order is already confirmed or escalated.")
ALREADY_CONFIRMED = business("already_confirmed", "This order is already confirmed.")
ALREADY_ESCALATED = business("already_escalated", "This order is already escalated.")
NO_USABLE_ADDRESS = business(
    "no_usable_address_on_file", "The address on file can't be used for this order."
)
CLAIMED = business("claimed_by_another_run", "Another run is already paying for this order.")
BAD_RUN = validation("invalid_run_id", "The run identity is malformed.")
ADDRESS_DELIVERABLE = business(
    "address_already_deliverable", "The order's address is deliverable; nothing was changed."
)


class OrderState(FakeState):
    def __init__(
        self,
        orders: Iterable[Mapping[str, Any]],
        shoppers: Iterable[Mapping[str, Any]],
    ) -> None:
        super().__init__()
        self.orders: dict[str, Order] = {o["order_id"]: _order(o) for o in orders}
        # Only what the address rule needs: region and postal code, never the street or a name.
        self.on_file: dict[str, tuple[str, str]] = {
            s["shopper_id"]: (s["address"]["region"], s["address"]["postal_code"]) for s in shoppers
        }
        self.escalations: dict[str, Mapping[str, Any]] = {}
        # Every claim step, apart from `effects` (which counts order effects): (order ID, step).
        self.claims: list[tuple[str, str]] = []

    @classmethod
    def from_data(cls, data: Path = DATA) -> OrderState:
        return cls(_jsonl(data / "orders.jsonl"), _jsonl(data / "shoppers.jsonl"))


class OrderStoreFake(FakeService):
    def __init__(self, *, state: OrderState, scopes: frozenset[str] = ORDER_SCOPES) -> None:
        super().__init__(scopes, state)
        self.state = state

    def get(self, order_id: str) -> Result[Order]:
        refused = self._reachable("get") or check_order_id(order_id)
        if refused is not None:
            return refused
        found = self.state.orders.get(order_id)
        return UNKNOWN_ORDER if found is None else Ok(found)

    def use_address_on_file(
        self, *, idempotency_key: str, order_id: str, dry_run: bool = False
    ) -> Result[Order]:
        refused = self._admit("use_address_on_file", idempotency_key, order_id) or check_order_id(
            order_id
        )
        if refused is not None:
            return refused

        def effect(dry: bool) -> Result[Order]:
            order = self.state.orders.get(order_id)
            if order is None:
                return UNKNOWN_ORDER
            if order.status is not OrderStatus.PENDING:
                return NOT_PENDING
            if is_deliverable(order.shipping_postal_code):
                return ADDRESS_DELIVERABLE
            on_file = self.state.on_file.get(order.shopper_id)
            if on_file is None or on_file[0] != order.region or not is_deliverable(on_file[1]):
                return NO_USABLE_ADDRESS
            updated = replace(order, shipping_postal_code=on_file[1], address_on_file=True)
            return self._save("use_address_on_file", updated, dry)

        request = {"order_id": order_id}
        return self._commit("use_address_on_file", idempotency_key, request, dry_run, effect)

    def confirm(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        intent_id: str,
        amount: Money,
        dry_run: bool = False,
    ) -> Result[Order]:
        refused = (
            self._admit("confirm", idempotency_key, order_id, intent_id)
            or check_order_id(order_id)
            or (None if matches(_INTENT, intent_id) else BAD_INTENT)
            or check_money(amount)
        )
        if refused is not None:
            return refused

        def effect(dry: bool) -> Result[Order]:
            order = self.state.orders.get(order_id)
            if order is None:
                return UNKNOWN_ORDER
            if order.status is OrderStatus.CONFIRMED:
                return ALREADY_CONFIRMED
            if order.status not in (OrderStatus.PENDING, OrderStatus.PAYING):
                return NOT_PENDING
            updated = replace(
                order,
                status=OrderStatus.CONFIRMED,
                intent_id=intent_id,
                amount_minor=amount.amount_minor,
                currency=amount.currency,
            )
            return self._save("confirm", updated, dry)

        request = {
            "order_id": order_id,
            "intent_id": intent_id,
            "amount_minor": amount.amount_minor,
            "currency": amount.currency,
        }
        return self._commit("confirm", idempotency_key, request, dry_run, effect)

    def escalate(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        reason: str,
        context: Mapping[str, Any],
        dry_run: bool = False,
    ) -> Result[Order]:
        refused = (
            self._admit("escalate", idempotency_key, order_id, reason)
            or check_order_id(order_id)
            or (None if matches(_REASON, reason) else BAD_REASON)
        )
        if refused is not None:
            return refused
        if card_data_in(json.dumps(dict(context), sort_keys=True)):
            return CARD_DATA_REFUSED
        kept = MappingProxyType(json.loads(json.dumps(dict(context))))  # a plain copy, JSON only

        def effect(dry: bool) -> Result[Order]:
            order = self.state.orders.get(order_id)
            if order is None:
                return UNKNOWN_ORDER
            if order.status is OrderStatus.ESCALATED:
                return ALREADY_ESCALATED
            # A paying order can be escalated: the caller's context reports the claim (F1).
            if order.status not in (OrderStatus.PENDING, OrderStatus.PAYING):
                return NOT_PENDING
            updated = replace(order, status=OrderStatus.ESCALATED, escalation=reason)
            if not dry:
                stored = dict(kept)
                payment = stored.get("payment")
                caller = payment if isinstance(payment, Mapping) else None
                if order.claimed_by is not None:
                    # Under the store's lock (F1, claim-fix review C1): the caller's view and the
                    # claim's as it stands now; the one showing more money moved is kept, so a
                    # stale read on either side never hides a hold or a capture.
                    label = str(
                        (caller or {}).get("claim") or "unreported"
                    )  # the store can't tell who escalated (N3)
                    stored["payment"] = further_along(caller, claim_payment_view(order, label))
                self.state.escalations[order_id] = MappingProxyType(stored)
            return self._save("escalate", updated, dry)

        request = {"order_id": order_id, "reason": reason}
        return self._commit("escalate", idempotency_key, request, dry_run, effect)

    def claim_payment(
        self,
        *,
        order_id: str,
        run_id: str,
        payment: PaymentIntent | None = None,
        dry_run: bool = False,
    ) -> Result[Order]:
        intent_id = None if payment is None else payment.intent_id
        refused = (
            self._reachable("claim_payment", read=False)
            or (CARD_DATA_REFUSED if card_data_in(order_id, run_id, intent_id) else None)
            or check_order_id(order_id)
            or (None if matches(_RUN, run_id) else BAD_RUN)
            or (None if payment is None or matches(_INTENT, intent_id) else BAD_INTENT)
            or (None if payment is None or payment.order_id == order_id else BAD_INTENT)
        )
        if refused is not None:
            return refused

        def effect() -> Result[Order]:
            order = self.state.orders.get(order_id)
            if order is None:
                return UNKNOWN_ORDER
            if payment is None:
                return self._claim(order, run_id, dry_run)
            return self._report(order, run_id, payment, dry_run)

        # The store's lock, not its idempotency entries: the owner compare is the whole rule.
        result = self._store.exclusive(effect)
        return TIMEOUT if self.faults.take("claim_payment", FaultKind.TIMEOUT_AFTER) else result

    def _claim(self, order: Order, run_id: str, dry: bool) -> Result[Order]:
        if order.status is OrderStatus.PAYING:
            return Ok(order) if order.claimed_by == run_id else CLAIMED
        if order.status is OrderStatus.CONFIRMED:
            return ALREADY_CONFIRMED
        if order.status is OrderStatus.ESCALATED:
            return ALREADY_ESCALATED
        updated = replace(order, status=OrderStatus.PAYING, claimed_by=run_id)
        return self._keep_claim("claimed", updated, dry)

    def _report(
        self, order: Order, run_id: str, payment: PaymentIntent, dry: bool
    ) -> Result[Order]:
        if order.claimed_by != run_id:
            return CLAIMED
        if order.status not in (OrderStatus.PAYING, OrderStatus.ESCALATED):
            return NOT_PENDING
        updated = replace(
            order,
            intent_id=payment.intent_id,
            amount_minor=payment.authorized_minor,
            currency=payment.currency,
            payment_status=payment.status.value,
            captured_minor=payment.captured_minor,
        )
        if not dry and order.status is OrderStatus.ESCALATED:
            # Escalated while this run was paying: the stored escalation now shows the money.
            kept = dict(self.state.escalations.get(order.order_id, {}))
            before = kept.get("payment")
            caller = before if isinstance(before, Mapping) else None
            label = str(
                (caller or {}).get("claim") or "unreported"
            )  # the store can't tell who escalated (N3)
            kept["payment"] = further_along(caller, claim_payment_view(updated, label))
            self.state.escalations[order.order_id] = MappingProxyType(kept)
        return self._keep_claim("authorization_reported", updated, dry)

    def _keep_claim(self, step: str, order: Order, dry: bool) -> Ok[Order]:
        if dry:
            return Ok(replace(order, dry_run=True))
        self.state.orders[order.order_id] = order
        self.state.claims.append((order.order_id, step))
        return Ok(order)

    def _save(self, operation: str, order: Order, dry: bool) -> Ok[Order]:
        if dry:
            return Ok(replace(order, dry_run=True))
        self.state.orders[order.order_id] = order
        self._effects.append(Effect(operation, order.order_id, order.amount_minor))
        return Ok(order)


def _order(row: Mapping[str, Any]) -> Order:
    return Order(
        order_id=row["order_id"],
        shopper_id=row["shopper_id"],
        region=row["region"],
        lines=tuple(
            OrderLine(line["product_id"], line["variant_id"], int(line["quantity"]))
            for line in row["lines"]
        ),
        payment_token=row["payment_token"],
        shipping_postal_code=row["shipping_postal_code"],
    )


def _jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
