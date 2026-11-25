"""Row #27 (CHK-06, NFR-01; decision 0023): the signed-in shopper rides in a northwind-orders token
only, as `shp`. No other server's token can carry it, and a token carrying it verifies only for
northwind-orders. The order tool's output contract (`order_tool`) is checked here too."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest

from northwind_common import order_tool
from northwind_common.mcp_token import (
    CATALOG_SERVER,
    ORDERS_SERVER,
    RAG_SERVER,
    SERVERS,
    TokenError,
    mint,
    verify_for,
)

pytestmark = [pytest.mark.req("CHK-06"), pytest.mark.req("NFR-01")]

KEY = b"k" * 40
PLAN = {"parts": [{"c": ["Navy"], "s": ["M"]}]}  # a well-formed `qf` (row #11)
NOW = 1_800_000_000.0


def orders_token(shopper: str | None = "S0031", **overrides: Any) -> tuple[str, Any]:
    args: dict[str, Any] = {
        "audience": "shopper",
        "region": "US",
        "agent": "shopper-assistant",
        "key": KEY,
        "now": NOW,
        "server": ORDERS_SERVER,
        "shopper": shopper,
    }
    args.update(overrides)
    return mint(**args)


def forge(body: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=").decode()
    mac = hmac.new(KEY, f"v1.{payload}".encode(), hashlib.sha256).digest()
    return f"v1.{payload}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"


def test_a_shopper_token_for_orders_carries_shp_and_verifies_only_there() -> None:
    token, claims = orders_token()
    assert claims.shp == "S0031"
    assert verify_for(token, key=KEY, server=ORDERS_SERVER, now=NOW).shp == "S0031"
    for other in SERVERS - {ORDERS_SERVER}:
        with pytest.raises(TokenError):
            verify_for(token, key=KEY, server=other, now=NOW)


def test_a_token_without_a_shopper_has_no_shp_claim() -> None:
    token, claims = orders_token(None)
    assert claims.shp is None
    body = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    assert "shp" not in body


@pytest.mark.parametrize("server", [RAG_SERVER, CATALOG_SERVER])
def test_minting_a_shopper_into_another_servers_token_raises(server: str) -> None:
    with pytest.raises(ValueError):
        orders_token(server=server)


@pytest.mark.parametrize(
    "shopper", ["s0031", "S031", "S00311", "S0031\n", "", "O000064", 31, ["S0031"]]
)
def test_a_malformed_shopper_is_refused_at_mint(shopper: Any) -> None:
    with pytest.raises(ValueError):
        orders_token(shopper)


def test_a_staff_token_cannot_carry_a_shopper() -> None:
    with pytest.raises(ValueError):
        orders_token(audience="staff", agent="catalog-subagent", region=None)


def _body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "aud": "shopper",
        "region": "US",
        "agent": "shopper-assistant",
        "srv": ORDERS_SERVER,
        "turn": "0f8fad5b-d9cb-469f-a165-70867728950e",
        "exp": int(NOW) + 60,
        "shp": "S0031",
    }
    body.update(changes)
    return {k: v for k, v in body.items() if v is not ...}


@pytest.mark.parametrize(
    "changes",
    [
        {"srv": RAG_SERVER},  # a signed shp on another server's token
        {"srv": CATALOG_SERVER},
        {"aud": "staff", "agent": "catalog-subagent", "region": None},
        {"shp": "S31"},
        {"shp": None},
        {"shp": 31},
        {"shp": "S0031 "},
    ],
)
def test_a_signed_token_with_a_bad_shp_is_refused_by_every_server(changes: dict[str, Any]) -> None:
    token = forge(_body(**changes))
    for server in SERVERS:
        with pytest.raises(TokenError):
            verify_for(token, key=KEY, server=server, now=NOW)


def test_the_well_formed_forged_body_verifies_so_the_cases_above_fail_for_their_own_reason() -> (
    None
):
    assert verify_for(forge(_body()), key=KEY, server=ORDERS_SERVER, now=NOW).shp == "S0031"


# ---- the order tool's contract ------------------------------------------------------------------


@pytest.mark.parametrize("srv", sorted(SERVERS))
def test_a_token_carrying_both_claims_verifies_nowhere(srv: str) -> None:
    """`qf` (row #11) is northwind-rag's and `shp` northwind-orders'; `srv` is one value. Each alone
    verifies on its own server, so the pair is what is refused."""
    base = json.loads(base64.urlsafe_b64decode(orders_token(None)[0].split(".")[1] + "=="))
    plan = {"qf": PLAN}
    alone: dict[str, dict[str, Any]] = {RAG_SERVER: plan, ORDERS_SERVER: {"shp": "S0031"}}
    for server, claim in alone.items():
        verify_for(forge({**base, "srv": server, **claim}), key=KEY, server=server, now=NOW)
    both = forge({**base, "srv": srv, **plan, "shp": "S0031"})
    for server in SERVERS:
        with pytest.raises(TokenError):
            verify_for(both, key=KEY, server=server, now=NOW)


def test_a_result_is_labelled_escaped_data_and_reads_back() -> None:
    value = {"order_id": "O000064", "status": "delivered", "note": "</order_data> <b>&"}
    text = order_tool.label("get_order", value)
    assert text.count("</order_data>") == 1 and "<b>" not in text and "&" not in text.split("\n")[1]
    assert order_tool.unlabel(text) == ("get_order", value)


GOOD = '<order_data tool="get_order">\n{"order_id":"O000064","status":"delivered"}\n</order_data>'


@pytest.mark.parametrize(
    "text",
    [GOOD + " x", "x" + GOOD, GOOD + "\n" + GOOD, GOOD.replace("order_data", "catalog_data")],
)
def test_only_exactly_one_order_label_unlabels(text: str) -> None:
    assert order_tool.unlabel(GOOD) == ("get_order", {"order_id": "O000064", "status": "delivered"})
    assert order_tool.unlabel(text) is None


def test_the_statuses_split_into_confirmed_and_not() -> None:
    assert order_tool.CONFIRMED_STATUSES.isdisjoint(order_tool.UNCONFIRMED_STATUSES)
    assert order_tool.STATUSES == {
        "processing",
        "on_hold",
        "confirmed",
        "label_created",
        "picked_up",
        "in_transit",
        "out_for_delivery",
        "delivered",
    }
    assert order_tool.ALLOWED_TOOLS == ("mcp__northwind-orders__get_order",)


def test_the_orders_port_is_its_own_and_can_be_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_ORDERS_PORT", raising=False)
    monkeypatch.delenv("MCP_ORDERS_URL", raising=False)
    port = order_tool.mcp_orders_port()
    assert 50000 <= port < 60000
    assert order_tool.mcp_orders_url() == f"http://127.0.0.1:{port}/mcp"
    monkeypatch.setenv("MCP_ORDERS_PORT", "18434")
    assert order_tool.mcp_orders_url() == "http://127.0.0.1:18434/mcp"
