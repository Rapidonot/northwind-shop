"""The per-turn MCP token (CC-10; NFR-01, NFR-02, hard rules 4 and 8)."""

from __future__ import annotations

import base64
import json

import pytest

from northwind_common import mcp_token
from northwind_common.mcp_token import TokenError, TokenKeyError, mint, signing_key, verify

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("NFR-02")]

KEY = b"k" * 40
OTHER = b"o" * 40
NOW = 1_800_000_000.0


def shopper(**kw: object) -> str:
    args: dict[str, object] = {
        "audience": "shopper",
        "region": "UK",
        "agent": "shopper-assistant",
        "key": KEY,
        "now": NOW,
    }
    args.update(kw)
    return mint(**args)[0]  # type: ignore[arg-type]


def b64(obj: object) -> str:
    raw = json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def forge(body: dict[str, object], key: bytes = KEY) -> str:
    payload = b64(body)
    return f"v1.{payload}.{mcp_token._sign(key, payload)}"


def good_body(**kw: object) -> dict[str, object]:
    body: dict[str, object] = {
        "aud": "shopper",
        "region": "UK",
        "agent": "shopper-assistant",
        "turn": "1b4e28ba-2fa1-4d2e-883f-0016d3cca427",
        "exp": int(NOW) + 60,
    }
    body.update(kw)
    return body


def test_a_minted_token_verifies_to_its_claims() -> None:
    token, claims = mint(
        audience="shopper", region="UK", agent="shopper-assistant", key=KEY, now=NOW
    )
    assert verify(token, key=KEY, now=NOW + 1) == claims
    assert claims.aud == "shopper" and claims.region == "UK" and claims.agent == "shopper-assistant"
    assert claims.exp == int(NOW) + mcp_token.TTL_SECONDS == int(NOW) + 120


def test_every_mint_is_a_new_turn() -> None:
    a = mint(audience="shopper", region="UK", agent="shopper-assistant", key=KEY, now=NOW)[1]
    b = mint(audience="shopper", region="UK", agent="shopper-assistant", key=KEY, now=NOW)[1]
    assert a.turn != b.turn


def test_a_forged_signature_is_refused() -> None:
    token = shopper()
    with pytest.raises(TokenError):
        verify(token, key=OTHER, now=NOW)
    head, payload, sig = token.split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    with pytest.raises(TokenError):
        verify(f"{head}.{payload}.{flipped}", key=KEY, now=NOW)


def test_changing_a_claim_breaks_the_signature() -> None:
    _, _, sig = shopper().split(".")
    staff = b64(good_body(aud="staff", agent="catalog-subagent", region=None))
    with pytest.raises(TokenError):
        verify(f"v1.{staff}.{sig}", key=KEY, now=NOW)


def test_an_expired_token_is_refused() -> None:
    token = shopper()
    verify(token, key=KEY, now=NOW + 119)
    with pytest.raises(TokenError):
        verify(token, key=KEY, now=NOW + 120)


@pytest.mark.parametrize(
    "body",
    [
        good_body(exp=int(NOW) + 3600),  # signed, but lives too long
        good_body(aud="staff"),  # the assistant is a shopper agent
        good_body(agent="catalog-subagent"),  # a staff agent with a shopper audience
        good_body(agent="refund-bot"),
        good_body(region=None),
        good_body(region="uk"),
        good_body(region="UK<script>"),
        good_body(turn="not-a-uuid"),
        good_body(turn="1b4e28ba-2fa1-1d2e-883f-0016d3cca427"),  # uuid1, not uuid4
        good_body(exp=True),
        good_body(exp=str(int(NOW) + 60)),
        {**good_body(), "extra": 1},
        {k: v for k, v in good_body().items() if k != "turn"},
    ],
)
def test_a_signed_token_with_bad_claims_is_refused(body: dict[str, object]) -> None:
    with pytest.raises(TokenError):
        verify(forge(body), key=KEY, now=NOW)


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "v1",
        "v1..",
        "v2.a.b",
        "v1.a.b.c",
        "v1.!!!.sig",
        "v1.é.sig",
        "x" * 3000,
        42,
    ],
)
def test_a_malformed_token_is_refused(token: object) -> None:
    with pytest.raises(TokenError):
        verify(token, key=KEY, now=NOW)


def test_a_refusal_says_nothing_about_why() -> None:
    reasons = []
    for token in (None, shopper(now=NOW - 500), forge(good_body(aud="staff")), "v1.a.b"):
        with pytest.raises(TokenError) as caught:
            verify(token, key=KEY, now=NOW)
        reasons.append(str(caught.value))
    assert set(reasons) == {"invalid token"}


def test_a_staff_token_may_have_no_region() -> None:
    token, claims = mint(audience="staff", region=None, agent="catalog-subagent", key=KEY, now=NOW)
    assert verify(token, key=KEY, now=NOW).region is None and claims.aud == "staff"


@pytest.mark.parametrize(
    "kw",
    [
        {"audience": "staff"},  # the assistant is a shopper agent
        {"agent": "nobody"},
        {"region": None},
        {"ttl": 0},
        {"ttl": 121},
    ],
)
def test_minting_refuses_what_verify_would_refuse(kw: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        shopper(**kw)


def test_the_key_comes_only_from_the_environment_and_must_be_long() -> None:
    assert signing_key({mcp_token.KEY_ENV: "z" * 32}) == b"z" * 32
    for env in ({}, {mcp_token.KEY_ENV: "short"}):
        with pytest.raises(TokenKeyError) as caught:
            signing_key(env)
        assert "short" not in str(caught.value) and mcp_token.KEY_ENV in str(caught.value)


def test_the_key_is_not_in_the_token() -> None:
    token = shopper()
    payload = token.split(".")[1]
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    assert KEY.decode() not in token and KEY.decode() not in decoded
    assert set(json.loads(decoded)) == mcp_token.CLAIMS


def test_bearer_header() -> None:
    assert mcp_token.bearer("abc") == {"Authorization": "Bearer abc"}
