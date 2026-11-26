"""CHK-07, return initiation: the core of `request_return` (hard rules 6, 8, 9). The desk decides
again itself; an ineligible item stores nothing; keys and open requests never make a second one.

Also D9's wording table (the user, 2026-11-25): exactly one plain wording per status."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any

import pytest

from northwind_assistant.output_check import check_text
from northwind_common import returns_policy as rp
from northwind_common.errors import ToolError
from northwind_common.order_tool import STATUSES
from northwind_mcp_orders.book import SCAN_STATUSES, OrderBook
from northwind_mcp_orders.policy_data import PolicyData
from northwind_mcp_orders.returns import ReturnDesk
from northwind_mcp_orders.wording import STATUS_WORDING

pytestmark = pytest.mark.req("CHK-07")

POLICY = PolicyData.from_fixtures()
TURN = "0f8fad5b-d9cb-469f-a165-70867728950e"
OTHER_TURN = "0f8fad5b-d9cb-469f-a165-70867728950f"
ARGS: dict[str, Any] = {
    "shopper": "S0188",
    "region": "US",
    "turn": TURN,
    "order_id": "O000800",
    "variant_id": "P0085-FOR-REG-M",
    "condition": "unused",
    "key": "return-key-1",
}


@pytest.fixture
def desk() -> ReturnDesk:
    return ReturnDesk(OrderBook.from_data(), POLICY)


def ask(desk: ReturnDesk, **changes: Any) -> dict[str, Any]:
    return desk.request(**{**ARGS, **changes})


def code_of(desk: ReturnDesk, **changes: Any) -> str:
    with pytest.raises(ToolError) as caught:
        ask(desk, **changes)
    return caught.value.code


def test_an_eligible_item_is_recorded_as_requested(desk: ReturnDesk) -> None:
    made = ask(desk)
    assert (made["result"], made["request_status"], made["outcome"]) == (
        "recorded",
        "requested",
        "eligible",
    )
    assert len(desk.open) == 1
    words = " ".join(str(v) for v in made.values()).lower()
    assert not any(w in words for w in ("approved", "refund issued", "label", "case"))


def test_an_ambiguous_item_needs_review(desk: ReturnDesk) -> None:
    made = ask(desk, condition="damaged_on_arrival")
    assert (made["request_status"], made["reason"]) == ("needs_review", "damaged_on_arrival")


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"condition": "wear_and_tear"}, "wear_and_tear"),
        ({"order_id": "O000928", "variant_id": "P0082-CLY-REG-M"}, "not_delivered"),
        ({"condition": "worn_or_washed"}, "worn_or_washed"),
    ],
)
def test_an_ineligible_item_is_a_business_error_and_writes_nothing(
    desk: ReturnDesk, changes: dict[str, Any], code: str
) -> None:
    with pytest.raises(ToolError) as caught:
        ask(desk, **changes)
    assert (caught.value.category, caught.value.retryable) == ("business", False)
    assert caught.value.code == code and caught.value.message == rp.REASONS[code][1]
    assert desk.open == {} and not any(desk._keys.values())


def test_the_desk_decides_again_after_the_item_stops_being_eligible(desk: ReturnDesk) -> None:
    """The model's earlier "eligible" counts for nothing: the desk reads the scans now."""
    state = desk.book.orders.state
    number = desk.book.tracking_numbers["O000800"]
    events = desk.book.carrier.state.events
    events[number] = tuple(replace(e, on="2026-09-01") for e in events[number])
    assert state.orders["O000800"].region == "US"
    assert code_of(desk) == "out_of_window"
    assert desk.open == {}


def test_the_same_key_and_arguments_give_one_request(desk: ReturnDesk) -> None:
    first = ask(desk)
    for _ in range(10):
        again = ask(desk)
        assert again["result"] == "already_requested"
        assert {**again, "result": "recorded"} == first
    assert len(desk.open) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"condition": "sealed"},
        {"variant_id": "P0001-BLK-REG-M"},
        {"order_id": "O000928"},
    ],
)
def test_the_same_key_with_other_arguments_is_a_conflict(
    desk: ReturnDesk, changes: dict[str, Any]
) -> None:
    ask(desk)
    assert code_of(desk, **changes) == "idempotency_conflict"
    assert len(desk.open) == 1


@pytest.mark.parametrize("changes", [{"key": "another-key"}, {"turn": OTHER_TURN}])
def test_a_second_request_for_the_line_returns_the_first(
    desk: ReturnDesk, changes: dict[str, Any]
) -> None:
    first = ask(desk)
    again = ask(desk, condition="damaged_on_arrival", **changes)
    assert again["result"] == "already_requested" and again["condition"] == first["condition"]
    assert len(desk.open) == 1


@pytest.mark.parametrize("key", [None, "", "short", "x" * 65, "bad key!", "key-\n12345", 12345678])
def test_a_missing_or_malformed_key_is_refused(desk: ReturnDesk, key: Any) -> None:
    assert code_of(desk, key=key) == "invalid_idempotency_key"
    assert desk.open == {}


@pytest.mark.req("NFR-01")
@pytest.mark.parametrize(
    "changes",
    [{"order_id": "O000001"}, {"order_id": "O999999"}, {"shopper": "S0031"}, {"region": "UK"}],
)
def test_another_shoppers_order_is_not_found(desk: ReturnDesk, changes: dict[str, Any]) -> None:
    assert code_of(desk, **changes) == "not_found"
    assert desk.open == {}


def test_concurrent_requests_make_one(desk: ReturnDesk) -> None:
    results: list[str] = []
    threads = [
        threading.Thread(
            target=lambda n=n: results.append(ask(desk, key=f"thread-{n:04d}")["result"])
        )
        for n in range(16)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(set(results)) == ["already_requested", "recorded"]
    assert results.count("recorded") == 1 and len(desk.open) == 1


def test_a_product_of_unknown_kind_fails_closed(desk: ReturnDesk) -> None:
    desk.policy = PolicyData(POLICY.documents, {})
    assert code_of(desk) == "policy_unavailable"


# ---- D9: plain wording --------------------------------------------------------------------------


def test_every_status_has_exactly_one_plain_wording() -> None:
    assert set(STATUS_WORDING) == STATUSES | set(SCAN_STATUSES)
    assert len(set(STATUS_WORDING.values())) == len(STATUS_WORDING)
    for code, words in STATUS_WORDING.items():
        assert "_" not in words and words == words.lower(), code


@pytest.mark.parametrize("words", STATUS_WORDING.values())
def test_no_wording_confirms_an_order_or_gives_a_reason(words: str) -> None:
    for text in (f"Order O012345 is {words}.", f"Your order is {words}."):
        assert check_text(text) == text
    assert not any(w in words for w in ("confirmed", "placed", "approved", "fraud", "review"))
