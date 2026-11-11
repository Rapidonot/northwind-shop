"""The audit log: one event per phase of every agent action (BE-08, row #20; NFR-10's base).

Every action writes an `attempt` before it runs and one `outcome` after. Each event carries the five
fields BE-08 names: actor, action, inputs by reference, outcome and correlation ID.

Inputs and outcomes are references, never raw values (L1, N8). Every field is screened in this
order:
  1. card data, in a string or a number's digits: `{"refused": "card_data"}`, no digest or length;
  2. an allow-listed reference field whose value matches its pattern: written as is;
  3. anything else, a payment token, a postal code, free text, a malformed ID: `{"type", "length"}`.
No message, model text or digest is ever written.

The log is append-only and tamper-evident within stated limits (M6). Sinks offer only `append` and
`read_all`, and each event's hash chains to the one before. `verify_chain` finds an edit, an inner
delete, a reorder or an insert. It can't find two things, by design until NFR-10 adds an outside
anchor: a truncated tail, which is a valid shorter chain, and a full recompute by anyone who can
write the file. Both are pinned as known limits in the tests. `reconstruct_order` trusts what it is
given; `reconstruct_checked` also reports the chain's problems (security review L4). An in-memory
sink hands out copies, so a reader can't edit the log in place.

Action names here are `payment.capture` and so on. The mapping from port method names lives only in
`views.py` (R7).
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol

from northwind_backend.carriers import CARRIERS, TRACKING_NUMBER
from northwind_backend.errors import Ok, ToolError
from northwind_backend.ports import (
    FraudAssessment,
    Order,
    PaymentIntent,
    PriceQuote,
    Shipment,
    StockLevel,
    Tracking,
)
from northwind_common.cards import contains_cvv, contains_pan

GENESIS = "0" * 64
MAX_MINOR = 1_000_000_000  # the fakes' MAX_AMOUNT_MINOR
Phase = Literal["attempt", "outcome"]

_LONG_DIGITS = re.compile(r"\d(?:[\W_]?\d){12}")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,47}", re.ASCII)
_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "order_id": re.compile(r"O\d{6}", re.ASCII),
    "intent_id": re.compile(r"pi_(?:\d{6}|dry_run)", re.ASCII),
    "shopper_id": re.compile(r"S\d{4}", re.ASCII),
    "assessment_id": re.compile(r"fa_(?:\d{6}|dry_run)", re.ASCII),
    "shipment_id": re.compile(r"sh_(?:\d{6}|dry_run)", re.ASCII),
    "tracking_number": TRACKING_NUMBER,
    "idempotency_key": re.compile(r"[A-Za-z0-9_.:-]{1,128}", re.ASCII),
    "currency": re.compile(r"[A-Z]{3}", re.ASCII),
    "region": re.compile(r"US|CA|UK|EU"),
    "service": re.compile(r"standard|express"),
    "status": _CODE,
    "decision": _CODE,
    "reason": _CODE,
    "code": _CODE,
    "errorCategory": _CODE,
    "last_status": _CODE,
    "escalation": _CODE,
    "variant_id": re.compile(r"P\d{4}(?:-[A-Z0-9]{1,16}){1,4}", re.ASCII),
}
_MINOR_FIELDS = frozenset({"amount_minor", "authorized_minor", "captured_minor", "refunded_minor"})
_COUNT_FIELDS = frozenset(
    {"score", "scans", "attempts", "steps_ok", "extra_args", "on_hand", "lines"}
)
# A field name is written only if it is a short code-style name (security review M1).
_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,47}", re.ASCII)
# Actors and correlation IDs are code-chosen labels, never request text (security review L3).
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", re.ASCII)
_BOOL_FIELDS = frozenset({"dry_run", "ok", "empty", "escalated", "isRetryable", "address_on_file"})
# An order is written by these fields only: never its token, delivery address or lines (row #21).
_ORDER_FIELDS = (
    "order_id",
    "shopper_id",
    "region",
    "status",
    "intent_id",
    "amount_minor",
    "currency",
    "escalation",
    "address_on_file",
    "dry_run",
)


def card_data(value: object) -> bool:
    """Hard rule 1: a number is screened by its digits too (N8)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        value = str(value)
    if not isinstance(value, str):
        return False
    return contains_pan(value) or contains_cvv(value) or _LONG_DIGITS.search(value) is not None


def _shape(value: object) -> dict[str, Any]:
    try:
        length = len(value)  # type: ignore[arg-type]
    except TypeError:
        length = len(str(value))
    return {"type": type(value).__name__, "length": length}


def _ref(name: str, value: object) -> Any:
    if card_data(value):
        return {"refused": "card_data"}
    if isinstance(value, Enum):
        value = value.value
    if value is None:
        return None
    pattern = _ID_PATTERNS.get(name)
    if pattern is not None:
        return value if isinstance(value, str) and pattern.fullmatch(value) else _shape(value)
    if name in _MINOR_FIELDS:
        ok = type(value) is int and 0 <= value <= MAX_MINOR
        return value if ok else _shape(value)
    if name in _COUNT_FIELDS:
        return value if type(value) is int and 0 <= value <= 10_000 else _shape(value)
    if name in _BOOL_FIELDS:
        return value if type(value) is bool else _shape(value)
    if name == "carrier":
        return value if value in CARRIERS else _shape(value)
    if name == "reasons":
        if isinstance(value, list | tuple) and all(
            isinstance(v, str) and _CODE.fullmatch(v) for v in value
        ):
            return list(value)
        return _shape(value)
    return _shape(value)


def input_refs(inputs: Mapping[str, object]) -> dict[str, Any]:
    """Names are screened too (security review M1): a name that isn't a short code-style name, or
    that carries card data, is never written; such fields are only counted, as `extra_args`."""
    refs: dict[str, Any] = {}
    extra = 0
    for name, value in inputs.items():
        if not isinstance(name, str) or card_data(name) or not _FIELD_NAME.fullmatch(name):
            extra += 1
            continue
        refs[name] = _ref(name, value)
    if extra:
        counted = refs.get("extra_args")
        refs["extra_args"] = extra + (counted if type(counted) is int else 0)
    return refs


def _value_ref(value: object) -> dict[str, Any]:
    if isinstance(value, Order):
        return {
            **{name: _ref(name, getattr(value, name)) for name in _ORDER_FIELDS},
            "lines": len(value.lines),
        }
    if isinstance(value, PaymentIntent | Shipment | FraudAssessment | PriceQuote | StockLevel):
        return {f.name: _ref(f.name, getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Tracking):
        last = value.events[-1].status if value.events else None
        return {
            "tracking_number": _ref("tracking_number", value.tracking_number),
            "carrier": _ref("carrier", value.carrier),
            "scans": len(value.events),
            "last_status": _ref("last_status", last),
        }
    return {"type": type(value).__name__}


def outcome_ref(result: object) -> dict[str, Any]:
    """The outcome of a port call, by reference. An error keeps its code and drops its message."""
    if isinstance(result, ToolError):
        return {
            "ok": False,
            "errorCategory": result.error_category.value,
            "code": _ref("code", result.code),
            "isRetryable": result.is_retryable,
        }
    if isinstance(result, Ok):
        value = result.value
        if isinstance(value, tuple):
            return {"ok": True, "empty": not value, "items": [_value_ref(v) for v in value]}
        return {"ok": True, **_value_ref(value)}
    return {"ok": False, "crashed": type(result).__name__}


@dataclass(frozen=True, slots=True)
class AuditEvent:
    seq: int
    event_id: str
    at: str
    correlation_id: str
    parent_id: str | None
    actor: str
    action: str
    phase: Phase
    action_id: str
    inputs: Mapping[str, Any]
    outcome: Mapping[str, Any] | None
    prev_hash: str
    hash: str

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["inputs"] = dict(self.inputs)
        body["outcome"] = None if self.outcome is None else dict(self.outcome)
        return body

    @classmethod
    def from_dict(cls, body: Mapping[str, Any]) -> AuditEvent:
        return cls(**{f.name: body[f.name] for f in fields(cls)})


_DRAFT_KEYS = (
    "at",
    "correlation_id",
    "parent_id",
    "actor",
    "action",
    "phase",
    "action_id",
    "inputs",
    "outcome",
)


def _canonical(body: Mapping[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(prev_hash: str, seq: int, draft: Mapping[str, Any]) -> str:
    body = {"seq": seq, **{k: draft[k] for k in _DRAFT_KEYS}}
    return hashlib.sha256((prev_hash + _canonical(body)).encode("utf-8")).hexdigest()


def _seal(draft: Mapping[str, Any], seq: int, prev_hash: str) -> AuditEvent:
    clean = {k: draft[k] for k in _DRAFT_KEYS}
    # A round trip through JSON, so what is hashed is exactly what a reader will load.
    clean = json.loads(_canonical(clean))
    return AuditEvent(
        seq=seq,
        event_id=f"ev_{seq:08d}",
        prev_hash=prev_hash,
        hash=_hash(prev_hash, seq, clean),
        **clean,
    )


def verify_chain(events: Sequence[AuditEvent]) -> list[str]:
    """Problems found, empty if none. See the module docstring for what this can't find."""
    problems: list[str] = []
    prev = GENESIS
    for index, event in enumerate(events):
        if event.seq != index:
            problems.append(f"event {index}: seq {event.seq}")
        if event.prev_hash != prev:
            problems.append(f"event {index}: broken link")
        if event.event_id != f"ev_{event.seq:08d}":
            problems.append(f"event {index}: event_id")
        draft = {k: getattr(event, k) for k in _DRAFT_KEYS}
        draft["inputs"] = dict(event.inputs)
        draft["outcome"] = None if event.outcome is None else dict(event.outcome)
        if _hash(event.prev_hash, event.seq, draft) != event.hash:
            problems.append(f"event {index}: hash mismatch")
        prev = event.hash
    return problems


class AuditSink(Protocol):
    def append(self, draft: Mapping[str, Any]) -> AuditEvent: ...

    def read_all(self) -> tuple[AuditEvent, ...]: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.__events: list[AuditEvent] = []
        self.__lock = threading.Lock()

    def append(self, draft: Mapping[str, Any]) -> AuditEvent:
        # One lock over seq, the link and the append, so threads can't break the chain (N2).
        with self.__lock:
            prev = self.__events[-1].hash if self.__events else GENESIS
            event = _seal(draft, len(self.__events), prev)
            self.__events.append(event)
            # A copy, so a caller holding the result can't edit the log either (re-review N2).
            return copy.deepcopy(event)

    def read_all(self) -> tuple[AuditEvent, ...]:
        """Copies, so a reader can't edit the log in place (security review L4)."""
        with self.__lock:
            return tuple(copy.deepcopy(self.__events))


class JsonlAuditSink:
    """One fsynced JSON line per event, opened with O_APPEND and never truncated.

    Opening refuses a file whose chain doesn't verify, so nothing is appended to a broken log. Each
    append takes an exclusive `flock` and reads the last event under it, so two sinks or two
    processes on one path extend one chain rather than forking it (security review L4)."""

    def __init__(self, path: Path) -> None:
        self.__path = path
        self.__lock = threading.Lock()
        problems = verify_chain(self.__load())
        if problems:
            raise AuditWriteError("refusing to append to a log whose chain doesn't verify")

    def __load(self) -> list[AuditEvent]:
        if not self.__path.exists():
            return []
        with self.__path.open(encoding="utf-8") as handle:
            return [AuditEvent.from_dict(json.loads(line)) for line in handle if line.strip()]

    @staticmethod
    def __last(fd: int) -> AuditEvent | None:
        size = os.fstat(fd).st_size
        if size == 0:
            return None
        chunk = 65_536
        while True:
            start = max(0, size - chunk)
            data = os.pread(fd, size - start, start)
            lines = [line for line in data.split(b"\n") if line.strip()]
            if start == 0 or len(lines) > 1:
                return AuditEvent.from_dict(json.loads(lines[-1]))
            chunk *= 4

    def append(self, draft: Mapping[str, Any]) -> AuditEvent:
        with self.__lock:
            fd = os.open(self.__path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                last = self.__last(fd)
                seq = 0 if last is None else last.seq + 1
                prev = GENESIS if last is None else last.hash
                event = _seal(draft, seq, prev)
                os.write(fd, (_canonical(event.to_dict()) + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)  # also releases the flock
            return event

    def read_all(self) -> tuple[AuditEvent, ...]:
        with self.__lock:
            return tuple(self.__load())


class AuditWriteError(Exception):
    """The sink refused a write. Carries no detail: the cause's text goes nowhere."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AuditContext:
    """Who is acting, under which correlation ID. Bound into each run's views and tool handlers, so
    nothing depends on context variables reaching the SDK's tasks (L4).

    The actor, correlation ID and parent ID are code-chosen labels, checked here, never request
    text. Idempotency keys are written as they are once they pass the card check and their pattern,
    so callers derive them from IDs (`O000123:capture`), never from a person's details."""

    def __init__(
        self,
        sink: AuditSink,
        *,
        correlation_id: str,
        actor: str,
        parent_id: str | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        for label in (correlation_id, actor, parent_id):
            if label is not None and (card_data(label) or not _LABEL.fullmatch(label)):
                raise ValueError("actor and correlation IDs must be short code-chosen labels")
        self.sink = sink
        self._correlation_id = correlation_id
        self._actor = actor
        self._parent_id = parent_id
        self._clock = clock or _now
        self._count = 0
        self._lock = threading.Lock()

    # Read-only, so a label checked in `__init__` can't be swapped for request text later
    # (re-review N3).
    @property
    def correlation_id(self) -> str:
        return self._correlation_id

    @property
    def actor(self) -> str:
        return self._actor

    @property
    def parent_id(self) -> str | None:
        return self._parent_id

    def child(self, *, actor: str, correlation_id: str) -> AuditContext:
        return AuditContext(
            self.sink,
            correlation_id=correlation_id,
            actor=actor,
            parent_id=self.correlation_id,
            clock=self._clock,
        )

    def _next_action_id(self) -> str:
        with self._lock:
            self._count += 1
            return f"{self.correlation_id}/a{self._count:05d}"

    def _write(
        self,
        phase: Phase,
        action_id: str,
        action: str,
        refs: Mapping[str, Any],
        outcome: Mapping[str, Any] | None,
    ) -> None:
        draft = {
            "at": self._clock(),
            "correlation_id": self.correlation_id,
            "parent_id": self.parent_id,
            "actor": self.actor,
            "action": action,
            "phase": phase,
            "action_id": action_id,
            "inputs": dict(refs),
            "outcome": None if outcome is None else dict(outcome),
        }
        try:
            self.sink.append(draft)
        except Exception as error:  # noqa: BLE001 - a sink failure has one meaning to callers
            raise AuditWriteError from error

    def attempt(
        self,
        action: str,
        inputs: Mapping[str, object],
        *,
        labels: Mapping[str, str] | None = None,
    ) -> str:
        """Writes the attempt and returns its action ID. `labels` are code-chosen constants, such as
        an allowed tool's name; they still go through the card check."""
        action_id = self._next_action_id()
        self._write("attempt", action_id, action, self.refs(inputs, labels), None)
        return action_id

    def outcome(
        self,
        action_id: str,
        action: str,
        inputs: Mapping[str, object],
        outcome: Mapping[str, Any],
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        self._write("outcome", action_id, action, self.refs(inputs, labels), outcome)

    def record(
        self,
        action: str,
        inputs: Mapping[str, object],
        outcome: Mapping[str, Any],
        *,
        labels: Mapping[str, str] | None = None,
    ) -> str:
        action_id = self.attempt(action, inputs, labels=labels)
        self.outcome(action_id, action, inputs, outcome, labels=labels)
        return action_id

    @staticmethod
    def refs(
        inputs: Mapping[str, object], labels: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        refs = input_refs(inputs)
        for name, value in (labels or {}).items():
            refs[name] = {"refused": "card_data"} if card_data(value) else value
        return refs
