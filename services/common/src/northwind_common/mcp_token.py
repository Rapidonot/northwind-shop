"""A per-turn token for Northwind's MCP servers (CC-10; NFR-01, NFR-02, hard rules 4 and 8).

The gateway mints one for each turn from the caller it built itself. The token says who is asking
(`aud`, `region`), which agent (`agent`), for which turn (`turn`, which the server's search cap is
counted against) and until when (`exp`, at most `MAX_TTL_SECONDS` after minting). Servers take
audience and region from the token, never from tool arguments.

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
CLAIMS = frozenset({"aud", "region", "agent", "turn", "exp"})
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_B64 = re.compile(r"^[A-Za-z0-9_-]+$")
_REGION = re.compile(r"^[A-Z]{2,3}$")


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
    ttl: int = TTL_SECONDS,
) -> tuple[str, Claims]:
    """A fresh token and its claims. The turn id is new each time."""
    if AGENT_AUDIENCE.get(agent) != audience:
        raise ValueError("agent and audience do not match")
    if not 0 < ttl <= MAX_TTL_SECONDS:
        raise ValueError(f"ttl must be 1 to {MAX_TTL_SECONDS} seconds")
    if audience == "shopper" and region is None:
        raise ValueError("a shopper token needs a region")
    claims = Claims(
        aud=audience,
        region=region,
        agent=agent,
        turn=str(uuid.uuid4()),
        exp=int(now if now is not None else time.time()) + ttl,
    )
    body = {
        "aud": claims.aud,
        "region": claims.region,
        "agent": claims.agent,
        "turn": claims.turn,
        "exp": claims.exp,
    }
    payload = _b64(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{VERSION}.{payload}.{_sign(key, payload)}", claims


def _claims(body: Any, now: float) -> Claims:
    if not isinstance(body, dict) or set(body) != CLAIMS:
        raise TokenError
    aud, region, agent, turn, exp = (body[k] for k in ("aud", "region", "agent", "turn", "exp"))
    if (
        aud not in ("shopper", "staff")
        or not isinstance(agent, str)
        or AGENT_AUDIENCE.get(agent) != aud
        or not (region is None or (isinstance(region, str) and _REGION.match(region)))
        or (aud == "shopper" and region is None)
        or not (isinstance(turn, str) and _UUID4.match(turn))
        or not isinstance(exp, int)
        or isinstance(exp, bool)
        or exp <= now
        or exp > now + MAX_TTL_SECONDS
    ):
        raise TokenError
    return Claims(aud=aud, region=region, agent=agent, turn=turn, exp=exp)


def verify(token: object, *, key: bytes, now: float | None = None) -> Claims:
    """The claims of a valid token, else `TokenError`. The signature is checked, in constant
    time, before anything inside the token is read."""
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


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
