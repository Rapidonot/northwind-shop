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
# data/backend/shoppers.jsonl (a test checks it). S0188 (the user's pick after S0031, which has no
# `label_created` order) has eleven orders, one or more in each of the seven states:
# delivered O000800; out for delivery O000898; in transit O000928; picked up O000267, O000466,
# O000807, O000875; label created O000406, O000870; confirmed, not shipped O000517; on hold O000418.
DEMO_SHOPPER = "S0188"
DEMO_SHOPPERS = {DEMO_SHOPPER: "US"}

UNAVAILABLE = {
    "isError": True,
    "errorCategory": "transient",
    "isRetryable": True,
    "message": "Sign-in is unavailable right now.",
}


# Every method, so with the flag off any request is the same 404 as a path that does not exist
# (security review pass 1); with it on, only POST signs in.
@router.api_route(
    SIGNIN_PATH, methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False
)
def sign_in(request: Request) -> JSONResponse:
    if not demo_signin_enabled() or request.method != "POST":
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
