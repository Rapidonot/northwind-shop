"""FastAPI app for the portal gateway.

FOUND-0: a health endpoint that proves the portal -> gateway -> Postgres path works.
SA-02: the chat endpoint, in `northwind_gateway.chat`.
Row #16 (CHK-01): the shopper's session (`northwind_gateway.session`) and GET /api/cart
(`northwind_gateway.cart`).
Row #23 (SA-06): POST /api/handoff (`northwind_gateway.handoff`).
SA-01 PR 1: the shop pages' reads (`northwind_gateway.shop`).
SA-01 PR 2: the bag's cart writes (`cart_writes`), the order history (`orders`) and the demo
checkout, CHK-02's portal confirmation control (`checkout`, decision 0027).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import psycopg
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from northwind_assistant.usage import TELEMETRY_LOGGER
from northwind_common.localenv import announce_database
from northwind_gateway import access_log, checkout
from northwind_gateway.cart import router as cart_router
from northwind_gateway.cart_writes import router as cart_writes_router
from northwind_gateway.chat import (
    CardNumberRefused,
    card_refused,
    invalid_request,
    router,
)
from northwind_gateway.demo_orders import demo_orders
from northwind_gateway.handoff import handoff_desk
from northwind_gateway.handoff import router as handoff_router
from northwind_gateway.orders import router as orders_router
from northwind_gateway.session import SessionMiddleware
from northwind_gateway.settings import database_url
from northwind_gateway.shop import router as shop_router
from northwind_gateway.signin import router as signin_router
from northwind_gateway.staff import views as staff_views
from northwind_gateway.staff.routes import StaffGate
from northwind_gateway.staff.routes import router as staff_router

# SA-01 PR 1 (security review F1): no query string in uvicorn's access log, so card data typed
# into the shop's search never reaches it (hard rule 1). Again at startup, below.
access_log.install()

# OpenTelemetry comes later: for now the per-answer line goes to stderr (RAG-13).
_telemetry = logging.getLogger(TELEMETRY_LOGGER)
_telemetry.setLevel(logging.INFO)
if not _telemetry.handlers:
    _telemetry.addHandler(logging.StreamHandler())


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Row #16: the database the health check reads, by name only (the scratch-database proof).
    announce_database("gateway", database_url())
    access_log.install()
    yield
    # Row #23 (SA-06, security review M1): open handoff builds get a bounded wait, then are
    # cancelled, so no Care run outlives the process unseen.
    if handoff_desk.cache_info().currsize:
        await handoff_desk().close()


app = FastAPI(title="Northwind gateway", lifespan=lifespan)
app.include_router(router)
app.include_router(cart_router)
app.include_router(signin_router)  # row #27: the demo sign-in, behind NORTHWIND_DEMO_SIGNIN
app.include_router(handoff_router)  # row #23 (SA-06): the shopper asks for a person
app.include_router(shop_router)  # SA-01 PR 1: the shop pages' reads
app.include_router(cart_writes_router)  # SA-01 PR 2: the bag's controls and ADD TO BAG
app.include_router(orders_router)  # SA-01 PR 2: the order history, signed in only
app.include_router(checkout.router)  # SA-01 PR 2: the demo checkout, Pay (demo) (CHK-02)
app.include_router(staff_router)  # OPS-1: the demo staff console, only with DEMO_STAFF_DASHBOARD=1


def _checkout_stores() -> staff_views.CheckoutStores:
    """OPS-1: the console reads the checkout's stores, never the desk (SA-01's pin)."""
    desk = checkout.checkout_desk()
    return staff_views.CheckoutStores(orders=desk.orders, inventory=desk.inventory, audit=desk.sink)


staff_views.use_checkout(_checkout_stores)
# Built once here, on import, before any request (security review L2): the console's reads run in
# the threadpool, and `functools.cache` would let two threads each build a desk, losing Pay's
# idempotency record on the first.
checkout.checkout_desk()
# The same for the Pay (demo) order history, which the console's reads also reach from the
# threadpool (advisor-reviewer finding 5).
demo_orders()
app.add_middleware(SessionMiddleware)
# OPS-1 (security review L1): outermost, so a staff path is decided before any routing.
app.add_middleware(StaffGate)
app.add_exception_handler(RequestValidationError, invalid_request)
app.add_exception_handler(CardNumberRefused, card_refused)


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    db: Literal["ok", "unavailable"]
    pgvector: str | None = None


@app.get("/api/healthz")
def healthz() -> Health:
    """Report gateway and database health. Always 200, so the portal can show the state."""
    try:
        with psycopg.connect(database_url(), connect_timeout=2) as conn:
            row = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
    except psycopg.Error:
        # Not only OperationalError: a malformed URL raises ProgrammingError, and libpq's message
        # quotes the URL, password included. Letting it escape would log it (NFR-02).
        return Health(db="unavailable")
    return Health(db="ok", pgvector=row[0] if row else None)
