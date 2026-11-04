"""FastAPI app for the portal gateway.

FOUND-0: a health endpoint that proves the portal -> gateway -> Postgres path works.
SA-02: the chat endpoint, in `northwind_gateway.chat`.
"""

import logging
from typing import Literal

import psycopg
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from northwind_assistant.usage import TELEMETRY_LOGGER
from northwind_gateway.chat import (
    CardNumberRefused,
    card_refused,
    invalid_request,
    router,
)
from northwind_gateway.settings import database_url

# OpenTelemetry comes later: for now the per-answer line goes to stderr (RAG-13).
_telemetry = logging.getLogger(TELEMETRY_LOGGER)
_telemetry.setLevel(logging.INFO)
if not _telemetry.handlers:
    _telemetry.addHandler(logging.StreamHandler())

app = FastAPI(title="Northwind gateway")
app.include_router(router)
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
