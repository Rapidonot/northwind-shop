"""Return eligibility and return requests for the signed-in shopper's own orders (row #27, CHK-07).

`assess` decides one line of one order from the order store, the carrier's scans and the policy
facts. northwind-policy's `evaluate_return_eligibility` and `ReturnDesk.request` below both use it,
so a request never rests on what a model says was decided: the desk decides again itself.

`ReturnDesk.request` is the core of `request_return` (its MCP handler is in `tools`). It
records the shopper's own request and nothing else: no label, refund or case. Eligible is stored as
`requested`, ambiguous as `needs_review`; ineligible is a business error and stores nothing.

Idempotency (hard rule 9): a key is remembered per (shopper, turn, key), the turn from the verified
token, as for the cart (decision 0019). The same key and arguments return the first outcome; other
arguments are an `idempotency_conflict`. One open request per order line: a second one, whatever its
key, returns the first. Only a stored request is remembered, so a refusal is decided again. It is in
memory, like the cart.

No `dry_run` (D15): `evaluate_return_eligibility` is the dry run."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from threading import RLock
from typing import Any

from northwind_common import returns_policy
from northwind_common.errors import ToolError
from northwind_common.returns_policy import CONDITIONS, Decision
from northwind_mcp_orders.book import OrderBook, not_found
from northwind_mcp_orders.policy_data import PolicyData

KEY = re.compile(r"[A-Za-z0-9_-]{8,64}", re.ASCII)  # fullmatch
MAX_KEYS = 50  # remembered keys per shopper
REQUEST_STATUS: Mapping[str, str] = {"eligible": "requested", "ambiguous": "needs_review"}


def item_not_in_order() -> ToolError:
    return ToolError("validation", False, "item_not_in_order", "That item is not in that order.")


def invalid_condition() -> ToolError:
    return ToolError(
        "validation",
        False,
        "invalid_condition",
        f"The condition must be one of: {', '.join(CONDITIONS)}.",
    )


def invalid_idempotency_key() -> ToolError:
    return ToolError(
        "validation",
        False,
        "invalid_idempotency_key",
        "A return request needs an idempotency_key of 8-64 letters, digits, '-' or '_'.",
    )


def idempotency_conflict() -> ToolError:
    return ToolError(
        "business",
        False,
        "idempotency_conflict",
        "That idempotency_key was already used for a different return request in this turn.",
    )


def policy_unavailable() -> ToolError:
    return ToolError(
        "transient", True, "policy_unavailable", "The policy could not be read. Try again later."
    )


def not_eligible(decision: Decision) -> ToolError:
    return ToolError("business", False, decision.reason, decision.explanation)


@dataclass(frozen=True)
class Assessment:
    order_id: str
    variant_id: str
    condition: str
    decision: Decision

    def as_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "variant_id": self.variant_id,
            "condition": self.condition,
            "outcome": self.decision.outcome,
            "reason": self.decision.reason,
            "explanation": self.decision.explanation,
            "next_step": self.decision.next_step,
            "facts": [fact.as_dict() for fact in self.decision.facts],
        }


def _delivered_on(view: Mapping[str, Any]) -> date | None:
    if view["status"] != "delivered" or view["tracking"] is None:
        return None
    scans = [s["on"] for s in view["tracking"]["scans"] if s["status"] == "delivered"]
    return date.fromisoformat(scans[-1]) if scans else None


def assess(
    book: OrderBook,
    policy: PolicyData,
    *,
    shopper: str,
    region: str | None,
    order_id: str,
    variant_id: Any,
    condition: Any,
    as_of: date = returns_policy.AS_OF,
) -> Assessment:
    """The order is read first, so another shopper's order is the same `not_found` whatever the
    other arguments are. `shopper` and `region` come from the verified token, never an argument."""
    view = book.view(order_id, shopper=shopper, region=region)
    if region is None or region not in returns_policy.WINDOW_DAYS:
        raise not_found()
    if not isinstance(condition, str) or condition not in CONDITIONS:
        raise invalid_condition()
    lines = [line for line in view["lines"] if line["variant_id"] == variant_id]
    if not lines:
        raise item_not_in_order()
    subcategory = policy.subcategories.get(lines[0]["product_id"])
    if subcategory is None:
        raise policy_unavailable()  # fail closed: the product's kind is not known
    decision = returns_policy.evaluate(
        region=region,
        subcategory=subcategory,
        delivered_on=_delivered_on(view),
        condition=condition,
        lines=len(lines),
        as_of=as_of,
    )
    return Assessment(order_id, lines[0]["variant_id"], condition, decision)


@dataclass
class ReturnDesk:
    book: OrderBook
    policy: PolicyData
    # (shopper, order, variant) -> the one open request
    open: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    _keys: dict[str, OrderedDict[tuple[str, str], tuple[str, dict[str, Any]]]] = field(
        default_factory=dict
    )
    _lock: RLock = field(default_factory=RLock)

    def request(
        self,
        *,
        shopper: str,
        region: str | None,
        turn: str,
        order_id: str,
        variant_id: Any,
        condition: Any,
        key: Any,
    ) -> dict[str, Any]:
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise invalid_idempotency_key()
        fingerprint = json.dumps([order_id, variant_id, condition], default=str)
        with self._lock:
            keys = self._keys.setdefault(shopper, OrderedDict())
            seen = keys.get((turn, key))
            if seen is not None:
                if seen[0] != fingerprint:
                    raise idempotency_conflict()
                return {**seen[1], "result": "already_requested"}
            found = assess(
                self.book,
                self.policy,
                shopper=shopper,
                region=region,
                order_id=order_id,
                variant_id=variant_id,
                condition=condition,
            )
            line = (shopper, found.order_id, found.variant_id)
            if line in self.open:
                return {**self.open[line], "result": "already_requested"}
            if found.decision.outcome not in REQUEST_STATUS:
                raise not_eligible(found.decision)
            stored = {
                **found.as_dict(),
                "request_status": REQUEST_STATUS[found.decision.outcome],
            }
            self.open[line] = stored
            keys[(turn, key)] = (fingerprint, stored)
            while len(keys) > MAX_KEYS:
                keys.popitem(last=False)
            return {**stored, "result": "recorded"}
