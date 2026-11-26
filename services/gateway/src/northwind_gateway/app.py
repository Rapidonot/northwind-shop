"""FastAPI app for the portal gateway.

FOUND-0: a health endpoint that proves the portal -> gateway -> Postgres path works.
SA-02: the chat endpoint, in `northwind_gateway.chat`.
Row #16 (CHK-01): the shopper's session (`northwind_gateway.session`) and GET /api/cart
(`northwind_gateway.cart`).
Row #23 (SA-06): POST /api/handoff (`northwind_gateway.handoff`).
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
from northwind_gateway.cart import router as cart_router
from northwind_gateway.chat import (
    CardNumberRefused,
    card_refused,
    invalid_request,
    router,
)
from northwind_gateway.handoff import router as handoff_router
from northwind_gateway.session import SessionMiddleware
from northwind_gateway.settings import database_url
from northwind_gateway.signin import router as signin_router

# OpenTelemetry comes later: for now the per-answer line goes to stderr (RAG-13).
_telemetry = logging.getLogger(TELEMETRY_LOGGER)
_telemetry.setLevel(logging.INFO)
if not _telemetry.handlers:
    _telemetry.addHandler(logging.StreamHandler())


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Row #16: the database the health check reads, by name only (the scratch-database proof).
    announce_database("gateway", database_url())
    yield


app = FastAPI(title="Northwind gateway", lifespan=lifespan)
app.include_router(router)
app.include_router(cart_router)
app.include_router(signin_router)  # row #27: the demo sign-in, behind NORTHWIND_DEMO_SIGNIN
app.include_router(handoff_router)  # row #23 (SA-06): the shopper asks for a person
app.add_middleware(SessionMiddleware)
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
