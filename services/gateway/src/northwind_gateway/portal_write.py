"""The guard on every portal write (SA-01 PR 2: the bag's controls, ADD TO BAG and Pay (demo)).

A write must come from the portal's own page, never from another site or a link:
- `Content-Type: application/json`: a cross-site page can send a form or text body without a
  preflight, never a JSON one (as POST /api/handoff, security review M1);
- the fixed header `X-Northwind-Portal: 1`: a custom header also needs a preflight, which the
  gateway never grants (it has no CORS policy);
- `Sec-Fetch-Site`, when the browser sends it, must be `same-origin`;
- `Origin`, when sent without a same-origin `Sec-Fetch-Site`, must be the gateway's own.
The session cookie is `SameSite=Strict` too. A refusal is one fixed `permission` envelope.

Keys: the portal sends one random key per click (letters only, so it can never look like a card
number). The cart tools remember a key per (turn, key); the gateway's cart token carries one turn
per session (`portal_turn`), so a retried click is applied once and a new click is a new change.
"""

from __future__ import annotations

import hashlib
import re
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

PORTAL_HEADER = "x-northwind-portal"
REFUSED = "That request could not be accepted."
NO_STORE = {"Cache-Control": "no-store"}
# The portal's keys: 16-64 letters, digits, '-' or '_' (the cart tools' own rule is 8-64).
KEY = re.compile(r"[A-Za-z0-9_-]{16,64}", re.ASCII)
_TURN_LABEL = b"northwind-portal-cart-v1:"


def from_portal(request: Request) -> bool:
    kind = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if kind != "application/json" or request.headers.get(PORTAL_HEADER) != "1":
        return False
    site = request.headers.get("sec-fetch-site")
    if site is not None and site != "same-origin":
        return False
    origin = request.headers.get("origin")
    if origin is not None and site != "same-origin":
        host = request.headers.get("host", "")
        return origin in {f"http://{host}", f"https://{host}"}
    return True


def refused() -> JSONResponse:
    body = {
        "isError": True,
        "errorCategory": "permission",
        "isRetryable": False,
        "message": REFUSED,
    }
    return JSONResponse(status_code=403, content=body, headers=NO_STORE)


def envelope(
    status: int, category: str, retryable: bool, message: str, **extra: object
) -> JSONResponse:
    body: dict[str, object] = {
        "isError": True,
        "errorCategory": category,
        "isRetryable": retryable,
        "message": message,
        **extra,
    }
    return JSONResponse(status_code=status, content=body, headers=NO_STORE)


def portal_turn(session: str) -> str:
    """One stable turn per session for the portal's cart writes: a UUID4 shape (as `mint` needs),
    derived from the session, so the cart's per-(turn, key) memory spans the whole session."""
    digest = hashlib.sha256(_TURN_LABEL + session.encode("ascii")).digest()
    return str(uuid.UUID(bytes=digest[:16], version=4))
