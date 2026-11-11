"""Rebuild one order from audit events alone (BE-08, row #20; M1, R1, S1, S2).

Nothing here reads a port, a fake or a result object: only the events given.

Joining: an event belongs to an order if its inputs or outcome name the order, or name an intent
that an `authorize` outcome joined to the order (M1), or a tracking number its shipment outcome did.
So a failed `capture`, `void`, `refund` or `status` is found through its `intent_id`, and a `track`
through its tracking number, attempt and outcome alike.

Uncertain actions (R1, S1). A state-changing action whose outcome was a timeout, an unexpected
error, an `audit_incomplete`, or that has no outcome at all, may have taken effect. All attempts
under one `(action, idempotency_key)` are one logical action. It is done once if any of them has an
`Ok`, whatever the event order. With no `Ok` it is `uncertain`: listed with its amount, never
counted as done, and never reported as absent. A later read can settle it (S2): `payment.status` for
`capture`, `void` and `refund`; `fraud.read` for `assess`. `authorize` and `create_shipment` leave
nothing to read by, so only a same-key `Ok` settles them.

Payment state is read from the intent fields of `Ok` outcomes, never added up, so a replayed `Ok`
can't double `captured_minor`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from northwind_backend.audit import AuditEvent, verify_chain

Settled = Literal["open", "done", "not_done"]

STATE_CHANGING = frozenset(
    {
        "payment.authorize",
        "payment.capture",
        "payment.void",
        "payment.refund",
        "fraud.assess",
        "carrier.create_shipment",
        # Row #21: the order store's writes. No read settles them yet, so only a same-key Ok does.
        "order.address_on_file",
        "order.confirm",
        "order.escalate",
    }
)
ORDER_ACTIONS = frozenset({"order.get", "order.address_on_file", "order.confirm", "order.escalate"})
UNCERTAIN_CODES = frozenset({"timeout", "unexpected_error", "audit_incomplete"})
PAYMENT_ACTIONS = frozenset({"payment.capture", "payment.void", "payment.refund", "payment.status"})


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    seq: int
    correlation_id: str
    actor: str
    action: str
    phase: str
    ok: bool | None
    code: str | None


@dataclass(frozen=True, slots=True)
class Uncertain:
    action: str
    idempotency_key: str | None
    amount_minor: int | None
    settled: Settled


@dataclass(frozen=True, slots=True)
class OrderRecord:
    """One order as its events show it.

    The payment fields (`payment_status`, `captured_minor`, `refunded_minor`) are the *confirmed*
    state only: what an `Ok` outcome proved. They are never the whole state while an `uncertain`
    entry is open. Show `payment_label`, which carries the doubt, or read `uncertain` alongside
    them; never show the confirmed fields alone (row review F8)."""

    order_id: str
    timeline: tuple[TimelineEntry, ...] = ()
    fraud_decision: str | None = None
    fraud_score: int | None = None
    intent_id: str | None = None
    payment_status: str | None = None
    currency: str | None = None
    authorized_minor: int = 0
    captured_minor: int = 0
    refunded_minor: int = 0
    shipment_id: str | None = None
    tracking_number: str | None = None
    scans: int | None = None
    held: str | None = None
    escalated: bool = False
    order_status: str | None = None
    escalation_reason: str | None = None
    unknown: tuple[str, ...] = ()
    uncertain: tuple[Uncertain, ...] = ()
    agent_runs: tuple[str, ...] = ()

    @property
    def payment_label(self) -> str:
        """The payment status, never plainly `authorized` while a capture may have happened."""
        open_money = [
            u.action
            for u in self.uncertain
            if u.settled == "open" and u.action.startswith("payment.")
        ]
        base = self.payment_status or "none"
        return f"{base} ({', '.join(open_money)} uncertain)" if open_money else base


def _str(body: Mapping[str, Any] | None, key: str) -> str | None:
    value = (body or {}).get(key)
    return value if isinstance(value, str) else None


def _int(body: Mapping[str, Any] | None, key: str) -> int | None:
    value = (body or {}).get(key)
    return value if type(value) is int else None


def _is_ok(event: AuditEvent) -> bool:
    return event.phase == "outcome" and bool((event.outcome or {}).get("ok"))


def _intents_of(events: Iterable[AuditEvent], order_id: str) -> set[str]:
    return {
        intent
        for e in events
        if e.action == "payment.authorize"
        and _is_ok(e)
        and _str(e.outcome, "order_id") == order_id
        and not (e.outcome or {}).get("dry_run")
        and (intent := _str(e.outcome, "intent_id")) is not None
    }


def _tracking_of(events: Iterable[AuditEvent], order_id: str) -> set[str]:
    return {
        number
        for e in events
        if e.action == "carrier.create_shipment"
        and _is_ok(e)
        and _str(e.outcome, "order_id") == order_id
        and (number := _str(e.outcome, "tracking_number")) is not None
    }


def _belongs(event: AuditEvent, order_id: str, intents: set[str], tracking: set[str]) -> bool:
    for body in (event.inputs, event.outcome or {}):
        if _str(body, "order_id") == order_id:
            return True
        if _str(body, "intent_id") in intents or _str(body, "tracking_number") in tracking:
            return True
    return False


def _uncertain_outcome(event: AuditEvent) -> bool:
    outcome = event.outcome or {}
    return outcome.get("ok") is False and (
        outcome.get("code") in UNCERTAIN_CODES or "crashed" in outcome
    )


def reconstruct_order(events: Sequence[AuditEvent], order_id: str) -> OrderRecord:
    intents = _intents_of(events, order_id)
    tracking = _tracking_of(events, order_id)
    mine = [e for e in events if _belongs(e, order_id, intents, tracking)]
    if not mine:
        return OrderRecord(order_id=order_id)
    # The runs that touched the order: their own events, and runs nested under the order's flows.
    runs = {e.correlation_id for e in mine if e.action.startswith("agent.")}
    mine += [e for e in events if e.correlation_id in runs and e not in mine]
    mine.sort(key=lambda e: e.seq)

    state: dict[str, Any] = {"order_id": order_id}
    outcomes_by_action: dict[str, str] = {}
    for event in mine:
        if event.phase == "outcome":
            outcomes_by_action[event.action_id] = event.action
        if not _is_ok(event) or (event.outcome or {}).get("dry_run"):
            continue
        out = event.outcome or {}
        if event.action in ("fraud.assess", "fraud.read"):
            items = out.get("items") if event.action == "fraud.read" else [out]
            for item in items or []:
                if _str(item, "order_id") == order_id:
                    state["fraud_decision"] = _str(item, "decision")
                    state["fraud_score"] = _int(item, "score")
        elif event.action == "payment.authorize" or event.action in PAYMENT_ACTIONS:
            if _str(out, "intent_id") in intents:
                state["intent_id"] = _str(out, "intent_id")
                state["payment_status"] = _str(out, "status")
                state["currency"] = _str(out, "currency")
                for key in ("authorized_minor", "captured_minor", "refunded_minor"):
                    state[key] = _int(out, key) or 0
        elif event.action == "carrier.create_shipment":
            state["shipment_id"] = _str(out, "shipment_id")
            state["tracking_number"] = _str(out, "tracking_number")
        elif event.action == "carrier.track":
            if _str(out, "tracking_number") == state.get("tracking_number"):
                state["scans"] = _int(out, "scans")
        elif event.action in ORDER_ACTIONS and _str(out, "order_id") == order_id:
            state["order_status"] = _str(out, "status")
            if event.action == "order.escalate":
                state["escalated"] = True
                state["escalation_reason"] = _str(out, "escalation")
        elif event.action == "order.held":
            state["held"] = _str(event.inputs, "reason")
            state["escalated"] = bool(out.get("escalated"))

    unknown = tuple(
        e.action_id for e in mine if e.phase == "attempt" and e.action_id not in outcomes_by_action
    )
    timeline = tuple(
        TimelineEntry(
            seq=e.seq,
            correlation_id=e.correlation_id,
            actor=e.actor,
            action=e.action,
            phase=e.phase,
            ok=None if e.outcome is None else bool(e.outcome.get("ok")),
            code=_str(e.outcome, "code"),
        )
        for e in mine
        if not e.action.startswith("agent.")
    )
    return OrderRecord(
        timeline=timeline,
        unknown=unknown,
        uncertain=_uncertain(mine, set(unknown), intents, order_id),
        agent_runs=tuple(sorted(runs)),
        **state,
    )


def _uncertain(
    mine: list[AuditEvent], unknown: set[str], intents: set[str], order_id: str
) -> tuple[Uncertain, ...]:
    groups: dict[tuple[str, str], list[AuditEvent]] = {}
    for event in mine:
        # A dry run skips the idempotency store, so it proves nothing about the real call: it is
        # never part of a group and never settles one (row review F2).
        if event.action not in STATE_CHANGING or event.inputs.get("dry_run") is True:
            continue
        key = _str(event.inputs, "idempotency_key")
        # A key written only as type and length can't be read back, so such calls are never
        # merged: each stands alone, by its own action ID (F2, related low).
        slot = (event.action, key if key is not None else f"#{event.action_id}")
        groups.setdefault(slot, []).append(event)
    found: list[Uncertain] = []
    for (action, slot_key), group in groups.items():
        key = None if slot_key.startswith("#") else slot_key
        # Settled by key (S1): any Ok under it, whatever the event order.
        outcome_known = any(_is_ok(e) for e in group)
        doubtful = [
            e
            for e in group
            if (e.phase == "outcome" and _uncertain_outcome(e))
            or (e.phase == "attempt" and e.action_id in unknown)
        ]
        if not doubtful:
            continue
        first, last = doubtful[0], doubtful[-1]
        # Only a read after the last doubtful attempt can settle it: a read between two attempts
        # says nothing about the later one (row review F1).
        settled: Settled = (
            "done" if outcome_known else _settle_by_read(action, key, first, last, mine, order_id)
        )
        found.append(
            Uncertain(
                action=action,
                idempotency_key=key,
                amount_minor=_amount_of(action, first, mine, intents),
                settled=settled,
            )
        )
    return tuple(found)


def _amount_of(
    action: str, event: AuditEvent, mine: list[AuditEvent], intents: set[str]
) -> int | None:
    amount = _int(event.inputs, "amount_minor")
    if amount is not None or action != "payment.capture":
        return amount
    # A capture with no amount takes the whole authorization.
    intent = _str(event.inputs, "intent_id")
    for e in mine:
        if e.action == "payment.authorize" and _is_ok(e) and _str(e.outcome, "intent_id") == intent:
            return _int(e.outcome, "authorized_minor")
    return None


def _settle_by_read(
    action: str,
    key: str | None,
    first: AuditEvent,
    last: AuditEvent,
    mine: list[AuditEvent],
    order_id: str,
) -> Settled:
    """Reads after `last`, the group's last doubtful event; a refund's baseline is the amount
    refunded before `first`, where the logical action began.

    A refund total can also grow from another key's refund, so a doubtful refund is left `open`
    when any other refund on the same intent succeeded or was doubtful after `first`: a later
    read can't tell whose refund it shows (re-review L1). With an unreadable key, "another" is
    told apart by action ID, since two unreadable keys compare equal (#20 advisor re-review N1)."""
    event = first
    later = [e for e in mine if e.seq > last.seq and _is_ok(e)]
    if action in ("payment.capture", "payment.void", "payment.refund"):
        intent = _str(event.inputs, "intent_id")
        reads = [
            e
            for e in later
            if e.action == "payment.status" and _str(e.outcome, "intent_id") == intent
        ]
        if not reads:
            return "open"
        out = reads[-1].outcome or {}
        if action == "payment.capture":
            return "done" if (_int(out, "captured_minor") or 0) > 0 else "not_done"
        if action == "payment.void":
            return "done" if out.get("status") == "voided" else "not_done"
        others = [
            e
            for e in mine
            if e.action == "payment.refund"
            and e.seq > first.seq
            and _str(e.inputs, "intent_id") == intent
            and e.inputs.get("dry_run") is not True
            and (
                _str(e.inputs, "idempotency_key") != key
                if key is not None
                else e.action_id != first.action_id
            )
        ]
        if others:
            return "open"
        before = _refunded_before(event, mine, intent)
        return "done" if (_int(out, "refunded_minor") or 0) > before else "not_done"
    if action == "fraud.assess":
        reads = [
            e for e in later if e.action == "fraud.read" and _str(e.inputs, "order_id") == order_id
        ]
        if not reads:
            return "open"
        return "not_done" if (reads[-1].outcome or {}).get("empty") else "done"
    return "open"  # authorize and create_shipment: only a same-key Ok settles them (S2)


def _refunded_before(event: AuditEvent, mine: list[AuditEvent], intent: str | None) -> int:
    seen = 0
    for e in mine:
        if e.seq >= event.seq:
            break
        if _is_ok(e) and e.action in PAYMENT_ACTIONS and _str(e.outcome, "intent_id") == intent:
            seen = _int(e.outcome, "refunded_minor") or 0
    return seen


def reconstruct_checked(
    events: Sequence[AuditEvent], order_id: str
) -> tuple[OrderRecord, list[str]]:
    """The record, with the whole chain's problems: empty means the events verified. A record from
    a chain that doesn't verify is evidence of tampering, not of the order (security review L4)."""
    return reconstruct_order(events, order_id), verify_chain(events)
