"""The demo sign-in (row #27, CHK-06; decision 0023): `POST /api/session/shopper`.

With `NORTHWIND_DEMO_SIGNIN=1` it binds the one fixed synthetic shopper, `DEMO_SHOPPER`, to the
caller's session (`session.sign_shopper`), so the chat's next turns get the order tool for that
shopper's own orders. It takes no body: no request value names the shopper. With the flag off it is
the same 404 as a path that does not exist. There is no picker; a portal control is SA-01's.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from northwind_common.mcp_token import TokenKeyError, signing_key
from northwind_gateway.session import (
    SIGNIN_PATH,
    STATE_KEY,
    demo_signin_enabled,
    session_key,
    shopper_cookie_header,
)

router = APIRouter()

# The one demo shopper (the user's D1) and the region on file for them in
# data/backend/shoppers.jsonl (a test checks it). Their ten orders: O000005, O000064, O000113,
# O000257, O000331, O000392, O000587, O000638, O000784 and O000874.
DEMO_SHOPPER = "S0031"
DEMO_SHOPPERS = {DEMO_SHOPPER: "US"}

UNAVAILABLE = {
    "isError": True,
    "errorCategory": "transient",
    "isRetryable": True,
    "message": "Sign-in is unavailable right now.",
}


@router.post(SIGNIN_PATH)
def sign_in(request: Request) -> JSONResponse:
    if not demo_signin_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    session = getattr(request.state, STATE_KEY, None)
    try:
        key = session_key(signing_key())
    except TokenKeyError:
        key = None
    if not isinstance(session, str) or key is None:
        return JSONResponse(
            status_code=503, content=UNAVAILABLE, headers={"cache-control": "no-store"}
        )
    response = JSONResponse(content={"signed_in": True}, headers={"cache-control": "no-store"})
    name, value = shopper_cookie_header(key, session, DEMO_SHOPPER)
    response.headers.append(name.decode("ascii"), value.decode("ascii"))
    return response
