"""CHK-06: order status and delivery tracking for the signed-in shopper's own orders (row #27).

Acceptance (PRD): "Status matches the order store and carrier tracking in test data." Every result
is checked against the store and the carrier fake as seeded from `data/backend`, with each order's
labelled outcome applied. Own orders only (NFR-01), allow-listed fields only (hard rule 7), live on
every call (hard rule 2), structured errors (hard rule 10), card data refused first (hard rule 1).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from itertools import product
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.fakes._base import FaultKind
from northwind_backend.fakes.orders import DATA
from northwind_backend.ports import OrderStatus, Tracking, TrackingEvent
from northwind_common.errors import ToolError
from northwind_common.mcp_token import CATALOG_SERVER
from northwind_common.order_tool import CONFIRMED_STATUSES, ORDER_ID, STATUSES
from northwind_mcp_orders import testing
from northwind_mcp_orders.book import OrderBook, shopper_status
from northwind_mcp_orders.server import OrdersServer
from northwind_mcp_orders.tools import TOOLS

pytestmark = pytest.mark.req("CHK-06")

DEMO = testing.DEMO_SHOPPER
ALLOWED_KEYS = {"order_id", "placed_on", "status", "lines", "tracking"}
FORBIDDEN = (
    "payment_token",
    "shipping_postal_code",
    "postal",
    "exception",
    "escalation",
    "shopper",
)


def _rows(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (DATA / name).read_text().splitlines() if line.strip()]


ORDERS = {row["order_id"]: row for row in _rows("orders.jsonl")}
TRACKING = {row["order_id"]: row for row in _rows("tracking.jsonl")}
LABELS = {row["order_id"]: row for row in _rows("replay_labels.jsonl")}
DEMO_ORDERS = sorted(o for o, row in ORDERS.items() if row["shopper_id"] == DEMO)


def expected(order_id: str) -> dict[str, Any]:
    """What the data files say, read independently of `OrderBook`."""
    row, track = ORDERS[order_id], TRACKING.get(order_id)
    if LABELS[order_id]["expected_outcome"] == "escalated":
        status = "on_hold"
    elif track is None:
        status = "confirmed"
    elif not track["events"]:
        status = "label_created"
    else:
        status = track["events"][-1]["status"]
    return {
        "order_id": order_id,
        "placed_on": row["placed_on"],
        "status": status,
        "lines": [
            {
                "product_id": li["product_id"],
                "variant_id": li["variant_id"],
                "quantity": li["quantity"],
            }
            for li in row["lines"]
        ],
        "tracking": None
        if track is None
        else {
            "carrier": track["carrier"],
            "tracking_number": track["tracking_number"],
            "scans": [{"on": e["on"], "status": e["status"]} for e in track["events"]],
        },
    }


@pytest.fixture(scope="module")
def book() -> OrderBook:
    return OrderBook.from_data()


@pytest.fixture
def live() -> Iterator[tuple[OrdersServer, str]]:
    instance = testing.server()
    with testing.running(instance) as url:
        yield instance, url


def call(url: str, token: str, args: dict[str, Any], name: str = "get_order") -> Any:
    return asyncio.run(testing.call(url, token, name, args))


# ---- 1. every order matches the store and the carrier ------------------------------------------


def test_all_1000_orders_match_the_store_and_the_carrier_as_their_owner(book: OrderBook) -> None:
    assert len(ORDERS) == 1000
    for order_id, row in ORDERS.items():
        got = book.view(order_id, shopper=row["shopper_id"], region=row["region"])
        assert dict(got) == expected(order_id), order_id


def test_the_demo_shoppers_orders_cover_every_state(book: OrderBook) -> None:
    """S0188 (the user's pick): every state a placed order can be in; `processing` (pending) does
    not occur in the labelled fixtures."""
    states = {book.view(o, shopper=DEMO, region="US")["status"] for o in DEMO_ORDERS}
    assert states == STATUSES - {"processing"}
    assert len(DEMO_ORDERS) == 11


def test_over_http_the_owner_gets_each_order_as_the_files_state_it(
    live: tuple[OrdersServer, str],
) -> None:
    _, url = live
    bearer, _ = testing.token()
    for order_id in DEMO_ORDERS:
        is_error, _, body = call(url, bearer, {"order_id": order_id})
        assert not is_error and body == expected(order_id)


# ---- 2. the status mapping ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "scans", "shopper"),
    [
        *[(s, None, "processing") for s in (OrderStatus.PENDING, OrderStatus.PAYING)],
        *[(OrderStatus.ESCALATED, scans, "on_hold") for scans in (None, (), ("delivered",))],
        (OrderStatus.CONFIRMED, None, "confirmed"),
        (OrderStatus.CONFIRMED, (), "label_created"),
        *[
            (OrderStatus.CONFIRMED, (*before, last), last)
            for before, last in product(
                [(), ("picked_up",), ("picked_up", "in_transit")],
                ["picked_up", "in_transit", "out_for_delivery", "delivered"],
            )
        ],
    ],
)
def test_every_store_status_and_last_scan_maps_to_one_shopper_status(
    status: OrderStatus, scans: tuple[str, ...] | None, shopper: str
) -> None:
    tracking = (
        None
        if scans is None
        else Tracking(
            "FWF-AAAAAAAA", "Fernway Freight", tuple(TrackingEvent("2026-10-01", s) for s in scans)
        )
    )
    assert shopper_status(status, tracking) == shopper
    assert shopper in STATUSES
    assert (shopper in CONFIRMED_STATUSES) is (status is OrderStatus.CONFIRMED)


@pytest.mark.parametrize("scan", ["lost", "returned_to_sender", "", "DELIVERED"])
def test_an_unknown_scan_fails_closed_as_unavailable_tracking(scan: str) -> None:
    tracking = Tracking("FWF-AAAAAAAA", "Fernway Freight", (TrackingEvent("2026-10-01", scan),))
    with pytest.raises(ToolError) as caught:
        shopper_status(OrderStatus.CONFIRMED, tracking)
    assert caught.value.category == "transient" and caught.value.code == "tracking_unavailable"


# ---- 3. own orders only (NFR-01) ---------------------------------------------------------------


@pytest.mark.req("NFR-01")
def test_another_shoppers_an_unknown_and_a_malformed_order_get_the_same_bytes(
    live: tuple[OrdersServer, str],
) -> None:
    _, url = live
    bearer, _ = testing.token()
    other = next(o for o, row in ORDERS.items() if row["shopper_id"] != DEMO)
    texts = set()
    for value in (other, "O999999", "O12", "P0001", "", "x" * 32, "O000800; DROP"):
        is_error, text, body = call(url, bearer, {"order_id": value})
        assert is_error and body["code"] == "not_found" and body["errorCategory"] == "validation"
        texts.add(text)
    assert len(texts) == 1
    assert other not in texts.pop()


@pytest.mark.req("NFR-01")
def test_a_non_string_order_id_gets_the_same_bytes_too(live: tuple[OrdersServer, str]) -> None:
    _, url = live
    bearer, _ = testing.token()
    _, reference, _ = call(url, bearer, {"order_id": "O999999"})
    for value in (64, None, ["O000800"], {"id": "O000800"}):
        _, text, _ = call(url, bearer, {"order_id": value})
        assert text == reference


def test_an_order_number_as_a_shopper_types_it_is_forgiven(live: tuple[OrdersServer, str]) -> None:
    _, url = live
    bearer, _ = testing.token()
    for typed in ("o000800", " #O000800 ", "#o000800"):
        is_error, _, body = call(url, bearer, {"order_id": typed})
        assert not is_error and body["order_id"] == "O000800"


@pytest.mark.req("NFR-01")
def test_a_token_with_no_signed_in_shopper_gets_no_order(live: tuple[OrdersServer, str]) -> None:
    _, url = live
    bearer, claims = testing.token(shopper=None)
    assert claims.shp is None
    is_error, _, body = call(url, bearer, {"order_id": DEMO_ORDERS[0]})
    assert is_error and body["code"] == "sign_in_required" and body["errorCategory"] == "permission"


@pytest.mark.req("NFR-01")
def test_the_region_in_the_token_must_be_the_orders(live: tuple[OrdersServer, str]) -> None:
    _, url = live
    bearer, _ = testing.token(region="UK")
    _, text, body = call(url, bearer, {"order_id": DEMO_ORDERS[0]})
    assert body["code"] == "not_found"


@pytest.mark.req("NFR-01")
def test_a_catalog_token_gets_a_bare_401_and_reaches_no_handler(
    live: tuple[OrdersServer, str],
) -> None:
    instance, url = live
    bearer, _ = testing.token(shopper=None, server_name=CATALOG_SERVER)
    with pytest.raises(Exception):  # noqa: B017, PT011 - the transport's own 401 error
        call(url, bearer, {"order_id": DEMO_ORDERS[0]})
    assert instance.calls == 0


@pytest.mark.req("NFR-01")
def test_only_the_assistant_is_offered_get_order(live: tuple[OrdersServer, str]) -> None:
    _, url = live

    async def names(agent: str) -> list[str]:
        bearer, _ = testing.token(agent=agent)
        async with testing.connect(url, bearer) as client:
            return [tool.name for tool in (await client.list_tools()).tools]

    assert asyncio.run(names("shopper-assistant")) == ["get_order"]
    assert asyncio.run(names("shopper-portal")) == []
    is_error, _, body = call(url, testing.token(agent="shopper-portal")[0], {"order_id": "O000800"})
    assert is_error and body["code"] == "not_allowed"


# ---- 4. allow-listed fields only (hard rule 7) --------------------------------------------------


def test_no_result_carries_a_forbidden_field_over_all_1000_orders(book: OrderBook) -> None:
    secrets = set()
    for order_id, row in ORDERS.items():
        got = book.view(order_id, shopper=row["shopper_id"], region=row["region"])
        assert set(got) == ALLOWED_KEYS
        text = json.dumps(got)
        assert not any(word in text for word in FORBIDDEN), order_id
        for value in (row["payment_token"], row["shipping_postal_code"], row["shopper_id"]):
            assert value not in text
        if row["exception"]:
            assert row["exception"] not in text
        reason = LABELS[order_id]["expected_reason"]
        if reason:
            assert reason not in text
        secrets.add(row["payment_token"])
    assert len(secrets) > 1


# ---- 5. live on every call (hard rule 2) --------------------------------------------------------


def test_a_status_or_scan_changed_while_the_server_runs_shows_in_the_next_result(
    live: tuple[OrdersServer, str],
) -> None:
    instance, url = live
    bearer, _ = testing.token()
    shipped = next(o for o in DEMO_ORDERS if expected(o)["status"] == "picked_up")
    assert call(url, bearer, {"order_id": shipped})[2]["status"] == "picked_up"
    instance.book.add_scan(shipped, "2026-11-20", "delivered")
    body = call(url, bearer, {"order_id": shipped})[2]
    assert body["status"] == "delivered" and body["tracking"]["scans"][-1]["on"] == "2026-11-20"
    instance.book.set_status(shipped, OrderStatus.ESCALATED)
    assert call(url, bearer, {"order_id": shipped})[2]["status"] == "on_hold"


# ---- 6. errors (hard rule 10) -------------------------------------------------------------------


def test_a_carrier_timeout_is_transient_and_distinct_from_a_label_with_no_scans(
    live: tuple[OrdersServer, str],
) -> None:
    instance, url = live
    unscanned = next(o for o in sorted(ORDERS) if expected(o)["status"] == "label_created")
    owner = ORDERS[unscanned]
    bearer, _ = testing.token(owner["shopper_id"], owner["region"])
    is_error, _, body = call(url, bearer, {"order_id": unscanned})
    assert not is_error and body["status"] == "label_created" and body["tracking"]["scans"] == []
    instance.book.carrier.faults.inject("track", FaultKind.TIMEOUT_BEFORE, times=1)
    is_error, _, body = call(url, bearer, {"order_id": unscanned})
    assert is_error and body == {
        "isError": True,
        "errorCategory": "transient",
        "isRetryable": True,
        "code": "tracking_unavailable",
        "message": "Tracking could not be read. Try again later.",
    }


def test_an_order_store_outage_is_transient_not_not_found(live: tuple[OrdersServer, str]) -> None:
    instance, url = live
    bearer, _ = testing.token()
    instance.book.orders.faults.inject("get", FaultKind.OUTAGE, times=1)
    is_error, _, body = call(url, bearer, {"order_id": DEMO_ORDERS[0]})
    assert is_error and body["code"] == "orders_unavailable" and body["isRetryable"] is True


def test_an_unknown_argument_is_refused(live: tuple[OrdersServer, str]) -> None:
    _, url = live
    bearer, _ = testing.token()
    for args in ({"order_id": "O000800", "shopper_id": "S0001"}, {"region": "UK"}, {}):
        is_error, _, body = call(url, bearer, args)
        assert is_error and body["code"] in {"unknown_argument", "not_found"}
    assert call(url, bearer, {"order_id": "O000800", "shopper_id": "S0001"})[2]["code"] == (
        "unknown_argument"
    )


# ---- 7. card data (hard rule 1) -----------------------------------------------------------------


@pytest.mark.req("PAY-05")
@pytest.mark.parametrize(
    "args",
    [
        {"order_id": "4242 4242 4242 4242"},
        {"order_id": "O000800", "note": "4242424242424242"},
        {"4242424242424242": "O000800"},
        {"order_id": "４２４２４２４２４２４２４２４２"},
    ],
)
def test_card_data_in_any_argument_is_refused_before_the_store_is_read(
    live: tuple[OrdersServer, str], args: dict[str, Any]
) -> None:
    instance, url = live
    bearer, _ = testing.token()
    instance.book.orders.faults.inject("get", FaultKind.OUTAGE)  # a store read would show
    is_error, text, body = call(url, bearer, args)
    assert is_error and body["code"] == "card_data_refused"
    assert "4242" not in text


def test_the_server_refuses_to_start_on_data_that_does_not_match_its_manifest(
    tmp_path: Path,
) -> None:
    for name in (
        "manifest.json",
        "orders.jsonl",
        "replay_labels.jsonl",
        "shoppers.jsonl",
        "tracking.jsonl",
    ):
        (tmp_path / name).write_bytes((DATA / name).read_bytes())
    with (tmp_path / "tracking.jsonl").open("a") as handle:
        handle.write("\n ")
    with pytest.raises(RuntimeError, match="tracking.jsonl"):
        OrderBook.from_data(tmp_path)


def test_the_tools_own_text_names_no_real_order() -> None:
    """Hard rule 2: the definition goes to the model with every signed-in turn, so its example is a
    well-formed number that is no order (the user's decision, with the prompt's placeholders)."""
    tool = TOOLS["get_order"].tool
    text = json.dumps(tool.model_dump(mode="json"))
    named = set(re.findall(r"O\d{6}", text))
    real = OrderBook.from_data().placed_on
    assert named == {"O012345"} and not named & set(real)
    assert ORDER_ID.fullmatch("O012345")  # still shows the right shape
