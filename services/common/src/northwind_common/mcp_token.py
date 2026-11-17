"""A per-turn token for Northwind's MCP servers (CC-10; NFR-01, NFR-02, hard rules 4 and 8).

The gateway mints one for each turn from the caller it built itself. The token says who is asking
(`aud`, `region`), which agent (`agent`), for which server (`srv`), for which turn (`turn`, which
the server's search cap is counted against) and until when (`exp`, at most `MAX_TTL_SECONDS` after
minting). A shopper token may also carry `sid`, the gateway's session id (row #16, CHK-01, decision
0019), which the cart is keyed on: it is present only when the minter passes a session, so a token
without one keeps the claim set it always had. The session id never reaches the model: it rides in
the token, which the CLI only forwards as a header. Servers take audience and region from the token,
never from tool arguments, and accept only a token minted for themselves (`verify_for`; #10b, CC-10
security review L4), so a token for one server is refused by every other one that shares the key.

Format: `v1.<base64url JSON claims>.<base64url HMAC-SHA256>`, signed with the key in
`NORTHWIND_MCP_TOKEN_KEY`. The key comes only from the environment, is never logged, and never
reaches the CLI: the CLI holds only the token. Every failure is the same `TokenError`, with no
detail, so a caller learns nothing from a refusal.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from northwind_common.tool_scopes import AGENT_AUDIENCE, Audience

KEY_ENV = "NORTHWIND_MCP_TOKEN_KEY"
MIN_KEY_CHARS = 32
TTL_SECONDS = 120
MAX_TTL_SECONDS = 120
VERSION = "v1"
CLAIMS = frozenset({"aud", "region", "agent", "srv", "turn", "exp"})
# Row #16: present only on a token minted with a session (`mint(session=…)`).
OPTIONAL_CLAIMS = frozenset({"sid"})
RAG_SERVER = "northwind-rag"
CATALOG_SERVER = "northwind-catalog"
SERVERS = frozenset({RAG_SERVER, CATALOG_SERVER})
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_B64 = re.compile(r"^[A-Za-z0-9_-]+$")
_REGION = re.compile(r"^[A-Z]{2,3}$")
SESSION_ID = re.compile(r"^[0-9a-f]{32}$")


class TokenError(Exception):
    """The token is missing, malformed, forged, expired or for the wrong audience."""

    def __init__(self) -> None:
        super().__init__("invalid token")


class TokenKeyError(RuntimeError):
    """The signing key is not configured. Says which variable, never its value."""


@dataclass(frozen=True)
class Claims:
    aud: Audience
    region: str | None
    agent: str
    turn: str
    exp: int
    srv: str
    sid: str | None = None  # row #16: the gateway's session, which the cart is keyed on


def signing_key(environ: dict[str, str] | None = None) -> bytes:
    value = (environ if environ is not None else os.environ).get(KEY_ENV, "")
    if len(value) < MIN_KEY_CHARS:
        raise TokenKeyError(f"{KEY_ENV} is not set to a key of at least {MIN_KEY_CHARS} characters")
    return value.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    if not _B64.match(text):
        raise TokenError
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise TokenError from None


def _sign(key: bytes, payload: str) -> str:
    return _b64(hmac.new(key, f"{VERSION}.{payload}".encode("ascii"), hashlib.sha256).digest())


def mint(
    *,
    audience: Audience,
    region: str | None,
    agent: str,
    key: bytes,
    now: float | None = None,
    server: str,
    ttl: int = TTL_SECONDS,
    session: str | None = None,
) -> tuple[str, Claims]:
    """A fresh token and its claims, for one server. The turn id is new each time.

    `server` has no default (row #12, #10b security review L4): every minter names the server the
    token is for, so a forgotten argument is a `TypeError`, never a token for the wrong server."""
    if server not in SERVERS:
        raise ValueError("unknown server")
    if AGENT_AUDIENCE.get(agent) != audience:
        raise ValueError("agent and audience do not match")
    if not 0 < ttl <= MAX_TTL_SECONDS:
        raise ValueError(f"ttl must be 1 to {MAX_TTL_SECONDS} seconds")
    if audience == "shopper" and region is None:
        raise ValueError("a shopper token needs a region")
    if session is not None and (
        audience != "shopper" or not isinstance(session, str) or not SESSION_ID.match(session)
    ):
        raise ValueError("a session is 32 lowercase hex characters, on a shopper token only")
    claims = Claims(
        aud=audience,
        region=region,
        agent=agent,
        turn=str(uuid.uuid4()),
        exp=int(now if now is not None else time.time()) + ttl,
        srv=server,
        sid=session,
    )
    body: dict[str, Any] = {
        "aud": claims.aud,
        "region": claims.region,
        "agent": claims.agent,
        "srv": claims.srv,
        "turn": claims.turn,
        "exp": claims.exp,
    }
    if session is not None:
        body["sid"] = session
    payload = _b64(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{VERSION}.{payload}.{_sign(key, payload)}", claims


def _claims(body: Any, now: float) -> Claims:
    if not isinstance(body, dict) or not (
        set(body) == CLAIMS or set(body) == CLAIMS | OPTIONAL_CLAIMS
    ):
        raise TokenError
    sid = body.get("sid")
    aud, region, agent, srv, turn, exp = (
        body[k] for k in ("aud", "region", "agent", "srv", "turn", "exp")
    )
    if (
        aud not in ("shopper", "staff")
        or not isinstance(agent, str)
        or AGENT_AUDIENCE.get(agent) != aud
        or not isinstance(srv, str)
        or srv not in SERVERS
        or not (region is None or (isinstance(region, str) and _REGION.match(region)))
        or (aud == "shopper" and region is None)
        or not (isinstance(turn, str) and _UUID4.match(turn))
        or not isinstance(exp, int)
        or isinstance(exp, bool)
        or exp <= now
        or exp > now + MAX_TTL_SECONDS
        or (
            "sid" in body
            and not (aud == "shopper" and isinstance(sid, str) and SESSION_ID.match(sid))
        )
    ):
        raise TokenError
    return Claims(aud=aud, region=region, agent=agent, turn=turn, exp=exp, srv=srv, sid=sid)


def _verify(token: object, *, key: bytes, now: float | None = None) -> Claims:
    """The claims of a valid token for any server, else `TokenError`. The signature is checked, in
    constant time, before anything inside the token is read. Private (row #12, #10b re-review P1):
    `verify_for` is the only public check, so no gate accepts a token minted for another server."""
    if not isinstance(token, str) or len(token) > 2048 or not token.isascii():
        raise TokenError
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        raise TokenError
    _, payload, signature = parts
    if not hmac.compare_digest(_sign(key, payload).encode(), signature.encode()):
        raise TokenError
    try:
        body = json.loads(_unb64(payload))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TokenError from None
    return _claims(body, now if now is not None else time.time())


def verify_for(token: object, *, key: bytes, server: str, now: float | None = None) -> Claims:
    """`_verify`, and the token must have been minted for `server` (L4). The only public check: a
    token for another server is the same bare `TokenError`."""
    if server not in SERVERS:
        raise TokenError
    claims = _verify(token, key=key, now=now)
    if not hmac.compare_digest(claims.srv.encode(), server.encode()):
        raise TokenError
    return claims


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
