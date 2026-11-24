"""RAG-04 (row #11, decision 0022): the query-plan claim on a northwind-rag token.

Filters reach mcp-rag only as a signed claim the server mints for its own first search, never as a
tool argument the model could set. The claim has one shape, is for northwind-rag only, keeps the
turn's id (so RAG-10's cap counts the search), and fits the token limit or is refused at mint time.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

import pytest

from northwind_common.mcp_token import (
    CATALOG_SERVER,
    MAX_TOKEN_CHARS,
    RAG_SERVER,
    TokenError,
    _sign,
    bitmap_products,
    mint,
    plan_ok,
    product_bitmap,
    verify_for,
)

pytestmark = pytest.mark.req("RAG-04")

KEY = b"k" * 40
PLAN = {"parts": [{"c": ["Navy"], "s": ["M"], "p": product_bitmap(["P0001", "P0011"])}]}


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


def test_a_plan_rides_in_the_token_and_verifies() -> None:
    token, claims = minted(plan=PLAN)
    assert claims.qf == PLAN
    assert verify_for(token, key=KEY, server=RAG_SERVER).qf == PLAN


def test_a_token_without_a_plan_has_none() -> None:
    token, _ = minted()
    assert verify_for(token, key=KEY, server=RAG_SERVER).qf is None


def test_the_plan_token_keeps_the_turns_id() -> None:
    turn = str(uuid.uuid4())
    token, claims = minted(plan=PLAN, turn=turn)
    assert claims.turn == turn == verify_for(token, key=KEY, server=RAG_SERVER).turn


@pytest.mark.parametrize("turn", ["", "not-a-uuid", str(uuid.uuid1()), 5])
def test_a_bad_turn_is_refused(turn: Any) -> None:
    with pytest.raises(ValueError):
        minted(turn=turn)


def test_a_plan_is_for_northwind_rag_only() -> None:
    with pytest.raises(ValueError):
        minted(plan=PLAN, server=CATALOG_SERVER)


@pytest.mark.parametrize(
    "plan",
    [
        {},
        {"parts": []},
        {"parts": [{}] * 4},
        {"parts": [{"region": "UK"}]},
        {"parts": [{"visibility": "internal"}]},
        {"parts": [{"q": ""}]},
        {"parts": [{"q": "x" * 201}]},
        {"parts": [{"q": "a\x00b"}]},
        {"parts": [{"c": []}]},
        {"parts": [{"c": "Navy"}]},
        {"parts": [{"c": ["x" * 33]}]},
        {"parts": [{"s": ["M"] * 17}]},
        {"parts": [{"p": "XYZ"}]},
        {"parts": [{"p": "f" * 65}]},
        {"parts": [{"p": 7}]},
        {"parts": [{"c": ["Navy"]}], "extra": 1},
    ],
)
def test_other_shapes_are_refused(plan: Any) -> None:
    assert not plan_ok(plan)
    with pytest.raises(ValueError):
        minted(plan=plan)


def forged(body: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=").decode()
    return f"v1.{payload}.{_sign(KEY, payload)}"


def test_a_signed_token_with_a_bad_plan_is_refused() -> None:
    """Even signed with the key, a claim of another shape is refused on read."""
    _, claims = minted()
    body = {
        "aud": "shopper",
        "region": "US",
        "agent": "shopper-assistant",
        "srv": RAG_SERVER,
        "turn": claims.turn,
        "exp": claims.exp,
        "qf": {"parts": [{"visibility": "internal"}]},
    }
    with pytest.raises(TokenError):
        verify_for(forged(body), key=KEY, server=RAG_SERVER)
    body["qf"] = PLAN
    assert verify_for(forged(body), key=KEY, server=RAG_SERVER).qf == PLAN


def test_the_largest_plan_fits_the_token() -> None:
    every = product_bitmap([f"P{n:04d}" for n in range(1, 257)])
    part = {"q": "q" * 200, "c": ["Glacier Blue"] * 16, "s": ["60 x 102 in"] * 16, "p": every}
    # Three full parts do not fit: mint refuses rather than make a token `_verify` would refuse.
    with pytest.raises(ValueError):
        minted(plan={"parts": [part, part, part]})
    small = {"q": "navy fleece under 80", "c": ["Navy"], "s": ["M"], "p": every}
    token, _ = minted(plan={"parts": [small, small, small]})
    assert len(token) <= MAX_TOKEN_CHARS


def test_the_bitmap_round_trips() -> None:
    ids = {"P0001", "P0009", "P0200", "P0256"}
    assert bitmap_products(product_bitmap(ids)) == ids
    assert bitmap_products(product_bitmap([])) == frozenset()


@pytest.mark.parametrize("bad", ["P0000", "P0257", "X0001", "P01", 1])
def test_an_id_outside_the_bitmap_is_refused(bad: Any) -> None:
    with pytest.raises(ValueError):
        product_bitmap([bad])
