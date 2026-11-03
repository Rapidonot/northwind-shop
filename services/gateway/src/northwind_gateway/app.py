"""FastAPI app for the portal gateway.

FOUND-0: only a health endpoint that proves the portal -> gateway -> Postgres path works.
"""

import os
from typing import Literal

import psycopg
from fastapi import FastAPI
from pydantic import BaseModel

DEFAULT_DATABASE_URL = "postgresql://northwind:northwind-local@localhost:5432/northwind"

app = FastAPI(title="Northwind gateway")


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    db: Literal["ok", "unavailable"]
    pgvector: str | None = None


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


@app.get("/api/healthz")
def healthz() -> Health:
    """Report gateway and database health. Always 200, so the portal can show the state."""
    try:
        with psycopg.connect(database_url(), connect_timeout=2) as conn:
            row = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
    except psycopg.OperationalError:
        return Health(db="unavailable")
    return Health(db="ok", pgvector=row[0] if row else None)
