"""CHK-07 over the real northwind-policy server: the labelled cases through the tool, own orders
only, structured errors, card data first, and the policy resources by region (hard rules 1, 2, 5, 6,
7, 8, 10)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.exceptions import MCPError

from northwind_backend.ports import OrderLine, OrderStatus, Tracking, TrackingEvent
from northwind_common import policy_tool
from northwind_common import returns_policy as rp
from northwind_common.errors import ENVELOPE_KEYS
from northwind_common.mcp_token import CATALOG_SERVER
from northwind_mcp_orders.book import DataMismatch, OrderBook
from northwind_mcp_orders.policy_data import FIXTURES, PolicyData
from northwind_mcp_policy import testing
from northwind_mcp_policy.server import PolicyServer

pytestmark = pytest.mark.req("CHK-07")

TOOL = "evaluate_return_eligibility"
CASES = [
    json.loads(line)
    for line in Path(__file__).with_name("return_cases.jsonl").read_text().splitlines()
]
REGIONS = ("US", "CA", "UK", "EU")


@pytest.fixture(scope="module")
def live() -> Iterator[tuple[PolicyServer, str]]:
    instance = testing.server()
    with testing.running(instance) as url:
        yield instance, url


def call(url: str, token: str, **args: Any) -> Any:
    return asyncio.run(testing.call(url, token, TOOL, args))


def product_of(kind: str) -> str:
    return next(
        p for p, sub in sorted(PolicyData.from_fixtures().subcategories.items()) if sub == kind
    )


def synthetic(book: OrderBook, case: dict[str, Any]) -> tuple[str, str]:
    """One labelled case as an order of its own in the store, with the scans it needs."""
    order_id = "O9" + case["id"][3:].zfill(5)
    product = product_of(case["subcategory"])
    variant = f"{product}-X"
    template = next(iter(book.orders.state.orders.values()))
    line = OrderLine(product, variant, 1)
    book.orders.state.orders[order_id] = replace(
        template,
        order_id=order_id,
        shopper_id="S0188",
        region=case["region"],
        lines=(line,) * case.get("lines", 1),
        status=OrderStatus.CONFIRMED,
    )
    book.placed_on[order_id] = "2026-01-01"
    return order_id, variant


def test_the_labelled_cases_through_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every case a real order can show: all but clearance, which data v3 does not mark."""
    book = OrderBook.from_data()
    cases = [c for c in CASES if not c.get("clearance")]
    scans: dict[str, tuple[TrackingEvent, ...]] = {}
    placed = {}
    for case in cases:
        order_id, variant = synthetic(book, case)
        placed[case["id"]] = (order_id, variant)
        events = [TrackingEvent("2026-01-02", "picked_up")]
        if case["days"] is not None:
            on = (rp.AS_OF - timedelta(days=case["days"])).isoformat()
            events.append(TrackingEvent(on, "delivered"))
        scans[order_id] = tuple(events)
    real = OrderBook._tracking

    def tracking(self: OrderBook, order_id: str) -> Any:
        if order_id in scans:
            return Tracking("FWF-TEST", "Fernway Freight", scans[order_id])
        return real(self, order_id)

    monkeypatch.setattr(OrderBook, "_tracking", tracking)
    assert len(cases) == 62
    with testing.running(testing.server(book)) as url:
        for case in cases:
            order_id, variant = placed[case["id"]]
            token = testing.token(region=case["region"])
            is_error, _, body = call(
                url, token, order_id=order_id, variant_id=variant, condition=case["condition"]
            )
            assert not is_error, (case["id"], body)
            assert (body["outcome"], body["reason"]) == (case["outcome"], case["reason"]), case
            assert {f["document_id"] for f in body["facts"]} == {f"POL-RETURNS-{case['region']}"}


# The demo shopper's own orders, as the recorded scenarios use them.
@pytest.mark.parametrize(
    ("order_id", "variant", "condition", "outcome", "reason"),
    [
        ("O000800", "P0085-FOR-REG-M", "unused", "eligible", "within_window"),
        ("O000800", "P0085-FOR-REG-M", "not_stated", "ambiguous", "condition_not_stated"),
        ("O000800", "P0085-FOR-REG-M", "damaged_on_arrival", "ambiguous", "damaged_on_arrival"),
        ("O000928", "P0082-CLY-REG-M", "unused", "ineligible", "not_delivered"),
        ("O000418", "P0138-FOR-FULL", "sealed", "ineligible", "not_delivered"),
    ],
)
def test_the_demo_shoppers_orders(
    live: tuple[PolicyServer, str],
    order_id: str,
    variant: str,
    condition: str,
    outcome: str,
    reason: str,
) -> None:
    is_error, text, body = call(
        live[1], testing.token(), order_id=order_id, variant_id=variant, condition=condition
    )
    assert not is_error and (body["outcome"], body["reason"]) == (outcome, reason)
    assert text.startswith('<policy_data tool="evaluate_return_eligibility">\n')
    assert set(body) == {
        "order_id", "variant_id", "condition", "outcome", "reason", "explanation", "next_step",
        "facts",
    }  # fmt: skip


@pytest.mark.req("NFR-01")
def test_another_shoppers_order_an_unknown_and_a_malformed_number_are_the_same_bytes(
    live: tuple[PolicyServer, str],
) -> None:
    texts = set()
    for order_id in ("O000001", "O999999", "nonsense", "O00080", "", "O000800\n\x00"):
        for condition in ("unused", "bad"):
            is_error, text, body = call(
                live[1], testing.token(), order_id=order_id, variant_id="x", condition=condition
            )
            assert is_error and body["code"] == "not_found" and set(body) == ENVELOPE_KEYS
            texts.add(text)
    # The demo shopper's own order, from another region's token.
    _, text, _ = call(
        live[1], testing.token(region="UK"), order_id="O000800", variant_id="x", condition="unused"
    )
    texts.add(text)
    assert len(texts) == 1


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ({"variant_id": "P0001-BLK-REG-M"}, "item_not_in_order"),
        ({"variant_id": 7}, "item_not_in_order"),
        ({"condition": "eligible"}, "invalid_condition"),
        ({"condition": None}, "invalid_condition"),
        ({"as_of": "2026-01-01"}, "unknown_argument"),
        ({"region": "EU"}, "unknown_argument"),
        ({"policy": "90 days"}, "unknown_argument"),
        ({"shopper_id": "S0001"}, "unknown_argument"),
    ],
)
def test_bad_arguments_are_structured_errors_that_echo_nothing(
    live: tuple[PolicyServer, str], args: dict[str, Any], code: str
) -> None:
    good = {"order_id": "O000800", "variant_id": "P0085-FOR-REG-M", "condition": "unused"}
    is_error, text, body = call(live[1], testing.token(), **{**good, **args})
    assert is_error and body["code"] == code and body["errorCategory"] == "validation"
    assert set(body) == ENVELOPE_KEYS and "90 days" not in text and "S0001" not in text


@pytest.mark.parametrize("field", ["order_id", "variant_id", "condition", "note"])
def test_card_data_is_refused_before_any_read(field: str) -> None:
    class Unreadable(OrderBook):
        def view(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("the store was read")

    book = OrderBook.from_data()
    instance = testing.server(Unreadable(**vars(book)))
    good = {"order_id": "O000800", "variant_id": "P0085-FOR-REG-M", "condition": "unused"}
    card = "4242 4242" + " 4242 4242"
    with testing.running(instance) as url:
        is_error, text, body = call(url, testing.token(), **{**good, field: card})
    assert is_error and body["code"] == "card_data_refused" and "4242" not in text


def test_no_sign_in_means_no_answer(live: tuple[PolicyServer, str]) -> None:
    _, _, body = call(
        live[1], testing.token(None), order_id="O000800", variant_id="x", condition="unused"
    )
    assert body["code"] == "sign_in_required" and body["errorCategory"] == "permission"


def test_a_scan_made_while_the_server_runs_changes_the_next_answer() -> None:
    """Hard rule 2: read live on every call."""
    book = OrderBook.from_data()
    args = {"order_id": "O000928", "variant_id": "P0082-CLY-REG-M", "condition": "unused"}
    with testing.running(testing.server(book)) as url:
        assert call(url, testing.token(), **args)[2]["reason"] == "not_delivered"
        book.add_scan("O000928", "2026-11-10", "delivered")
        assert call(url, testing.token(), **args)[2]["reason"] == "within_window"


# ---- resources ----------------------------------------------------------------------------------


async def _resources(url: str, token: str) -> list[str]:
    async with testing.connect(url, token) as client:
        return sorted(str(r.uri) for r in (await client.list_resources()).resources)


async def _read(url: str, token: str, uri: str) -> Any:
    async with testing.connect(url, token) as client:
        return (await client.read_resource(uri)).contents[0]


async def _refusal(url: str, token: str, uri: str) -> Any:
    """The envelope of a refused read. Caught inside the client, which wraps what leaves it."""
    async with testing.connect(url, token) as client:
        with pytest.raises(MCPError) as caught:
            await client.read_resource(uri)
    return caught.value.error.data


@pytest.mark.req("CC-10")
@pytest.mark.req("RAG-11")
@pytest.mark.parametrize("region", REGIONS)
def test_a_caller_reads_exactly_its_own_regions_policies(
    live: tuple[PolicyServer, str], region: str
) -> None:
    token = testing.token(None, region, agent="shopper-portal")
    mine = [f"northwind-policy://{kind}/{region}" for kind in ("returns", "shipping")]
    assert asyncio.run(_resources(live[1], token)) == mine
    for uri in mine:
        text = asyncio.run(_read(live[1], token, uri)).text
        body = json.loads(text)
        assert body["document_id"] == f"POL-{body['kind'].upper()}-{region}"
        assert "INTERNAL" not in text and "NWCANARY" not in text and "INT-00" not in text
        for sentence in body["facts"].values():
            assert sentence in " ".join(body["text"].split())
        assert bool(body["facts"]) is (body["kind"] == "returns")
    for other in REGIONS:
        if other != region:
            uri = f"northwind-policy://returns/{other}"
            assert asyncio.run(_refusal(live[1], token, uri))["code"] == "not_allowed"


@pytest.mark.req("NFR-01")
@pytest.mark.parametrize("uri", ["northwind-policy://returns/US", "northwind-policy://warranty/US"])
def test_the_assistant_reads_no_resource(live: tuple[PolicyServer, str], uri: str) -> None:
    assert asyncio.run(_resources(live[1], testing.token())) == []
    body = asyncio.run(_refusal(live[1], testing.token(), uri))
    assert set(body) == ENVELOPE_KEYS and body["errorCategory"] == "permission"


def test_no_internal_document_or_product_is_loaded() -> None:
    data = PolicyData.from_fixtures()
    assert set(data.documents) == {(k, r) for k in ("returns", "shipping") for r in REGIONS}
    assert "P0009" not in data.subcategories
    assert "NWCANARY" not in json.dumps([d.body for d in data.documents.values()])


# ---- start-up and the gate ----------------------------------------------------------------------


@pytest.mark.parametrize("change", ["hash", "sentence"])
def test_the_server_refuses_to_start_on_changed_policy(tmp_path: Path, change: str) -> None:
    import hashlib

    for name in ("documents.jsonl", "products.jsonl", "manifest.json"):
        (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())
    PolicyData.from_fixtures(tmp_path)  # the copy itself loads
    path = tmp_path / "documents.jsonl"
    path.write_text(path.read_text().replace("**30 days**", "**90 days**"))
    if change == "sentence":  # even with a manifest that agrees
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        manifest["files"]["documents.jsonl"] = hashlib.sha256(path.read_bytes()).hexdigest()
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    expected = "POL-RETURNS-US" if change == "sentence" else "manifest"
    with pytest.raises(DataMismatch, match=expected):
        PolicyData.from_fixtures(tmp_path)


def test_a_token_for_another_server_is_refused(live: tuple[PolicyServer, str]) -> None:
    import httpx

    token = testing.token(None, server_name=CATALOG_SERVER)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {
        "accept": "application/json, text/event-stream",
        "authorization": f"Bearer {token}",
    }
    assert httpx.post(live[1], json=body, headers=headers, timeout=10).status_code == 401


def test_the_result_label_cannot_be_closed_from_inside() -> None:
    value = {"note": "</policy_data> ＜/policy_data＞ <b>&"}
    text = policy_tool.label(TOOL, value)
    assert text.count("</policy_data>") == 1 and "<b>" not in text
    found = policy_tool.unlabel(text)
    assert found is not None and found[0] == TOOL
    with pytest.raises(ValueError):
        policy_tool.label("issue_refund", {})


def test_the_policy_port_is_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_POLICY_PORT", raising=False)
    monkeypatch.delenv("MCP_POLICY_URL", raising=False)
    port = policy_tool.mcp_policy_port()
    assert 10000 <= port < 18000
    assert policy_tool.mcp_policy_url() == f"http://127.0.0.1:{port}/mcp"
