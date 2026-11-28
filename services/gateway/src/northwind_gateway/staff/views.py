"""OPS-1: the demo staff console's views, read from the real stores. Every count is counted here
from rows; nothing is invented, and an empty store is a valid empty view.

- Orders: the order store's 1,000 synthetic orders, seeded as northwind-orders seeds them
  (`data/backend`: the order rows, their replay labels and the carrier's tracking), with each
  order's shopper status worked out as northwind-orders does (`shopper_status`, pinned equal to
  `northwind_mcp_orders.book.shopper_status` by test_ops1_views.py) and its exception as the reason;
  then the orders Pay (demo) placed since the server started.
- Handoffs: the handoff desk's queue and its audit (`handoff.handoff_stores`).
- Agent activity: the audit events of the handoff desk and the checkout, outcomes only.
- Stock and holds: the checkout's inventory: on hand per node, and its live holds.
The in-memory stores are this process's: their views say "since this server started". Internal-only
products are left out of every view (hard rule 7).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from northwind_backend.audit import AuditEvent, InMemoryAuditSink
from northwind_backend.cases import SUMMARY_LABEL
from northwind_backend.fakes.catalog import FIXTURES
from northwind_backend.fakes.handoffs import HandoffState
from northwind_backend.fakes.orders import DATA, OrderState
from northwind_backend.handoff import HandoffCase
from northwind_backend.inventory import InventoryState
from northwind_backend.inventory.model import HoldStatus
from northwind_backend.ports import OrderStatus
from northwind_gateway.demo_orders import DemoOrders, demo_orders
from northwind_gateway.handoff import HandoffStores, handoff_stores
from northwind_gateway.staff import agent_map, traces

STARTED = datetime.now(UTC)
LATEST = 10
EVENT_LIMIT = 500
SCAN_STATUSES = ("picked_up", "in_transit", "out_for_delivery", "delivered")
_OUTCOMES = {"placed": OrderStatus.CONFIRMED, "escalated": OrderStatus.ESCALATED}
# Reference fields an audit event may carry, in the order a row names one.
_REFS = (
    "order_id",
    "case_id",
    "intent_id",
    "assessment_id",
    "shipment_id",
    "variant_id",
    "shopper_id",
)


@dataclass(frozen=True)
class CheckoutStores:
    """What the console reads of the checkout: its placed orders, its inventory and its audit. Never
    the checkout desk or module itself, so the console cannot reach the confirmation issuer (SA-01's
    pin: only app.py imports checkout.py). app.py registers the provider (`use_checkout`)."""

    orders: OrderState
    inventory: InventoryState
    audit: InMemoryAuditSink


@dataclass(frozen=True)
class Sources:
    """What the views read: this process's stores. A test passes its own."""

    handoffs: HandoffStores | None
    checkout: CheckoutStores
    demo: DemoOrders


_checkout: Callable[[], CheckoutStores] | None = None


def use_checkout(provider: Callable[[], CheckoutStores]) -> None:
    global _checkout
    _checkout = provider


def sources() -> Sources:
    if _checkout is None:
        raise RuntimeError(
            "the checkout's stores were never registered (app.py calls use_checkout)"
        )
    return Sources(handoffs=handoff_stores(), checkout=_checkout(), demo=demo_orders())


def _iso(at: datetime) -> str:
    """UTC to the second, `Z`-suffixed. Whole seconds matter: `10:15:30.123456+00:00` reads as a
    16-digit run to the card check and could be withheld as card data (security review L3)."""
    return at.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_text(at: str) -> str:
    """An audit event's `at` (ISO 8601 text), shown as `_iso` shows a time."""
    try:
        return _iso(datetime.fromisoformat(at))
    except ValueError:
        return ""


def _jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# -- the catalog (public products only, hard rule 7) --------------------------------------------


@dataclass(frozen=True)
class Catalog:
    products: Mapping[str, tuple[str, str]]  # product ID -> (name, department), public only
    variants: tuple[tuple[str, str], ...]  # (variant ID, product ID), public only, file order


@cache
def catalog(fixtures: Path = FIXTURES) -> Catalog:
    products = {
        p["product_id"]: (str(p["name"]), str(p["department"]))
        for p in _jsonl(fixtures / "products.jsonl")
        if p.get("visibility") == "public"
    }
    variants = tuple(
        (str(v["variant_id"]), str(v["product_id"]))
        for v in _jsonl(fixtures / "variants.jsonl")
        if v["product_id"] in products and v.get("visibility", "public") == "public"
    )
    return Catalog(products=products, variants=variants)


# -- orders ---------------------------------------------------------------------------------------


def shopper_status(status: OrderStatus, shipment: bool, scans: Sequence[str]) -> str:
    """What the shop shows for an order, as northwind-orders works it out (`book.shopper_status`):
    escalated is on hold; pending or paying is processing; confirmed is confirmed with no shipment,
    label created with no scans, else the last scan. A status that mapping does not know is
    `unknown` here (northwind-orders fails closed on it)."""
    if status is OrderStatus.ESCALATED:
        return "on_hold"
    if status in (OrderStatus.PENDING, OrderStatus.PAYING):
        return "processing"
    if status is not OrderStatus.CONFIRMED:
        return "unknown"
    if not shipment:
        return "confirmed"
    if not scans:
        return "label_created"
    return scans[-1] if scans[-1] in SCAN_STATUSES else "unknown"


@cache
def store_orders(data: Path = DATA) -> tuple[dict[str, Any], ...]:
    """The 1,000 seeded orders, as rows. Read once: this store is seeded read-only."""
    state = OrderState.from_data(data)
    rows = {row["order_id"]: row for row in _jsonl(data / "orders.jsonl")}
    labels = {label["order_id"]: label for label in _jsonl(data / "replay_labels.jsonl")}
    tracking = {
        row["order_id"]: [e["status"] for e in row["events"]]
        for row in _jsonl(data / "tracking.jsonl")
    }
    out = []
    for order_id, order in state.orders.items():
        label = labels.get(order_id)
        status = (
            _OUTCOMES.get(str(label["expected_outcome"]), order.status) if label else order.status
        )
        row = rows[order_id]
        out.append(
            {
                "order_id": order_id,
                "placed_on": str(row["placed_on"]),
                "shopper_id": order.shopper_id,
                "region": order.region,
                "postal_code": order.shipping_postal_code,
                "lines": len(order.lines),
                "units": sum(line.quantity for line in order.lines),
                "status": shopper_status(status, order_id in tracking, tracking.get(order_id, [])),
                "escalated": status is OrderStatus.ESCALATED,
                "reason": row.get("exception"),
                "source": "store",
            }
        )
    return tuple(out)


def demo_rows(src: Sources) -> list[dict[str, Any]]:
    placed = src.checkout.orders.orders
    out = []
    for shopper, entry in src.demo.everyone():
        order = placed.get(str(entry["order_id"]))
        lines = entry.get("lines", [])
        out.append(
            {
                "order_id": str(entry["order_id"]),
                "placed_on": str(entry["placed_on"]),
                "shopper_id": shopper,
                "region": order.region if order else "",
                "postal_code": order.shipping_postal_code if order else "",
                "lines": len(lines),
                "units": sum(int(line["quantity"]) for line in lines),
                "status": str(entry["status"]),
                "escalated": entry["status"] == "on_hold",
                # The checkout's own escalation code (fraud_review, out_of_stock, placement_error,
                # ...), from its order store; advisor-reviewer finding 1.
                "reason": order.escalation if order and order.escalation else None,
                "source": "demo",
            }
        )
    return out


UNRECORDED = "unrecorded"


def order_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every part adds up to the whole it sits under (the user's look fix, 2026-11-28):
    total = escalated + other_exceptions + processed; exceptions = escalated + other_exceptions;
    the escalated orders' reasons add up to escalated. An exception is a recorded reason or an
    escalation; an escalated order with no recorded reason is counted as `unrecorded`."""
    escalated = [r for r in rows if r["escalated"]]
    reasons = Counter(str(r["reason"]) if r["reason"] else UNRECORDED for r in escalated)
    other = sum(1 for r in rows if r["reason"] and not r["escalated"])
    return {
        "total": len(rows),
        "exceptions": len(escalated) + other,
        "escalated": len(escalated),
        "other_exceptions": other,
        "processed": sum(1 for r in rows if not r["reason"] and not r["escalated"]),
        "escalated_by_reason": dict(sorted(reasons.items())),
    }


def orders_view(src: Sources) -> dict[str, Any]:
    rows = [*store_orders(), *demo_rows(src)]
    return {"counts": order_counts(rows), "rows": rows}


# -- handoffs ---------------------------------------------------------------------------------


def _amount(case: HandoffCase) -> dict[str, Any] | None:
    amounts = case.amounts
    if (
        amounts.order is not None
        and amounts.order.order_total_minor is not None
        and amounts.order.currency
    ):
        return {"minor": amounts.order.order_total_minor, "currency": amounts.order.currency}
    if (
        amounts.cart is not None
        and amounts.cart.subtotal_minor is not None
        and amounts.cart.currency
    ):
        return {"minor": amounts.cart.subtotal_minor, "currency": amounts.cart.currency}
    return None


def handoff_rows(state: HandoffState | None) -> list[dict[str, Any]]:
    if state is None:
        return []
    out: list[dict[str, Any]] = []
    for reference in sorted(set(state.opened) | set(state.cases)):
        case = state.cases.get(reference)
        if case is None:  # opened; the case is still being built
            out.append(
                {
                    "reference": reference,
                    "time": None,
                    "intent": "",
                    "order_id": None,
                    "summary": "",
                    "summary_label": "",
                    "amount": None,
                    "status": "opening",
                }
            )
            continue
        out.append(
            {
                "reference": reference,
                "time": None,  # the queue keeps no time; never a guessed one
                "intent": case.intent,
                "order_id": case.order_id,
                "summary": case.summary,
                "summary_label": SUMMARY_LABEL[case.summary_source],
                "amount": _amount(case),
                "status": "queued",
            }
        )
    return out


def handoffs_view(src: Sources) -> dict[str, Any]:
    return {
        "started_at": _iso(STARTED),
        "handoffs": handoff_rows(src.handoffs.handoffs if src.handoffs else None),
    }


# -- agent activity ---------------------------------------------------------------------------


def outcome_of(outcome: Mapping[str, Any] | None) -> str:
    if not outcome:
        return "error"
    if outcome.get("escalated") is True or outcome.get("status") == "escalated":
        return "escalated"
    if outcome.get("ok") is True:
        return "ok"
    if outcome.get("errorCategory") in ("permission", "validation", "business"):
        return "refused"
    return "error"


def _ref(event: AuditEvent) -> str:
    for where in (event.inputs, event.outcome or {}):
        for name in _REFS:
            value = where.get(name)
            if isinstance(value, str):
                return value
    return event.correlation_id


def _detail(outcome: Mapping[str, Any] | None) -> str:
    if not outcome:
        return ""
    for name in ("code", "status", "decision", "escalation", "root_cause", "errorCategory"):
        value = outcome.get(name)
        if isinstance(value, str):
            return value
    return ""


def activity_events(src: Sources) -> list[dict[str, Any]]:
    sinks: list[Iterable[AuditEvent]] = [src.checkout.audit.read_all()]
    if src.handoffs is not None:
        sinks.append(src.handoffs.audit.read_all())
    events = [e for sink in sinks for e in sink if e.phase == "outcome"]
    events.sort(key=lambda e: (e.at, e.seq), reverse=True)
    return [
        {
            "time": _iso_text(e.at),
            "actor": e.actor,
            "action": e.action,
            "ref": _ref(e),
            "outcome": outcome_of(e.outcome),
            "detail": _detail(e.outcome),
        }
        for e in events
    ]


def activity_view(src: Sources) -> dict[str, Any]:
    """The newest EVENT_LIMIT events, with the total, so a long-running demo stays cheap to read
    (security review L4); the overview counts them all."""
    events = activity_events(src)
    return {"started_at": _iso(STARTED), "events": events[:EVENT_LIMIT], "total": len(events)}


# -- stock and holds --------------------------------------------------------------------------


def live_holds(state: InventoryState, now: datetime) -> list[tuple[str, str, str, int, datetime]]:
    """(order ID, variant ID, node, units, expires at) of each hold still counted against stock."""
    out = []
    for record in state.records.values():
        if record.status is not HoldStatus.HELD or record.expires_at <= now:
            continue
        for hold in record.holds:
            a = hold.allocation
            out.append((record.order_id, a.variant_id, a.node, a.quantity, record.expires_at))
    return out


def stock_view(src: Sources, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    shop = catalog()
    state = src.checkout.inventory
    with state.lock:
        rows = {key: row.on_hand for key, row in state.rows.items()}
        holds = live_holds(state, now)
    held: Counter[tuple[str, str]] = Counter()
    for _, variant, node, units, _ in holds:
        held[(variant, node)] += units
    nodes = sorted({node for _, node in rows})
    variants = []
    for variant, product in shop.variants:
        per_node = {
            node: {"on_hand": rows[(variant, node)], "held": held[(variant, node)]}
            for node in nodes
            if (variant, node) in rows
        }
        if not per_node:
            continue
        name, department = shop.products[product]
        variants.append(
            {
                "product_id": product,
                "product": name,
                "department": department,
                "variant_id": variant,
                "nodes": per_node,
            }
        )
    public = {variant for variant, _ in shop.variants}
    return {
        "started_at": _iso(STARTED),
        "nodes": nodes,
        "holds": [
            {
                "order_id": order_id,
                "variant_id": variant,
                "product": shop.products[dict(shop.variants)[variant]][0],
                "node": node,
                "quantity": units,
                "status": HoldStatus.HELD.value,
                "expires_at": _iso(expires),
            }
            for order_id, variant, node, units, expires in holds
            if variant in public
        ],
        "variants": variants,
    }


# -- the agent system and the overview ----------------------------------------------------------


def system_view(_: Sources) -> dict[str, Any]:
    return {"map": agent_map.generate().to_dict(), "traces": list(traces.TRACES)}


def overview_view(src: Sources) -> dict[str, Any]:
    shop = catalog()
    events = activity_events(src)
    handoffs = handoff_rows(src.handoffs.handoffs if src.handoffs else None)
    rows = [*store_orders(), *demo_rows(src)]
    stock = stock_view(src)
    return {
        "started_at": _iso(STARTED),
        "catalog": {
            "products": len(shop.products),
            "variants": len(shop.variants),
            "orders": len(rows),
        },
        "agent_events": {
            "total": len(events),
            "by_outcome": dict(Counter(e["outcome"] for e in events)),
        },
        "handoffs": {
            "open": len(handoffs),
            "by_status": dict(Counter(h["status"] for h in handoffs)),
        },
        "orders": order_counts(rows),
        "holds": {
            "count": len(stock["holds"]),
            "units": sum(h["quantity"] for h in stock["holds"]),
            "nodes": sorted({h["node"] for h in stock["holds"]}),
        },
        "latest": events[:LATEST],
    }


VIEWS = {
    "overview": overview_view,
    "system": system_view,
    "activity": activity_view,
    "handoffs": handoffs_view,
    "orders": orders_view,
    "stock": stock_view,
}
