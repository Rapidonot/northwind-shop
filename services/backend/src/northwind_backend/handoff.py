"""The human handoff: a shopper's request for a person becomes a case staff can act on without the
transcript (row #23, SA-06; PRD §7, Care row; decision 0025).

The shopper asks through the portal (a button, or a message the gateway's deterministic matcher
reads as a request for a person). The gateway, never a model, asks this desk for the handoff. The
Shopper Assistant has no tool that reaches here (hard rule 8, PRD §7: no case-assignment tool).

The case holds what SA-06's acceptance lists, and code fills in every field but the summary:
- identity: the signed-in shopper, or an opaque reference to the anonymous session (never the
  session ID itself), and the region;
- intent: one of a closed set, from keyword rules over the screened shopper turns (`intent_of`),
  never model text;
- root cause: with an order the shopper owns, BE-07's (`root_cause_of`, from the order store);
  without one, `shopper_requested`;
- amounts: with an order, BE-07's (from the order store); without one, the live cart the gateway
  read at request time (hard rule 2: a live tool); an empty or unreadable cart is `no_amounts`;
- recommended action: with an order, BE-07's action table; without one, `HANDOFF_ACTIONS`.
The summary is the Care agent's (BE-07's `run_care`, or `run_summary` with no order), labelled as
model-written from an untrusted transcript. If the Care run fails, the case is still queued, with
`summary_missing`.

"Honored immediately": `HandoffDesk.request` opens the handoff (one per identity, under an
idempotency key, hard rule 9) and returns its reference at once; the Care run happens after, in a
task the desk holds. The gateway running this in-process is a lab simplification (decision 0025):
a real deployment puts a queue between the portal and the backend crew.

A case never holds transcript text, the session ID, a payment token, a postal code or card data.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, Protocol, runtime_checkable

from northwind_backend.agents.care import (
    MAX_TRANSCRIPT_TURNS,
    CarePorts,
    CareTask,
    Turn,
    run_care,
    run_summary,
)
from northwind_backend.audit import AuditContext, AuditWriteError, card_data
from northwind_backend.cases import NO_SUMMARY, SUMMARY_LABEL, Case, CaseAmounts, SummarySource
from northwind_backend.errors import Ok, Result, transient
from northwind_backend.recovery import RetryPolicy, call_with_recovery
from northwind_backend.views import CARE_READ, AuditedOrderStore
from northwind_common.llm import AgentRuntime

log = logging.getLogger(__name__)

ACTOR = "handoff-desk"
MAX_HANDOFF_TURNS = 10
# Security review M1: what one process holds. Over `MAX_OPEN_HANDOFFS`, a new identity is refused
# with a fixed transient error; over `MAX_CARE_RUNS` at once, the case is built by code alone
# (`care_busy`, `summary_missing`), so the handoff still opens at once.
MAX_OPEN_HANDOFFS = 1000
MAX_CARE_RUNS = 2
DESK_FULL = transient(
    "handoffs_full", "No more handoffs can be opened right now. Please try again later."
)
if MAX_HANDOFF_TURNS > MAX_TRANSCRIPT_TURNS:  # a handoff's turns must fit one Care task
    raise RuntimeError("MAX_HANDOFF_TURNS exceeds the Care task's bound")
SHOPPER_REQUESTED = "shopper_requested"
CART_LABEL = "cart at handoff (live cart read when the shopper asked)"
# Security review L4: the portal sends the turns and labels their roles, so an "assistant" turn is
# as unverified as a shopper's.
TRANSCRIPT_LABEL = (
    "sent by the shopper's browser, assistant turns included; roles and wording are unverified"
)
REFERENCE = re.compile(r"H\d{6}", re.ASCII)
REGIONS = frozenset({"US", "CA", "UK", "EU"})
_SHOPPER = re.compile(r"S\d{4}", re.ASCII)
# 32 letters a-p, one per nibble of an HMAC: no digits, so no run of them can look like card data to
# the queue's and the audit's card checks (hex would, by chance, in a few percent of sessions).
_SESSION_REF = re.compile(r"[a-p]{32}", re.ASCII)
_VARIANT = re.compile(r"[A-Z]\d{4,8}(?:-[A-Z0-9]{1,12}){0,4}", re.ASCII)
_ORDER_ID = re.compile(r"(?<![A-Za-z0-9])O\d{6}(?!\d)", re.ASCII)
_MONEY = re.compile(r"\d{1,9}\.\d{2}", re.ASCII)
_CURRENCY = re.compile(r"[A-Z]{3}", re.ASCII)

Intent = Literal[
    "order_issue", "return_or_refund", "delivery", "payment", "product_question", "other"
]
INTENTS: tuple[Intent, ...] = (
    "order_issue",
    "return_or_refund",
    "delivery",
    "payment",
    "product_question",
    "other",
)
Trigger = Literal["button", "message"]
TRIGGERS: frozenset[str] = frozenset({"button", "message"})
AmountsSource = Literal["order", "cart", "none"]

# Code, never model text: what staff should do for a handoff with no order, per intent.
HANDOFF_ACTIONS: Mapping[Intent, str] = {
    "order_issue": "ask the shopper for the order ID, read the order, then answer them",
    "return_or_refund": "ask the shopper for the order ID and check return eligibility",
    "delivery": "ask the shopper for the order ID and check the shipment's tracking",
    "payment": "ask the shopper for the order ID and read the payment state; never take card "
    "details in chat",
    "product_question": "answer the shopper's product question from the catalog and guides",
    "other": "read the summary and answer the shopper",
}

# Keyword rules, checked in this order on the screened shopper turns (casefolded, NFKC).
_INTENT_RULES: tuple[tuple[Intent, re.Pattern[str]], ...] = (
    ("return_or_refund", re.compile(r"\b(?:return|returns|returning|refund\w*|exchange\w*)\b")),
    (
        "payment",
        re.compile(r"\b(?:pay|paid|payment\w*|charge[ds]?|charged|declined|billing|billed|card)\b"),
    ),
    (
        "delivery",
        re.compile(
            r"\b(?:deliver\w*|shipping|shipment|shipped|ship|tracking|track|arriv\w*|"
            r"parcel|package|courier|late|lost)\b"
        ),
    ),
    ("order_issue", re.compile(r"\b(?:order|orders|ordered|cancel\w*|o\d{6})\b")),
    (
        "product_question",
        re.compile(
            r"\b(?:size|sizes|sizing|fit|fits|colou?rs?|material|waterproof|warm\w*|jacket\w*|"
            r"boots?|tents?|sleeping bag|backpack\w*|layer\w*|product\w*|stock|wear)\b"
        ),
    ),
)


# ---- the request --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Identity:
    """Who asked: a signed-in shopper (`S1234`), or an anonymous session by an opaque reference
    (32 letters a-p, an HMAC the gateway derives from the session ID; never the ID itself)."""

    kind: Literal["shopper", "session"]
    ref: str
    region: str

    def __post_init__(self) -> None:
        pattern = {"shopper": _SHOPPER, "session": _SESSION_REF}.get(self.kind)
        if pattern is None or not isinstance(self.ref, str) or not pattern.fullmatch(self.ref):
            raise ValueError("an identity is a shopper ID or a session reference")
        if self.region not in REGIONS:
            raise ValueError("unknown region")

    def key(self) -> str:
        """The handoff's idempotency key: one open handoff per identity (hard rule 9)."""
        return f"handoff:{self.kind}:{self.ref}"


@dataclass(frozen=True, slots=True)
class CartLine:
    variant_id: str
    quantity: int
    line_total_minor: int | None


@dataclass(frozen=True, slots=True)
class CartSnapshot:
    """The live cart as `get_cart` returned it to the gateway, in minor units. Only IDs,
    quantities and amounts are kept: no product names, no stock."""

    lines: tuple[CartLine, ...]
    subtotal_minor: int | None
    currency: str | None

    @classmethod
    def from_view(cls, view: object) -> CartSnapshot | None:
        """A strict read of the cart view: anything malformed is None (`no_amounts`), never a
        guess."""
        if not isinstance(view, Mapping):
            return None
        raw_lines = view.get("lines")
        if not isinstance(raw_lines, list):
            return None
        lines: list[CartLine] = []
        for raw in raw_lines:
            if not isinstance(raw, Mapping):
                return None
            variant, quantity = raw.get("variant_id"), raw.get("quantity")
            if not isinstance(variant, str) or not _VARIANT.fullmatch(variant):
                return None
            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or not 0 < quantity < 1000
            ):
                return None
            lines.append(CartLine(variant, quantity, _minor(raw.get("line_total"))))
        currency = view.get("currency")
        if currency is not None and not (
            isinstance(currency, str) and _CURRENCY.fullmatch(currency)
        ):
            return None
        return cls(tuple(lines), _minor(view.get("subtotal")), currency)


def _minor(amount: object) -> int | None:
    """ "12.50" -> 1250; anything else is None."""
    if not isinstance(amount, str) or not _MONEY.fullmatch(amount):
        return None
    units, cents = amount.split(".")
    return int(units) * 100 + int(cents)


@dataclass(frozen=True, slots=True)
class HandoffRequest:
    """What the gateway sends: built from server-side data only (identity, cart) plus the
    shopper's transcript, which is untrusted. A turn with card data is refused here as well as at
    the gateway (hard rule 1)."""

    identity: Identity
    trigger: Trigger
    turns: tuple[Turn, ...] = field(default=())
    cart: CartSnapshot | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, Identity):
            raise ValueError("an identity is required")
        if self.trigger not in TRIGGERS:
            raise ValueError("a handoff's trigger is the button or a message")
        turns = tuple(self.turns)
        if len(turns) > MAX_HANDOFF_TURNS or not all(isinstance(t, Turn) for t in turns):
            raise ValueError(f"at most {MAX_HANDOFF_TURNS} turns")
        if any(
            card_data(t.text) or card_data(unicodedata.normalize("NFKC", t.text)) for t in turns
        ):
            raise ValueError("a turn holds card data")
        object.__setattr__(self, "turns", turns)


def intent_of(turns: tuple[Turn, ...], *, has_order: bool = False) -> Intent:
    """The handoff's intent, from the screened shopper turns only, by the first rule that matches.
    With an order and nothing more specific, it is `order_issue`."""
    text = " ".join(
        unicodedata.normalize("NFKC", t.text).casefold() for t in turns if t.role == "shopper"
    )
    for intent, pattern in _INTENT_RULES:
        if intent == "product_question" and has_order:
            continue
        if pattern.search(text):
            return intent
    return "order_issue" if has_order else "other"


# ---- the case -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HandoffAmounts:
    source: AmountsSource
    order: CaseAmounts | None = None
    cart: CartSnapshot | None = None


@dataclass(frozen=True, slots=True)
class HandoffCase:
    reference: str
    identity: Identity
    trigger: Trigger
    intent: Intent
    root_cause: str
    recommended_action: str
    amounts: HandoffAmounts
    summary: str
    summary_source: SummarySource
    flags: tuple[str, ...]
    order_id: str | None
    order_case_id: str | None
    correlation_id: str
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["flags"] = list(self.flags)
        body["summary_label"] = SUMMARY_LABEL[self.summary_source]
        body["transcript_label"] = TRANSCRIPT_LABEL
        if self.amounts.source == "cart":
            body["amounts"]["label"] = CART_LABEL
        return body

    def render(self) -> str:
        """The case as staff read it: plain text, one field per line. The summary line says the
        transcript, assistant turns included, came from the shopper's browser (security review
        L4): its roles are not verified."""
        who = (
            f"shopper {self.identity.ref}"
            if self.identity.kind == "shopper"
            else f"anonymous session {self.identity.ref}"
        )
        lines = [
            f"Handoff {self.reference} ({who}, {self.identity.region}; asked by {self.trigger})",
            f"Intent: {self.intent}",
            f"Root cause: {self.root_cause}",
            f"Recommended action: {self.recommended_action}",
            f"Amounts: {_render_amounts(self.amounts)}",
            f"Order: {self.order_id or 'none'}"
            + (f" (case {self.order_case_id})" if self.order_case_id else ""),
            f"Flags: {', '.join(self.flags) or 'none'}",
            f"Summary ({SUMMARY_LABEL[self.summary_source]}): {self.summary}",
            f"Transcript: {TRANSCRIPT_LABEL}",
            f"Correlation ID: {self.correlation_id}",
        ]
        return "\n".join(lines)


def _render_amounts(amounts: HandoffAmounts) -> str:
    if amounts.source == "order" and amounts.order is not None:
        a = amounts.order
        return (
            f"order payment {a.payment_status or 'none reported'}; authorized "
            f"{a.authorized_minor}, captured {a.captured_minor} {a.currency or ''}".rstrip()
        )
    if amounts.source == "cart" and amounts.cart is not None:
        c = amounts.cart
        items = "; ".join(f"{ln.quantity} x {ln.variant_id}" for ln in c.lines)
        total = "unknown" if c.subtotal_minor is None else f"{c.subtotal_minor} {c.currency or ''}"
        return f"{CART_LABEL}: {items}; subtotal (minor units) {total.rstrip()}"
    return "none (no order and an empty or unreadable cart)"


def missing_for_staff(case: HandoffCase) -> list[str]:
    """What staff would lack to act without the transcript; empty means actionable. Missing
    amounts or summary are fine when the case says so (`no_amounts`, `summary_missing`)."""
    missing = [
        name
        for name in ("reference", "intent", "root_cause", "recommended_action")
        if not getattr(case, name)
    ]
    if not case.identity.ref or case.identity.region not in REGIONS:
        missing.append("identity")
    if case.amounts.source == "none" and "no_amounts" not in case.flags:
        missing.append("amounts")
    if not case.summary.strip() or (
        case.summary_source == "code" and "summary_missing" not in case.flags
    ):
        missing.append("summary")
    return missing


def build_case(
    *,
    reference: str,
    request: HandoffRequest,
    screened: tuple[Turn, ...],
    correlation_id: str,
    order_id: str | None = None,
    order_case: Case | None = None,
    summary: str | None = None,
) -> HandoffCase:
    """The case from code-held data. With `order_case` (BE-07's), its root cause, action, amounts
    and summary are taken as they are; otherwise the no-order fields, with `summary` (the checked
    one from `run_summary`) or none."""
    flags: list[str] = []
    intent = intent_of(screened, has_order=order_case is not None)
    if order_case is not None:
        return HandoffCase(
            reference=reference,
            identity=request.identity,
            trigger=request.trigger,
            intent=intent,
            root_cause=order_case.root_cause,
            recommended_action=order_case.recommended_action,
            amounts=HandoffAmounts("order", order=order_case.amounts),
            summary=order_case.summary,
            summary_source=order_case.summary_source,
            flags=tuple(f for f in order_case.flags if f != "total_unavailable"),
            order_id=order_case.order_id,
            order_case_id=order_case.case_id,
            correlation_id=correlation_id,
        )
    if order_id is not None:
        flags.append("order_case_failed")  # the shopper's order, but BE-07's case was not queued
    cart = request.cart
    if cart is not None and cart.lines:
        amounts = HandoffAmounts("cart", cart=cart)
    else:
        amounts = HandoffAmounts("none")
        flags.append("no_amounts")
    if summary is None:
        flags.append("summary_missing")
    return HandoffCase(
        reference=reference,
        identity=request.identity,
        trigger=request.trigger,
        intent=intent,
        root_cause=SHOPPER_REQUESTED,
        recommended_action=HANDOFF_ACTIONS[intent],
        amounts=amounts,
        summary=summary or NO_SUMMARY,
        summary_source="model" if summary is not None else "code",
        flags=tuple(flags),
        order_id=order_id,
        order_case_id=None,
        correlation_id=correlation_id,
    )


# ---- the queue and the desk ---------------------------------------------------------------------


class _CareBusy(Exception):
    """Every Care slot is taken: the case is built by code alone."""


@runtime_checkable
class HandoffQueuePort(Protocol):
    """The staff handoff queue (a stub for `mcp-cases`, row #19). Both calls take an idempotency
    key and `dry_run` (hard rule 9)."""

    def open_handoff(
        self, *, idempotency_key: str, identity: Identity, dry_run: bool = False
    ) -> Result[str]:
        """Reserves the handoff's reference. The same key returns the same reference."""
        ...

    def file_case(
        self, *, idempotency_key: str, case: HandoffCase, dry_run: bool = False
    ) -> Result[HandoffCase]:
        """Queues the built case under its reference, once."""
        ...


@dataclass(frozen=True, slots=True)
class HandoffReceipt:
    reference: str
    dry_run: bool = False


class HandoffDesk:
    """Opens handoffs at once and builds their cases after. `settle()` waits for every build
    (tests); `close()` waits a bounded time, then cancels (shutdown). `runtime` gives each Care run
    its agent runtime (None: the default); the gateway passes one runtime for the whole desk, as
    chat does."""

    def __init__(
        self,
        *,
        queue: HandoffQueuePort,
        care: CarePorts,
        audit: AuditContext,
        runtime: Callable[[], AgentRuntime | None] = lambda: None,
        policy: RetryPolicy | None = None,
        max_open: int = MAX_OPEN_HANDOFFS,
        max_runs: int = MAX_CARE_RUNS,
    ) -> None:
        self._queue = queue
        self._care = care
        self._audit = audit.child(actor=ACTOR, correlation_id=f"{audit.correlation_id}.handoff")
        self._runtime = runtime
        self._policy = policy or RetryPolicy()
        self._max_open = max_open
        self._max_runs = max_runs
        self._active = 0  # Care runs now; one event loop, so a plain counter is enough
        self._opened: dict[str, str] = {}  # idempotency key -> reference
        self._tasks: set[asyncio.Task[HandoffCase | None]] = set()

    async def request(
        self, request: HandoffRequest, *, dry_run: bool = False
    ) -> Result[HandoffReceipt]:
        """Opens the handoff and returns its reference; the case is built after. A repeat for the
        same identity returns the first reference, builds nothing more and is audited (security
        review L2: the later request's transcript is not merged into the case)."""
        key = request.identity.key()
        known = self._opened.get(key)
        if known is not None:
            if not dry_run:
                self._note_repeat(known)
            return Ok(HandoffReceipt(known, dry_run))
        if len(self._opened) >= self._max_open:
            return DESK_FULL
        opened = self._queue.open_handoff(
            idempotency_key=key, identity=request.identity, dry_run=dry_run
        )
        if not isinstance(opened, Ok):
            return opened
        reference = opened.value
        if not dry_run:
            self._opened[key] = reference
            task = asyncio.create_task(self._build(reference, request))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return Ok(HandoffReceipt(reference, dry_run))

    def _note_repeat(self, reference: str) -> None:
        try:
            self._audit.record("handoff.repeat", {"reference": reference}, {"ok": True})
        except AuditWriteError:
            log.error("handoff audit write failed")

    async def settle(self) -> list[HandoffCase | None]:
        return list(await asyncio.gather(*tuple(self._tasks)))

    async def close(self, timeout: float = 5.0) -> None:
        """Waits up to `timeout` seconds for open builds, then cancels the rest (shutdown)."""
        pending = tuple(self._tasks)
        if not pending:
            return
        _done, late = await asyncio.wait(pending, timeout=timeout)
        for task in late:
            task.cancel()
        if late:
            log.error("handoff builds cancelled at shutdown: %d", len(late))
            await asyncio.gather(*late, return_exceptions=True)

    async def _owned_order(self, request: HandoffRequest, run: AuditContext) -> str | None:
        """The first order ID in the shopper's turns, if the store shows it is the signed-in
        shopper's own. Anonymous callers, and anyone else's order, get none (hard rule 7)."""
        if request.identity.kind != "shopper":
            return None
        text = " ".join(
            unicodedata.normalize("NFKC", t.text) for t in request.turns if t.role == "shopper"
        )
        found = _ORDER_ID.search(text)
        if found is None:
            return None
        orders = AuditedOrderStore(self._care.orders, run, allowed=CARE_READ)
        done = await call_with_recovery(lambda: orders.get(found.group()), self._policy)
        if not isinstance(done.result, Ok) or done.result.value.shopper_id != request.identity.ref:
            return None
        return found.group()

    async def _build(self, reference: str, request: HandoffRequest) -> HandoffCase | None:
        correlation_id = f"handoff-{reference}"
        run = self._audit.child(actor=ACTOR, correlation_id=correlation_id)
        order_id: str | None = None
        screened: tuple[Turn, ...] = ()
        order_case: Case | None = None
        summary: str | None = None
        busy = self._active >= self._max_runs
        if not busy:
            self._active += 1
        try:
            if busy:
                # Security review M1: no Care run over the cap; the code-built case goes now.
                raise _CareBusy
            order_id = await self._owned_order(request, run)
            task = CareTask(order_id, request.turns)
            screened = task.transcript
            if order_id is not None:
                care = await run_care(
                    task,
                    ports=self._care,
                    audit=run,
                    correlation_id=f"{correlation_id}.care",
                    runtime=self._runtime(),
                    policy=self._policy,
                )
                order_case = care.case
            else:
                result = await run_summary(
                    task,
                    audit=run,
                    correlation_id=f"{correlation_id}.care",
                    runtime=self._runtime(),
                    policy=self._policy,
                )
                summary = result.summary
        except _CareBusy:
            log.error("handoff care run skipped: busy")
        except Exception as error:  # noqa: BLE001 - staff still get the code-built case
            log.error("handoff care run failed: %s", type(error).__name__)
        finally:
            if not busy:
                self._active -= 1
        case = build_case(
            reference=reference,
            request=request,
            screened=screened,
            correlation_id=correlation_id,
            order_id=order_id,
            order_case=order_case,
            summary=summary,
        )
        if busy:
            case = replace(case, flags=(*case.flags, "care_busy"))
        filed = self._queue.file_case(idempotency_key=f"{request.identity.key()}:case", case=case)
        outcome: dict[str, Any] = {
            "ok": isinstance(filed, Ok),
            "reference": reference,
            "intent": case.intent,
            "root_cause": case.root_cause,
            "flags": list(case.flags),
        }
        try:
            run.record("handoff.case", {"reference": reference}, outcome)
        except AuditWriteError:
            log.error("handoff audit write failed")
        if not isinstance(filed, Ok):
            log.error("handoff case not queued: %s", filed.code)
            return None
        return filed.value


__all__ = [
    "ACTOR",
    "CART_LABEL",
    "HANDOFF_ACTIONS",
    "INTENTS",
    "MAX_HANDOFF_TURNS",
    "SHOPPER_REQUESTED",
    "DESK_FULL",
    "MAX_CARE_RUNS",
    "MAX_OPEN_HANDOFFS",
    "TRANSCRIPT_LABEL",
    "CartLine",
    "CartSnapshot",
    "HandoffAmounts",
    "HandoffCase",
    "HandoffDesk",
    "HandoffQueuePort",
    "HandoffReceipt",
    "HandoffRequest",
    "Identity",
    "Intent",
    "build_case",
    "intent_of",
    "missing_for_staff",
]
