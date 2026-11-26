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

Row #11 (RAG-04, decision 0022): a token may also carry `qf`, a query plan for one search, minted
only by the assistant's server for its own first search, never given to the CLI. It holds per-part
filters (colours, sizes, and a live product allow-list as a bitmap) and optional part texts.
mcp-rag reads filters from this claim, never from tool arguments, and a filter can only narrow a
search. The claim keeps the turn's own `turn` id (`mint(turn=…)`), so the search counts against the
same RAG-10 cap as the turn's other searches.

Row #27 (CHK-06, decision 0023): a shopper token for northwind-orders may also carry `shp`, the
signed-in shopper's ID, which the demo sign-in binds to the session. It is minted only for that one
server (`mint(shopper=…)` refuses any other), so no other server ever sees it, and the ID never
reaches the model: like `sid`, it rides in the token, which the CLI only forwards as a header.
Row #27 PR 2 (CHK-07, decision 0024, amending 0023): also for northwind-policy, whose one tool reads
the same shopper's order, and there only for the agent `shopper-assistant`. Still no other server.

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

from northwind_common.cards import contains_card_data
from northwind_common.tool_scopes import AGENT_AUDIENCE, Audience

KEY_ENV = "NORTHWIND_MCP_TOKEN_KEY"
MIN_KEY_CHARS = 32
TTL_SECONDS = 120
MAX_TTL_SECONDS = 120
VERSION = "v1"
CLAIMS = frozenset({"aud", "region", "agent", "srv", "turn", "exp"})
# Row #16: present only on a token minted with a session (`mint(session=…)`).
# Row #27: `shp`, only on a northwind-orders or northwind-policy token minted with a shopper
# (`mint(shopper=…)`).
OPTIONAL_CLAIMS = frozenset({"sid", "qf", "shp"})
MAX_TOKEN_CHARS = 2048
# Row #11 (RAG-04): the query-plan claim's limits.
PLAN_MAX_PARTS = 3
PLAN_MAX_TEXT = 200
PLAN_MAX_LIST = 16
PLAN_MAX_ITEM = 32
PLAN_PART_KEYS = frozenset({"q", "c", "s", "p"})
PRODUCT_ID = re.compile(r"P(\d{4})", re.ASCII)
MAX_PRODUCT_NUMBER = 256  # a bitmap of at most 64 hex characters
_BITMAP = re.compile(r"[0-9a-f]{1,64}", re.ASCII)
RAG_SERVER = "northwind-rag"
CATALOG_SERVER = "northwind-catalog"
ORDERS_SERVER = "northwind-orders"  # row #27 (CHK-06)
POLICY_SERVER = "northwind-policy"  # row #27 PR 2 (CHK-07)
SERVERS = frozenset({RAG_SERVER, CATALOG_SERVER, ORDERS_SERVER, POLICY_SERVER})
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_B64 = re.compile(r"^[A-Za-z0-9_-]+$")
_REGION = re.compile(r"^[A-Z]{2,3}$")
SESSION_ID = re.compile(r"[0-9a-f]{32}", re.ASCII)  # fullmatch: no trailing "\n"
# Row #27 PR 2: the one agent whose northwind-policy token may name a shopper.
POLICY_SHOPPER_AGENT = "shopper-assistant"
SHOPPER_ID = re.compile(r"S\d{4}", re.ASCII)  # fullmatch; the synthetic shoppers' IDs


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
    qf: dict[str, Any] | None = None  # row #11: a query plan for one server search
    shp: str | None = None  # row #27: the signed-in shopper, on an orders or policy token only


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


def plan_ok(plan: Any) -> bool:
    """A query-plan claim of the one allowed shape (row #11): `{"parts": [part, …]}`, 1 to 3 parts,
    each with only `q` (text), `c` (colours), `s` (sizes) and `p` (a product bitmap)."""
    if not isinstance(plan, dict) or set(plan) != {"parts"}:
        return False
    parts = plan["parts"]
    if not isinstance(parts, list) or not 1 <= len(parts) <= PLAN_MAX_PARTS:
        return False
    for part in parts:
        if not isinstance(part, dict) or set(part) - PLAN_PART_KEYS:
            return False
        text = part.get("q")
        if text is not None and not (
            isinstance(text, str)
            and text.strip()
            and len(text) <= PLAN_MAX_TEXT
            and "\x00" not in text
            and not contains_card_data(text)  # hard rule 1: never in a token (finding 2)
        ):
            return False
        for key in ("c", "s"):
            items = part.get(key)
            if items is not None and not (
                isinstance(items, list)
                and 1 <= len(items) <= PLAN_MAX_LIST
                and all(
                    isinstance(i, str) and 0 < len(i) <= PLAN_MAX_ITEM and not contains_card_data(i)
                    for i in items
                )
            ):
                return False
        bitmap = part.get("p")
        if bitmap is not None and not (isinstance(bitmap, str) and _BITMAP.fullmatch(bitmap)):
            return False
    return True


def product_bitmap(product_ids: Any) -> str:
    """Product IDs `P0001`..`P0256` as a hex bitmap: bit n-1 for `Pn`. Raises ValueError for any
    other ID, so a caller drops the filter rather than send a wrong one."""
    bits = 0
    for product_id in product_ids:
        found = PRODUCT_ID.fullmatch(product_id) if isinstance(product_id, str) else None
        number = int(found.group(1)) if found else 0
        if not 1 <= number <= MAX_PRODUCT_NUMBER:
            raise ValueError("a product ID outside the bitmap")
        bits |= 1 << (number - 1)
    return format(bits, "x")


def bitmap_products(bitmap: str) -> frozenset[str]:
    if not _BITMAP.fullmatch(bitmap):
        raise ValueError("not a product bitmap")
    bits = int(bitmap, 16)
    return frozenset(f"P{n + 1:04d}" for n in range(bits.bit_length()) if bits >> n & 1)


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
    turn: str | None = None,
    plan: dict[str, Any] | None = None,
    shopper: str | None = None,
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
        audience != "shopper" or not isinstance(session, str) or not SESSION_ID.fullmatch(session)
    ):
        raise ValueError("a session is 32 lowercase hex characters, on a shopper token only")
    if turn is not None and not (isinstance(turn, str) and _UUID4.fullmatch(turn)):
        raise ValueError("a turn is a version-4 UUID")
    if plan is not None and not (server == RAG_SERVER and plan_ok(plan)):
        raise ValueError("a query plan is for northwind-rag only, in the allowed shape")
    if shopper is not None and not _shopper_ok(audience, server, agent, shopper):
        raise ValueError("a shopper is an S-number, on an orders or policy shopper token only")
    claims = Claims(
        aud=audience,
        region=region,
        agent=agent,
        turn=turn if turn is not None else str(uuid.uuid4()),
        exp=int(now if now is not None else time.time()) + ttl,
        srv=server,
        sid=session,
        qf=plan,
        shp=shopper,
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
    if plan is not None:
        body["qf"] = plan
    if shopper is not None:
        body["shp"] = shopper
    payload = _b64(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    token = f"{VERSION}.{payload}.{_sign(key, payload)}"
    if len(token) > MAX_TOKEN_CHARS:
        raise ValueError("the token is too long")  # `_verify` would refuse it
    return token, claims


def _shopper_ok(aud: Any, srv: Any, agent: Any, shp: Any) -> bool:
    """`shp` (row #27): a shopper ID, on a shopper token for northwind-orders, or for
    northwind-policy and the assistant (PR 2, CHK-07), nothing else."""
    return (
        aud == "shopper"
        and (srv == ORDERS_SERVER or (srv == POLICY_SERVER and agent == POLICY_SHOPPER_AGENT))
        and isinstance(shp, str)
        and SHOPPER_ID.fullmatch(shp) is not None
    )


def _claims(body: Any, now: float) -> Claims:
    if not isinstance(body, dict) or not (CLAIMS <= set(body) <= CLAIMS | OPTIONAL_CLAIMS):
        raise TokenError
    sid = body.get("sid")
    qf = body.get("qf")
    shp = body.get("shp")
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
        or not (isinstance(turn, str) and _UUID4.fullmatch(turn))
        or not isinstance(exp, int)
        or isinstance(exp, bool)
        or exp <= now
        or exp > now + MAX_TTL_SECONDS
        or (
            "sid" in body
            and not (aud == "shopper" and isinstance(sid, str) and SESSION_ID.fullmatch(sid))
        )
        or ("qf" in body and not (srv == RAG_SERVER and plan_ok(qf)))
        or ("shp" in body and not _shopper_ok(aud, srv, agent, shp))
    ):
        raise TokenError
    return Claims(
        aud=aud, region=region, agent=agent, turn=turn, exp=exp, srv=srv, sid=sid, qf=qf, shp=shp
    )


def _verify(token: object, *, key: bytes, now: float | None = None) -> Claims:
    """The claims of a valid token for any server, else `TokenError`. The signature is checked, in
    constant time, before anything inside the token is read. Private (row #12, #10b re-review P1):
    `verify_for` is the only public check, so no gate accepts a token minted for another server."""
    if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS or not token.isascii():
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
