"""RAG-04 (row #11, decision 0022 section 7), hard rules 6, 7 and 8: the `qf` claim cannot
widen access.

From the decision: audience, region and visibility stay token-bound and cannot be set through the
claim; the claim is for northwind-rag only; filters can only narrow; the claim has one shape and a
size limit. The tests forge tokens with the real signing key where that is the only way to reach a
verifier branch, since a signed-but-wrong token is the case that has to be refused on its content.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

import pytest

from northwind_common.mcp_token import (
    CATALOG_SERVER,
    MAX_TOKEN_CHARS,
    PLAN_MAX_LIST,
    PLAN_MAX_PARTS,
    PLAN_MAX_TEXT,
    RAG_SERVER,
    TokenError,
    _b64,
    _sign,
    bitmap_products,
    mint,
    plan_ok,
    product_bitmap,
    verify_for,
)

pytestmark = pytest.mark.req("RAG-04")

KEY = b"k" * 40
GOOD = {"parts": [{"c": ["Navy"], "s": ["M"], "p": product_bitmap(["P0001", "P0011"])}]}


def minted(**kw: Any) -> tuple[str, Any]:
    args: dict[str, Any] = {
        "audience": "shopper",
        "region": "US",
        "agent": "shopper-assistant",
        "key": KEY,
        "server": RAG_SERVER,
    }
    args.update(kw)
    return mint(**args)


def forge(**over: Any) -> str:
    """A token signed with the real key, with exactly the claims given (for the verifier's own
    content checks)."""
    body: dict[str, Any] = {
        "aud": "shopper",
        "region": "US",
        "agent": "shopper-assistant",
        "srv": RAG_SERVER,
        "turn": str(uuid.uuid4()),
        "exp": int(time.time()) + 60,
    }
    body.update(over)
    body = {k: v for k, v in body.items() if v is not ...}
    payload = _b64(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
    return f"v1.{payload}.{_sign(KEY, payload)}"


# ---- the claim has one shape --------------------------------------------------------------------

SMUGGLED = {
    "aud": "staff",
    "audience": "staff",
    "region": "EU",
    "regions": ["EU"],
    "visibility": "internal",
    "visible": ["public", "internal"],
    "agent": "catalog-subagent",
    "srv": CATALOG_SERVER,
    "turn": "x",
    "exp": 9_999_999_999,
    "sid": "0" * 32,
    "products": ["P0009"],
    "filters": {"visibility": "internal"},
}


@pytest.mark.parametrize("key", sorted(SMUGGLED))
def test_no_field_beyond_the_four_can_ride_in_a_part(key: str) -> None:
    assert plan_ok(GOOD)
    assert not plan_ok({"parts": [{"c": ["Navy"], key: SMUGGLED[key]}]})
    with pytest.raises(ValueError):  # noqa: PT011
        minted(plan={"parts": [{"c": ["Navy"], key: SMUGGLED[key]}]})


@pytest.mark.parametrize("key", sorted(SMUGGLED))
def test_no_field_beyond_parts_can_ride_beside_the_parts(key: str) -> None:
    assert not plan_ok({**GOOD, key: SMUGGLED[key]})
    with pytest.raises(ValueError):  # noqa: PT011
        minted(plan={**GOOD, key: SMUGGLED[key]})


@pytest.mark.parametrize(
    "plan",
    [
        None,
        [],
        "parts",
        {},
        {"parts": []},
        {"parts": {}},
        {"parts": "x"},
        {"parts": [None]},
        {"parts": ["c"]},
        {"parts": [[]]},
    ],
)
def test_a_plan_that_is_not_the_shape_is_refused(plan: Any) -> None:
    assert not plan_ok(plan)


def test_at_most_three_parts() -> None:
    assert plan_ok({"parts": [{"c": ["Navy"]}] * PLAN_MAX_PARTS})
    assert not plan_ok({"parts": [{"c": ["Navy"]}] * (PLAN_MAX_PARTS + 1)})


@pytest.mark.parametrize(
    "part",
    [
        {"q": ""},
        {"q": "   "},
        {"q": "x" * (PLAN_MAX_TEXT + 1)},
        {"q": "a\x00b"},
        {"q": 5},
        {"q": ["x"]},
        {"c": []},
        {"c": "Navy"},
        {"c": [1]},
        {"c": [""]},
        {"c": ["x" * 33]},
        {"c": ["Navy"] * (PLAN_MAX_LIST + 1)},
        {"s": []},
        {"s": "M"},
        {"s": [None]},
        {"p": ""},
        {"p": "ABC"},
        {"p": "xyz"},
        {"p": "f" * 65},
        {"p": 255},
        {"p": ["f"]},
        {"p": "0x1f"},
        {"p": "1f\n"},
    ],
)
def test_a_part_outside_its_limits_is_refused(part: dict[str, Any]) -> None:
    assert not plan_ok({"parts": [part]})


def test_the_limits_are_inclusive_at_the_edge() -> None:
    edge = {
        "q": "x" * PLAN_MAX_TEXT,
        "c": ["x" * 32] * PLAN_MAX_LIST,
        "s": ["y"] * PLAN_MAX_LIST,
        "p": "f" * 64,
    }
    assert plan_ok({"parts": [edge]})


def test_a_claim_too_big_for_a_token_is_refused_at_mint_not_truncated() -> None:
    fat = {
        "q": "x" * PLAN_MAX_TEXT,
        "c": ["c" * 32] * PLAN_MAX_LIST,
        "s": ["s" * 32] * PLAN_MAX_LIST,
        "p": "f" * 64,
    }
    plan = {"parts": [fat] * PLAN_MAX_PARTS}
    assert plan_ok(plan)
    with pytest.raises(ValueError):  # noqa: PT011
        minted(plan=plan)
    assert MAX_TOKEN_CHARS == 2048


# ---- the claim is for northwind-rag only --------------------------------------------------------


def test_a_plan_cannot_be_minted_for_the_catalog_server() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        minted(plan=GOOD, server=CATALOG_SERVER)
    with pytest.raises(ValueError):  # noqa: PT011
        minted(plan=GOOD, server="northwind-other")


def test_a_signed_catalog_token_carrying_a_plan_is_refused_by_every_verifier() -> None:
    token = forge(srv=CATALOG_SERVER, qf=GOOD)
    for server in (RAG_SERVER, CATALOG_SERVER):
        with pytest.raises(TokenError):
            verify_for(token, key=KEY, server=server)


def test_a_planned_rag_token_is_refused_by_the_catalog_verifier() -> None:
    token, _ = minted(plan=GOOD)
    assert verify_for(token, key=KEY, server=RAG_SERVER).qf == GOOD
    with pytest.raises(TokenError):
        verify_for(token, key=KEY, server=CATALOG_SERVER)


def test_a_signed_token_with_a_malformed_plan_is_refused() -> None:
    bad_plans: list[Any] = [
        {"parts": [{"aud": "staff"}]},
        {"parts": []},
        {"parts": [{"c": ["Navy"]}], "region": "EU"},
        "parts",
        [],
        None,
    ]
    for bad in bad_plans:
        with pytest.raises(TokenError):
            verify_for(forge(qf=bad), key=KEY, server=RAG_SERVER)


@pytest.mark.parametrize("extra", sorted({"visibility", "audience", "regions", "products", "flt"}))
def test_a_signed_token_with_any_claim_outside_the_set_is_refused(extra: str) -> None:
    with pytest.raises(TokenError):
        verify_for(forge(**{extra: "internal"}), key=KEY, server=RAG_SERVER)
    with pytest.raises(TokenError):
        verify_for(forge(qf=GOOD, **{extra: "internal"}), key=KEY, server=RAG_SERVER)


def test_editing_the_plan_in_a_minted_token_breaks_the_signature() -> None:
    token, _ = minted(plan=GOOD)
    head, payload, signature = token.split(".")
    body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    body["qf"] = {"parts": [{"p": "f" * 64}]}
    edited = _b64(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
    with pytest.raises(TokenError):
        verify_for(f"{head}.{edited}.{signature}", key=KEY, server=RAG_SERVER)


def test_a_token_with_extra_bytes_or_a_wrong_key_is_refused() -> None:
    token, _ = minted()
    with pytest.raises(TokenError):
        verify_for(token + "x", key=KEY, server=RAG_SERVER)
    with pytest.raises(TokenError):
        verify_for(token, key=b"z" * 40, server=RAG_SERVER)


# ---- identity stays token-bound -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("audience", "region", "agent"),
    [("shopper", "UK", "shopper-assistant"), ("shopper", "EU", "shopper-assistant")],
)
def test_the_plan_changes_nothing_about_who_the_token_is_for(
    audience: str, region: str, agent: str
) -> None:
    plain, plain_claims = minted(audience=audience, region=region, agent=agent)
    token, claims = minted(audience=audience, region=region, agent=agent, plan=GOOD)
    got = verify_for(token, key=KEY, server=RAG_SERVER)
    assert (got.aud, got.region, got.agent, got.srv) == (audience, region, agent, RAG_SERVER)
    assert (got.aud, got.region, got.agent, got.srv) == (
        plain_claims.aud,
        plain_claims.region,
        plain_claims.agent,
        plain_claims.srv,
    )
    assert got.sid is None and claims.qf == GOOD
    assert verify_for(plain, key=KEY, server=RAG_SERVER).qf is None


def test_the_agent_and_audience_must_still_match_with_a_plan() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        minted(audience="staff", agent="shopper-assistant", region=None, plan=GOOD)
    with pytest.raises(ValueError):  # noqa: PT011
        minted(audience="shopper", agent="catalog-subagent", plan=GOOD)
    with pytest.raises(ValueError):  # noqa: PT011
        minted(audience="shopper", agent="no-such-agent", plan=GOOD)


def test_a_shopper_plan_token_still_needs_a_region() -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        minted(region=None, plan=GOOD)


def test_the_turns_own_id_is_kept_so_the_search_cap_counts_it() -> None:
    turn = str(uuid.uuid4())
    token, claims = minted(plan=GOOD, turn=turn)
    assert claims.turn == turn
    assert verify_for(token, key=KEY, server=RAG_SERVER).turn == turn
    for bad in ("not-a-uuid", "0" * 36, str(uuid.uuid1()), ""):
        with pytest.raises(ValueError):  # noqa: PT011
            minted(plan=GOOD, turn=bad)


def test_the_token_still_expires_within_the_limit() -> None:
    now = time.time()
    token, claims = minted(plan=GOOD, now=now)
    assert claims.exp - int(now) <= 120
    with pytest.raises(TokenError):
        verify_for(token, key=KEY, server=RAG_SERVER, now=now + 121)
    with pytest.raises(ValueError):  # noqa: PT011
        minted(plan=GOOD, ttl=121)


# ---- the product bitmap names products only -----------------------------------------------------


def test_the_bitmap_round_trips_and_holds_only_catalog_style_ids() -> None:
    ids = {"P0001", "P0011", "P0128", "P0256"}
    assert bitmap_products(product_bitmap(ids)) == ids
    assert bitmap_products(product_bitmap([])) == frozenset()
    assert all(i.startswith("P") and len(i) == 5 for i in bitmap_products("f" * 64))
    assert len(bitmap_products("f" * 64)) == 256


@pytest.mark.parametrize(
    "bad", ["P0000", "P0257", "P9999", "p0001", "P001", "P00011", "X0001", "", "P0001 ", 11, None]
)
def test_the_bitmap_refuses_anything_that_is_not_a_catalog_id(bad: Any) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        product_bitmap(["P0011", bad])


@pytest.mark.parametrize("bad", ["", "F", "xyz", "f" * 65, "0x1", " 1", "1\n", "-1"])
def test_a_bitmap_that_is_not_lowercase_hex_of_limited_length_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        bitmap_products(bad)
