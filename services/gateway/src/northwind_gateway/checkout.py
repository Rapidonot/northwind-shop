"""The demo checkout: CHK-02's portal confirmation control (SA-01 PR 2; decision 0027; hard rules 1,
2, 9 and 10).

- `GET /api/checkout/summary`: the bag read live (`get_cart`, hard rule 2) as an order summary:
  the lines, the total, delivery, the region's returns policy, any issue, and a digest of exactly
  what is shown. It issues no confirmation.
- `POST /api/checkout/pay` {digest, idempotency_key}: the shopper pressed **Pay (demo)**, a portal
  control, never chat text. Signed in only (the user's decision, 2026-11-27). The route reads the
  bag again, refuses a changed digest, an issue, an amount over the order limit or stock that is
  already short (before any confirmation), and only then issues the confirmation reference, bound to
  the new order, its currency and exact total. This is the only call site of the confirmation
  book's `issue` in the repo (the pin in `test_be05_prerequisites.py`).
- Then BE-04's inventory store and `orders_place`'s deterministic step functions run in code, in
  a fixed order, exactly as the system prompt orders them: validate (and the address on file once),
  fraud, authorize (once more after a soft decline), capture, confirm; on any failure the order is
  escalated, which voids nothing it did not take and releases the stock hold. `finish` escalates an
  order that ends neither placed nor escalated. No model, ever: `run_order_placement` and
  `run_subagent` are never referenced here (pinned), and the checkout's tests make every LLM entry
  point raise and count zero requests at the replay proxy.
- Placed: the bag is emptied (the cart writes, keys from the order ID) and the order joins the
  shopper's history (`demo_orders`). Escalated: it joins as "on hold"; stock lost to another shopper
  shows "no longer available" with BE-04's substitutes.

Card data never reaches this route's state: no card field exists, only the sandbox token, and the
fakes refuse anything that looks like a PAN. Idempotency: one result per (shopper, key); the same
key with another digest is refused, and every pay of one shopper runs under that shopper's lock, so
two keys racing on one bag make one order. Fixed-text envelopes for every refusal.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CartReader
from northwind_backend.agents.orders_place import (
    OrderPlacementTask,
    PlacementResult,
    Ports,
    build_handlers,
    finish,
)
from northwind_backend.audit import AuditContext, InMemoryAuditSink
from northwind_backend.errors import AgentResult, Ok, Result, RunStatus
from northwind_backend.fakes.catalog import NOT_SOLD
from northwind_backend.fakes.fraud import FraudFake, FraudState
from northwind_backend.fakes.orders import DATA, OrderState, OrderStoreFake
from northwind_backend.fakes.payment import (
    DEFAULT_ORDER_LIMITS,
    SANDBOX_APPROVED,
    PaymentFake,
    PaymentState,
)
from northwind_backend.inventory import InventoryFake, InventoryState, Line
from northwind_backend.inventory.model import StockOut
from northwind_backend.ports import Money, Order, OrderLine, PriceQuote, StockLevel
from northwind_common.cards import contains_card_data
from northwind_gateway.cart import cart_reader
from northwind_gateway.cart_writes import CartWriter, Refusal, cart_writer
from northwind_gateway.chat import CardNumberRefused
from northwind_gateway.demo_orders import demo_orders
from northwind_gateway.portal_write import KEY, NO_STORE, envelope, from_portal, refused
from northwind_gateway.settings import shopper_caller
from northwind_gateway.shop import PRODUCT_ID, VARIANT_ID

log = logging.getLogger(__name__)

SUMMARY_PATH = "/api/checkout/summary"
PAY_PATH = "/api/checkout/pay"
CHECKOUT_PATHS = (SUMMARY_PATH, PAY_PATH)

ACTOR = "portal-checkout"
FIRST_ORDER_NUMBER = 900_001  # demo orders never share a number with the seeded ones
DIGEST = re.compile(r"[0-9a-f]{32}", re.ASCII)
AMOUNT = re.compile(r"\d{1,7}\.\d{2}", re.ASCII)
DELIVERY = "Standard delivery. This demo charges nothing for it and gives no date."

SIGN_IN = "Sign in to pay (demo)."
UNAVAILABLE = "Checkout isn't available right now. Please try again."
INVALID = "That request could not be accepted."
EMPTY = "Your bag is empty."
CHANGED = "Your bag changed. Check the summary again before paying."
NOT_READY = "Something in your bag can't be ordered. Check the summary."
OVER_LIMIT = "This order is above the demo's order limit."
REUSED = "That payment request was already used for a different order summary."
NO_LONGER = "An item in your bag is no longer available."
ALREADY_PLACED = "This bag was already paid for. Check your orders."
DECLINED = "The demo payment was declined. Nothing was charged."
ON_HOLD = "Your order is on hold: a person will check it. Nothing was charged."

router = APIRouter()


class Unreadable(Exception):
    """A cart that is not what `get_cart` returns; holds no text on purpose."""


# ---- the summary ------------------------------------------------------------------------------


def _minor(amount: str) -> int:
    if not AMOUNT.fullmatch(amount):
        raise Unreadable
    try:
        return int(Decimal(amount) * 100)
    except InvalidOperation:
        raise Unreadable from None


@dataclass(frozen=True)
class SummaryLine:
    variant_id: str
    product_id: str
    name: str
    colour: str
    size: str
    quantity: int
    unit_price: str | None
    line_total: str | None
    on_hand: int
    in_stock: bool


@dataclass(frozen=True)
class Summary:
    region: str
    currency: str | None
    lines: tuple[SummaryLine, ...]
    total: str | None
    issues: tuple[str, ...]

    @property
    def total_minor(self) -> int:
        return 0 if self.total is None else _minor(self.total)

    @property
    def digest(self) -> str:
        """Of exactly what the shopper is shown and confirms: lines, unit prices, the total."""
        shown = {
            "region": self.region,
            "currency": self.currency,
            "total": self.total,
            "lines": [[ln.variant_id, ln.quantity, ln.unit_price] for ln in self.lines],
        }
        text = json.dumps(shown, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    def view(self, signed_in: bool) -> dict[str, Any]:
        return {
            "region": self.region,
            "lines": [
                {
                    "variant_id": ln.variant_id,
                    "product_id": ln.product_id,
                    "name": ln.name,
                    "colour": ln.colour,
                    "size": ln.size,
                    "quantity": ln.quantity,
                    "unit_price": None
                    if ln.unit_price is None
                    else _money(ln.unit_price, self.currency),
                    "line_total": None
                    if ln.line_total is None
                    else _money(ln.line_total, self.currency),
                }
                for ln in self.lines
            ],
            "total": None if self.total is None else _money(self.total, self.currency),
            "delivery": DELIVERY,
            "return_policy": f"POL-RETURNS-{self.region}",
            "issues": list(self.issues),
            "digest": self.digest,
            "signed_in": signed_in,
        }


def _money(amount: str, currency: str | None) -> dict[str, str]:
    return {"amount": amount, "currency": currency or ""}


def summarize(cart: Any) -> Summary:
    """The live cart as a summary; every field type-checked, anything else fails closed."""
    if not isinstance(cart, dict) or not isinstance(cart.get("lines"), list):
        raise Unreadable
    region, currency, subtotal = cart.get("region"), cart.get("currency"), cart.get("subtotal")
    if not isinstance(region, str) or not re.fullmatch(r"[A-Z]{2}", region):
        raise Unreadable
    lines = []
    for raw in cart["lines"]:
        if not isinstance(raw, dict):
            raise Unreadable
        variant, product = raw.get("variant_id"), raw.get("product_id")
        quantity, on_hand = raw.get("quantity"), raw.get("on_hand")
        if not isinstance(variant, str) or not VARIANT_ID.fullmatch(variant):
            raise Unreadable
        if not isinstance(product, str) or not PRODUCT_ID.fullmatch(product):
            raise Unreadable
        for number in (quantity, on_hand):
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                raise Unreadable
        unit, line_total = raw.get("unit_price"), raw.get("line_total")
        for amount in (unit, line_total):
            if amount is not None and (not isinstance(amount, str) or not AMOUNT.fullmatch(amount)):
                raise Unreadable
        lines.append(
            SummaryLine(
                variant_id=variant,
                product_id=product,
                name=str(raw.get("name", "")),
                colour=str(raw.get("colour", "")),
                size=str(raw.get("size", "")),
                quantity=int(quantity),  # type: ignore[arg-type]
                unit_price=unit,
                line_total=line_total,
                on_hand=int(on_hand),  # type: ignore[arg-type]
                in_stock=raw.get("in_stock") is True,
            )
        )
    if subtotal is not None and (not isinstance(subtotal, str) or not AMOUNT.fullmatch(subtotal)):
        raise Unreadable
    if currency is not None and (
        not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)
    ):
        raise Unreadable
    issues = set()
    if not lines:
        issues.add("empty")
    if any(ln.unit_price is None for ln in lines) or (
        lines and (subtotal is None or currency is None)
    ):
        issues.add("not_sold_here")
    if any(not ln.in_stock or ln.on_hand < ln.quantity for ln in lines):
        issues.add("out_of_stock")
    if currency is not None and subtotal is not None:
        limit = DEFAULT_ORDER_LIMITS.get(currency)
        if limit is None or _minor(subtotal) > limit:
            issues.add("over_order_limit")
    return Summary(region, currency, tuple(lines), subtotal, tuple(sorted(issues)))


# ---- the fakes behind Pay (demo) -------------------------------------------------------------


@dataclass(frozen=True)
class LiveCartCatalog:
    """The catalog port for one pay: each line's price and stock as `get_cart` just read them from
    the live store (hard rule 2), never the fakes' own table. Any other variant is not sold."""

    lines: tuple[SummaryLine, ...]
    region: str
    currency: str

    def price(self, variant_id: str, region: str) -> Result[PriceQuote]:
        line = next((ln for ln in self.lines if ln.variant_id == variant_id), None)
        if line is None or region != self.region or line.unit_price is None:
            return NOT_SOLD
        return Ok(PriceQuote(variant_id, region, _minor(line.unit_price), self.currency))

    def stock(self, variant_id: str, region: str) -> Result[StockLevel]:
        line = next((ln for ln in self.lines if ln.variant_id == variant_id), None)
        if line is None or region != self.region:
            return NOT_SOLD
        return Ok(StockLevel(variant_id, region, line.on_hand))


def _now() -> datetime:
    return datetime.now(UTC)


def _shoppers() -> list[dict[str, Any]]:
    with (DATA / "shoppers.jsonl").open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


@dataclass
class CheckoutDesk:
    """The sandbox provider's side for the portal: one set of shared fake states for the process,
    and the demo orders' numbers. Holds tokens and intents only, never card data."""

    payment: PaymentState = field(default_factory=PaymentState)
    fraud: FraudState = field(default_factory=FraudState)
    orders: OrderState = field(default_factory=lambda: OrderState((), _shoppers()))
    inventory: InventoryState = field(default_factory=InventoryState.from_fixtures)
    sink: InMemoryAuditSink = field(default_factory=InMemoryAuditSink)
    paid: dict[tuple[str, str], tuple[str, int, dict[str, Any]]] = field(default_factory=dict)
    # test-writer finding (SA-01 PR 2): a placed order whose bag could not be emptied, by shopper
    # and digest. The same bag can't be paid again with a new key until it is emptied (or changes).
    unemptied: dict[tuple[str, str], Order] = field(default_factory=dict)
    _next: int = FIRST_ORDER_NUMBER
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _guard: threading.Lock = field(default_factory=threading.Lock)

    def lock_for(self, shopper: str) -> asyncio.Lock:
        with self._guard:
            return self._locks.setdefault(shopper, asyncio.Lock())

    def new_order(self, shopper: str, summary: Summary) -> Order | None:
        on_file = self.orders.on_file.get(shopper)
        if on_file is None or on_file[0] != summary.region:
            return None
        with self._guard:
            number, self._next = self._next, self._next + 1
        order = Order(
            order_id=f"O{number:06d}",
            shopper_id=shopper,
            region=summary.region,
            lines=tuple(
                OrderLine(ln.product_id, ln.variant_id, ln.quantity) for ln in summary.lines
            ),
            payment_token=SANDBOX_APPROVED,
            shipping_postal_code=on_file[1],
        )
        self.orders.orders[order.order_id] = order
        return order

    def ports(self, summary: Summary) -> Ports:
        return Ports(
            payment=PaymentFake(state=self.payment),
            fraud=FraudFake(state=self.fraud),
            catalog=LiveCartCatalog(summary.lines, summary.region, summary.currency or ""),
            orders=OrderStoreFake(state=self.orders),
            inventory=InventoryFake(_now, state=self.inventory),
        )

    def shortfall(self, order: Order) -> dict[str, Any] | None:
        """BE-04's answer, without holding anything (`dry_run`): what is short and its substitutes,
        or None when every line can be held now."""
        found = InventoryFake(_now, state=self.inventory).reserve(
            idempotency_key=f"{order.order_id}:inventory.check",
            order_id=order.order_id,
            region=order.region,
            postal_code=order.shipping_postal_code,
            lines=[Line(ln.variant_id, ln.quantity) for ln in order.lines],
            dry_run=True,
        )
        if isinstance(found, StockOut):
            body = found.to_envelope()
            return {"short": body["short"], "substitutes": body["substitutes"]}
        return None


@cache
def checkout_desk() -> CheckoutDesk:
    return CheckoutDesk()


def _body(payload: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(payload["content"][0]["text"])
    return body


async def place(desk: CheckoutDesk, order: Order, ref: str, summary: Summary) -> PlacementResult:
    """The system prompt's steps as fixed code over `orders_place`'s handlers: no model."""
    cid = f"checkout-{order.order_id}"
    task = OrderPlacementTask(order.order_id, ref)
    audit = AuditContext(desk.sink, correlation_id=cid, actor=ACTOR)
    h = build_handlers(task, desk.ports(summary), audit, correlation_id=cid)
    arg = {"order_id": order.order_id}

    async def escalated() -> PlacementResult:
        await h.escalate_order(arg)
        return await finish(h, AgentResult(RunStatus.COMPLETE, (), (), cid))

    checked = _body(await h.validate_order(arg))
    if not checked["isError"] and checked["value"]["issues"] == ["address_undeliverable"]:
        await h.use_address_on_file(arg)
        checked = _body(await h.validate_order(arg))
    if checked["isError"] or checked["value"]["issues"]:
        return await escalated()
    scored = _body(await h.assess_fraud(arg))
    if scored["isError"] or scored["value"]["decision"] != "allow":
        return await escalated()
    paid = _body(await h.authorize_payment(arg))
    if paid["isError"] and paid.get("code") == "soft_decline":
        paid = _body(await h.authorize_payment(arg))
    if paid["isError"]:
        return await escalated()
    for step in (h.capture_payment, h.confirm_order):
        if _body(await step(arg))["isError"]:
            return await escalated()
    return await finish(h, AgentResult(RunStatus.COMPLETE, (), (), cid))


async def _empty_bag(writer: CartWriter | None, caller: Caller, order: Order) -> bool:
    """The bag after a placed order: each line removed, keyed by the order (a retry is a no-op)."""
    if writer is None:
        return False
    try:
        for n, line in enumerate(order.lines):
            key = f"{order.order_id}-paid-{n:02d}-remove"
            done = await writer.write(
                caller, "remove_from_cart", {"variant_id": line.variant_id, "idempotency_key": key}
            )
            if isinstance(done, Refusal):
                return False
    except Exception as error:  # noqa: BLE001
        log.error("checkout bag clear failed: %s", type(error).__name__)
        return False
    return True


# ---- routes ------------------------------------------------------------------------------------

Shopper = Annotated[Caller, Depends(shopper_caller)]
Reader = Annotated[CartReader | None, Depends(cart_reader)]
Writer = Annotated[CartWriter | None, Depends(cart_writer)]


async def _summary(caller: Caller, reader: CartReader | None) -> Summary | None:
    if reader is None or caller.session is None:
        return None
    try:
        return summarize(await reader.read(caller))
    except Exception as error:  # noqa: BLE001 - its text must not reach the shopper or a log
        log.error("checkout read failed: %s", type(error).__name__)
        return None


@router.get(SUMMARY_PATH)
async def summary(caller: Shopper, reader: Reader) -> JSONResponse:
    shown = await _summary(caller, reader)
    if shown is None:
        return envelope(503, "transient", True, UNAVAILABLE)
    return JSONResponse(content=shown.view(caller.shopper_id is not None), headers=NO_STORE)


class PayBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    digest: str = Field(max_length=32)
    idempotency_key: str = Field(max_length=64)


@router.post(PAY_PATH)
async def pay(
    request: Request, body: PayBody, caller: Shopper, reader: Reader, writer: Writer
) -> JSONResponse:
    if not from_portal(request):
        return refused()
    texts = [body.digest, body.idempotency_key]
    if any(contains_card_data(t) for t in texts) or contains_card_data(" ".join(texts)):
        raise CardNumberRefused
    if not DIGEST.fullmatch(body.digest) or not KEY.fullmatch(body.idempotency_key):
        return envelope(422, "validation", False, INVALID)
    shopper = caller.shopper_id
    if shopper is None:
        return envelope(401, "permission", False, SIGN_IN)
    desk = checkout_desk()
    async with desk.lock_for(shopper):
        seen = desk.paid.get((shopper, body.idempotency_key))
        if seen is not None:
            if seen[0] != body.digest:
                return envelope(409, "validation", False, REUSED, code="idempotency_conflict")
            return JSONResponse(status_code=seen[1], content=seen[2], headers=NO_STORE)
        shown = await _summary(caller, reader)
        if shown is None:
            return envelope(503, "transient", True, UNAVAILABLE)
        earlier = desk.unemptied.get((shopper, shown.digest))
        if earlier is not None and shown.digest == body.digest:
            # This exact bag already became an order and was not emptied: never a second order and
            # a second charge for it. Empty it now; once that works, the same items can be bought
            # again later.
            if await _empty_bag(writer, caller, earlier):
                desk.unemptied.pop((shopper, shown.digest), None)
            return envelope(
                409,
                "business",
                False,
                ALREADY_PLACED,
                code="already_placed",
                order_id=earlier.order_id,
            )
        if "empty" in shown.issues:
            return envelope(409, "business", False, EMPTY, code="empty")
        if shown.digest != body.digest:
            return envelope(409, "business", False, CHANGED, code="summary_changed")
        if "over_order_limit" in shown.issues:
            return envelope(409, "business", False, OVER_LIMIT, code="over_order_limit")
        if shown.issues or shown.currency is None or shown.total is None:
            return envelope(409, "business", False, NOT_READY, code="not_ready")
        order = desk.new_order(shopper, shown)
        if order is None:
            return envelope(503, "transient", True, UNAVAILABLE)
        short = desk.shortfall(order)
        if short is not None:
            desk.orders.orders.pop(order.order_id, None)  # nothing was confirmed or held
            return envelope(409, "business", False, NO_LONGER, code="no_longer_available", **short)
        # The confirmation: bound to this order, its currency and its exact total. The one call
        # site of the book's `issue` in the repo (CHK-02's portal control; decision 0027).
        ref = desk.payment.confirmations.issue(
            order.order_id, Money(shown.total_minor, shown.currency)
        )
        result = await place(desk, order, ref, shown)
        status, content = await _outcome(desk, caller, writer, order, shown, result)
        desk.paid[(shopper, body.idempotency_key)] = (body.digest, status, content)
        return JSONResponse(status_code=status, content=content, headers=NO_STORE)


async def _outcome(
    desk: CheckoutDesk,
    caller: Caller,
    writer: CartWriter | None,
    order: Order,
    shown: Summary,
    result: PlacementResult,
) -> tuple[int, dict[str, Any]]:
    total = _money(shown.total or "0.00", shown.currency)
    placed = result.outcome == "placed"
    demo_orders().record(
        shopper=order.shopper_id,
        order_id=order.order_id,
        placed_on=_now().date().isoformat(),
        status="confirmed" if placed else "on_hold",
        lines=[
            {"product_id": ln.product_id, "variant_id": ln.variant_id, "quantity": ln.quantity}
            for ln in order.lines
        ],
        total=total,
    )
    if placed:
        emptied = await _empty_bag(writer, caller, order)
        if not emptied:
            desk.unemptied[(order.shopper_id, shown.digest)] = order
        return 200, {
            "result": "placed",
            "order_id": order.order_id,
            "total": total,
            "bag_emptied": emptied,
        }
    reason = result.reason
    if reason == "out_of_stock":
        short = desk.shortfall(order) or {"short": [], "substitutes": {}}
        body = {
            "isError": True,
            "errorCategory": "business",
            "isRetryable": False,
            "message": NO_LONGER,
        }
        return 409, {**body, "code": "no_longer_available", "order_id": order.order_id, **short}
    if reason == "payment_declined":
        body = {
            "isError": True,
            "errorCategory": "business",
            "isRetryable": False,
            "message": DECLINED,
        }
        return 409, {**body, "code": "payment_declined", "order_id": order.order_id}
    body = {"isError": True, "errorCategory": "business", "isRetryable": False, "message": ON_HOLD}
    return 409, {**body, "code": "on_hold", "order_id": order.order_id}
