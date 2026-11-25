"""CHK-07: `request_return` over the real northwind-orders server, `get_order`'s plain wording (D9),
and the start-up fact check (D14). Hard rules 1, 2, 6, 8, 9 and 10."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from northwind_common.errors import ENVELOPE_KEYS
from northwind_common.tool_scopes import FORBIDDEN_FRAGMENTS
from northwind_mcp_orders import app, testing
from northwind_mcp_orders.book import DataMismatch, OrderBook
from northwind_mcp_orders.policy_data import FIXTURES, PolicyData
from northwind_mcp_orders.server import OrdersServer
from northwind_mcp_orders.tools import TOOLS
from northwind_mcp_orders.wording import STATUS_WORDING

pytestmark = pytest.mark.req("CHK-07")

GOOD = {
    "order_id": "O000800",
    "variant_id": "P0085-FOR-REG-M",
    "condition": "unused",
    "idempotency_key": "return-key-1",
}


@pytest.fixture
def live() -> Iterator[tuple[OrdersServer, str]]:
    instance = testing.server()
    with testing.running(instance) as url:
        yield instance, url


def call(url: str, token: str, name: str = "request_return", **args: Any) -> Any:
    return asyncio.run(testing.call(url, token, name, args))


def test_an_eligible_return_is_recorded_once_and_labelled(live: tuple[OrdersServer, str]) -> None:
    instance, url = live
    token, _ = testing.token()
    is_error, text, body = call(url, token, **GOOD)
    assert not is_error and text.startswith('<order_data tool="request_return">\n')
    assert (body["result"], body["request_status"]) == ("recorded", "requested")
    assert body["facts"] and {f["document_id"] for f in body["facts"]} == {"POL-RETURNS-US"}
    for _ in range(10):
        assert call(url, token, **GOOD)[2]["result"] == "already_requested"
    assert len(instance.desk.open) == 1
    assert "S0188" not in text


def test_the_key_is_per_turn_and_a_line_has_one_request(live: tuple[OrdersServer, str]) -> None:
    instance, url = live
    first, other = testing.token()[0], testing.token()[0]  # two turns
    call(url, first, **GOOD)
    # Another turn's use of the key is not a conflict; the line already has its request.
    assert call(url, other, **{**GOOD, "condition": "sealed"})[2]["result"] == "already_requested"
    # The same turn's is.
    assert call(url, first, **{**GOOD, "condition": "sealed"})[2]["code"] == "idempotency_conflict"
    assert len(instance.desk.open) == 1


@pytest.mark.parametrize(
    ("changes", "code", "category"),
    [
        ({"order_id": "O000928", "variant_id": "P0082-CLY-REG-M"}, "not_delivered", "business"),
        ({"condition": "wear_and_tear"}, "wear_and_tear", "business"),
        ({"order_id": "O000001"}, "not_found", "validation"),
        ({"order_id": 5}, "not_found", "validation"),
        ({"variant_id": "P0001-BLK-REG-M"}, "item_not_in_order", "validation"),
        ({"condition": "eligible"}, "invalid_condition", "validation"),
        ({"idempotency_key": "x"}, "invalid_idempotency_key", "validation"),
        ({"idempotency_key": None}, "invalid_idempotency_key", "validation"),
        ({"dry_run": True}, "unknown_argument", "validation"),
        ({"as_of": "2026-10-27"}, "unknown_argument", "validation"),
        ({"eligible": True}, "unknown_argument", "validation"),
        ({"shopper_id": "S0001"}, "unknown_argument", "validation"),
    ],
)
def test_a_refusal_is_a_structured_error_and_writes_nothing(
    live: tuple[OrdersServer, str], changes: dict[str, Any], code: str, category: str
) -> None:
    instance, url = live
    is_error, _, body = call(url, testing.token()[0], **{**GOOD, **changes})
    assert is_error and set(body) == ENVELOPE_KEYS
    assert (body["code"], body["errorCategory"], body["isRetryable"]) == (code, category, False)
    assert instance.desk.open == {}


@pytest.mark.parametrize("field", [*GOOD, "note"])
def test_card_data_anywhere_is_refused_before_any_read(
    live: tuple[OrdersServer, str], monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    def unread(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the store was read")

    monkeypatch.setattr(OrderBook, "view", unread)
    card = "4242 4242" + " 4242 4242"
    is_error, text, body = call(live[1], testing.token()[0], **{**GOOD, field: card})
    assert is_error and body["code"] == "card_data_refused" and "4242" not in text


@pytest.mark.req("NFR-01")
def test_no_sign_in_and_another_agent_get_nothing(live: tuple[OrdersServer, str]) -> None:
    instance, url = live
    assert call(url, testing.token(None)[0], **GOOD)[2]["code"] == "sign_in_required"
    for agent in ("shopper-portal", "shopper-query"):
        assert call(url, testing.token(agent=agent)[0], **GOOD)[2]["code"] == "not_allowed"
    assert instance.desk.open == {}


def test_the_server_has_exactly_a_read_and_a_request() -> None:
    assert set(TOOLS) == {"get_order", "request_return"}
    tool = TOOLS["request_return"].tool
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is True
    assert set(tool.input_schema["required"]) == set(GOOD) == set(tool.input_schema["properties"])
    assert tool.input_schema["additionalProperties"] is False
    for name in TOOLS:
        assert not any(part in name for part in FORBIDDEN_FRAGMENTS["shopper-assistant"])
    examples = tool.description or ""
    assert "O012345" in examples and "O000" not in examples  # no real order in what a model sees


# ---- D9 --------------------------------------------------------------------------------------


def test_get_order_gives_the_plain_words_beside_every_code(live: tuple[OrdersServer, str]) -> None:
    token, _ = testing.token()
    seen = set()
    for order_id in ("O000800", "O000928", "O000406", "O000418", "O000517", "O000898", "O000267"):
        _, _, body = call(live[1], token, "get_order", order_id=order_id)
        assert body["status_text"] == STATUS_WORDING[body["status"]]
        seen.add(body["status"])
        for scan in (body["tracking"] or {"scans": []})["scans"]:
            assert scan["status_text"] == STATUS_WORDING[scan["status"]]
            assert set(scan) == {"on", "status", "status_text"}
    assert seen == set(STATUS_WORDING) - {"processing"}


def test_the_store_view_itself_has_no_wording() -> None:
    view = OrderBook.from_data().view("O000800", shopper="S0188", region="US")
    assert "status_text" not in json.dumps(dict(view))


# ---- D14 -------------------------------------------------------------------------------------


def test_the_server_does_not_start_on_policy_that_lost_a_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("documents.jsonl", "products.jsonl", "manifest.json"):
        (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())
    path = tmp_path / "documents.jsonl"
    path.write_text(path.read_text().replace("**30 days**", "**90 days**"))
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["files"]["documents.jsonl"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setenv("NORTHWIND_MCP_TOKEN_KEY", "k" * 40)
    assert isinstance(app.build(), OrdersServer)  # the real fixtures start
    real = PolicyData.from_fixtures
    monkeypatch.setattr(PolicyData, "from_fixtures", lambda: real(tmp_path))
    with pytest.raises(DataMismatch, match="POL-RETURNS-US"):
        app.build()
