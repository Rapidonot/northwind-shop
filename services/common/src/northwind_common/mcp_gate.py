"""The token gate in front of every Northwind MCP server (CC-10; #10b; NFR-01, NFR-02).

Moved from `northwind_mcp_rag.server` at #10b, with one change: the gate is bound to one server
and accepts only a token minted for it (`mcp_token.verify_for`, security review L4). It is plain
ASGI and uses the standard library only, so `northwind_common` gains no dependency (the runner's
environment holds it).

The old gate read the header with Starlette's `Headers(scope=scope).get("authorization", "")`: the
first raw header whose name is exactly `b"authorization"` (Starlette lower-cases the key it looks
for, not the stored names; ASGI servers send names lower-cased), decoded as latin-1, else "".
`_authorization` does the same, and the parity tests compare the two. One difference, fail-closed: a
scope with no `headers` at all (which the ASGI spec does not allow) made Starlette raise; here it is
a 401.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from northwind_common.mcp_token import TokenError, verify_for

log = logging.getLogger("northwind_mcp_gate")

CLAIMS_KEY = "northwind_claims"

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _authorization(scope: Scope) -> str:
    for name, value in scope.get("headers") or ():
        if bytes(name) == b"authorization":
            return bytes(value).decode("latin-1")
    return ""


def token_gate(app: ASGIApp, *, key: bytes, clock: Callable[[], float], server: str) -> ASGIApp:
    """A bare 401 for any HTTP request without a valid token for `server`; the verified claims ride
    in the request scope's state. Lifespan events pass through; a websocket is closed, so a later
    route of that kind could never be reached ungated (CC-10 security review L3)."""

    async def gate(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            log.warning("request refused: websocket")
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            return
        scheme, _, token = _authorization(scope).partition(" ")
        try:
            if scheme != "Bearer":
                raise TokenError
            claims = verify_for(token, key=key, server=server, now=clock())
        except TokenError:
            log.warning("request refused: token")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"www-authenticate", b"Bearer"), (b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return
        state = dict(scope.get("state") or {})
        state[CLAIMS_KEY] = claims
        await app({**scope, "state": state}, receive, send)

    return gate
