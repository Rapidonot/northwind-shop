"""The shopper's session (row #16, CHK-01; decision 0019).

An anonymous session, enough to key a cart on, until accounts exist (FOUND-3, SA-04). A pure ASGI
middleware on the session paths reads the `nw_session` cookie. If its signature checks, the request
carries that session id; if not (absent, malformed, forged or signed with another key), it gets a
new one, and the response sets the cookie. The id goes into the MCP token (`sid`), never into a
prompt, a tool argument, a tool result or a log, so no random value reaches the model's input.

- The id is 128 random bits as 32 lowercase hex characters (`secrets.token_hex`).
- The cookie is `<id>.<mac>`, where the mac is HMAC-SHA256 over the id, under a key derived from
  `NORTHWIND_MCP_TOKEN_KEY` with the label `northwind-session-v1`, so no new secret is needed. One
  key for two purposes is accepted for the lab under the per-purpose-keys open item (#10 L7, #10b
  L3).
- It is `HttpOnly`, `SameSite=Strict` and `Path=/api`. It has no `Secure` flag, because the lab
  serves plain HTTP on localhost.
- Only `SESSION_PATHS` get a session, so the health check needs no key.
- Without the key, a request gets no session, which fails closed: the cart tools refuse
  (`session_required`) and GET /api/cart answers 503. The key is read again on the next request.

Row #27 (CHK-06, decision 0023): the demo sign-in. Only with `NORTHWIND_DEMO_SIGNIN=1` (dev, tests
and e2e) does `POST /api/session/shopper` bind the one fixed synthetic shopper to the session, in a
second cookie, `nw_shopper`: `<shopper>.<mac>`, where the mac is HMAC-SHA256 under the same derived
key over the label `northwind-shopper-v1`, the session id and the shopper ID. So it is valid only
with the session it was issued for: a new session drops it, and it cannot be moved to another one.
The middleware reads it only while the flag is on, so turning the flag off signs everyone out. The
shopper ID goes into the northwind-orders token only (`shp`), never into a prompt or a log.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
from collections.abc import Callable
from typing import Any

from northwind_common.mcp_gate import ASGIApp, Message, Receive, Scope, Send
from northwind_common.mcp_token import TokenKeyError, signing_key

log = logging.getLogger(__name__)

COOKIE = "nw_session"
STATE_KEY = "northwind_session"
SIGNIN_PATH = "/api/session/shopper"  # row #27
SESSION_PATHS = ("/api/chat", "/api/cart", SIGNIN_PATH)
LABEL = b"northwind-session-v1"
# Row #27 (CHK-06): the demo sign-in.
SHOPPER_COOKIE = "nw_shopper"
SHOPPER_STATE_KEY = "northwind_shopper"
SHOPPER_LABEL = b"northwind-shopper-v1"
DEMO_SIGNIN_ENV = "NORTHWIND_DEMO_SIGNIN"
_SHOPPER = re.compile(r"S\d{4}", re.ASCII)
_ID = re.compile(r"[0-9a-f]{32}", re.ASCII)
_MAC = re.compile(r"[0-9a-f]{64}", re.ASCII)


def session_key(token_key: bytes) -> bytes:
    """The cookie key, derived from the MCP token key with a fixed label."""
    return hmac.new(token_key, LABEL, hashlib.sha256).digest()


def _mac(key: bytes, session: str) -> str:
    return hmac.new(key, session.encode("ascii"), hashlib.sha256).hexdigest()


def sign(key: bytes, session: str) -> str:
    return f"{session}.{_mac(key, session)}"


def verified(key: bytes, value: str) -> str | None:
    """The session id in a cookie value, if its signature checks; else None."""
    session, _, mac = value.partition(".")
    if not _ID.fullmatch(session) or not _MAC.fullmatch(mac):
        return None
    return session if hmac.compare_digest(_mac(key, session), mac) else None


def demo_signin_enabled() -> bool:
    """Read on every request, so the flag never sticks."""
    return os.environ.get(DEMO_SIGNIN_ENV) == "1"


def _shopper_mac(key: bytes, session: str, shopper: str) -> str:
    message = b"|".join((SHOPPER_LABEL, session.encode("ascii"), shopper.encode("ascii")))
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def sign_shopper(key: bytes, session: str, shopper: str) -> str:
    if not _ID.fullmatch(session) or not _SHOPPER.fullmatch(shopper):
        raise ValueError("not a session and a shopper")
    return f"{shopper}.{_shopper_mac(key, session, shopper)}"


def verified_shopper(key: bytes, session: str, value: str) -> str | None:
    """The shopper in an `nw_shopper` value, if it was signed for this session; else None."""
    shopper, _, mac = value.partition(".")
    if not _SHOPPER.fullmatch(shopper) or not _MAC.fullmatch(mac):
        return None
    return shopper if hmac.compare_digest(_shopper_mac(key, session, shopper), mac) else None


def shopper_cookie_header(key: bytes, session: str, shopper: str) -> tuple[bytes, bytes]:
    signed = sign_shopper(key, session, shopper)
    value = f"{SHOPPER_COOKIE}={signed}; HttpOnly; SameSite=Strict; Path=/api"
    return (b"set-cookie", value.encode("ascii"))


def cookie_values(scope: Scope, name: str) -> list[str]:
    """Every value sent for cookie `name`, in header order. Lenient on purpose (H10, row #27): each
    `;`-separated pair is read on its own, so a valueless cookie (`flag`), a name with a space or an
    unclosed quote from another localhost app no longer hides ours, as `SimpleCookie` did by
    dropping the whole jar. A value is taken as sent, with no unquoting: only the exact bytes the
    gateway signed verify (`verified`)."""
    found: list[str] = []
    for header, value in scope.get("headers") or ():
        if bytes(header) != b"cookie":
            continue
        for pair in bytes(value).decode("latin-1").split(";"):
            key, sep, item = pair.partition("=")
            if sep and key.strip() == name:
                found.append(item.strip())
    return found


def _cookie_value(scope: Scope, name: str = COOKIE) -> str:
    """The one value sent for cookie `name`, or "" for none or more than one. Two values mean
    another localhost app planted one (browsers scope cookies by host, not port, and send a longer
    path first), so neither is trusted (security review pass 1, L3): fail closed."""
    values = cookie_values(scope, name)
    return values[0] if len(values) == 1 else ""


def set_cookie_header(key: bytes, session: str) -> tuple[bytes, bytes]:
    value = f"{COOKIE}={sign(key, session)}; HttpOnly; SameSite=Strict; Path=/api"
    return (b"set-cookie", value.encode("ascii"))


def session_middleware(app: ASGIApp, key: Callable[[], bytes] | None = None) -> ASGIApp:
    """Binds a session to every HTTP request on `SESSION_PATHS` (module docstring). `key` defaults
    to the derived cookie key, read from the environment on each request (an HMAC; nothing cached,
    so a changed key never leaves a stale one in use)."""

    def current_key() -> bytes:
        return key() if key is not None else session_key(signing_key())

    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") not in SESSION_PATHS:
            await app(scope, receive, send)
            return
        if scope.get("path") == SIGNIN_PATH and not demo_signin_enabled():
            await app(scope, receive, send)  # row #27: no session, so the 404 sets no cookie either
            return
        try:
            cookie_key = current_key()
        except TokenKeyError:
            log.error("session unavailable: no key")
            await app(scope, receive, send)
            return
        session = verified(cookie_key, _cookie_value(scope))
        issue = session is None
        if session is None:
            session = secrets.token_hex(16)
        state: dict[str, Any] = dict(scope.get("state") or {})
        state[STATE_KEY] = session
        if not issue and demo_signin_enabled():  # row #27: a new session has no sign-in yet
            sent = _cookie_value(scope, SHOPPER_COOKIE)
            shopper = verified_shopper(cookie_key, session, sent) if sent else None
            if shopper is not None:
                state[SHOPPER_STATE_KEY] = shopper

        async def send_with_cookie(message: Message) -> None:
            if issue and message["type"] == "http.response.start":
                headers = [*message.get("headers", []), set_cookie_header(cookie_key, session)]
                message = {**message, "headers": headers}
            await send(message)

        await app({**scope, "state": state}, receive, send_with_cookie)

    return middleware


class SessionMiddleware:
    """`session_middleware` as a class, for `FastAPI.add_middleware`."""

    def __init__(self, app: ASGIApp, key: Callable[[], bytes] | None = None) -> None:
        self._inner = session_middleware(app, key)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._inner(scope, receive, send)
