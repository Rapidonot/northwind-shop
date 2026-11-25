"""CHK-07 gap tests (test-writer) for `ReturnDesk`: shopper and region mismatches, keys across
shoppers and turns, eviction at MAX_KEYS, concurrency on one key, and a scan that changes between
the check and the request (hard rules 2, 7, 9, 10)."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from northwind_common import returns_policy as rp
from northwind_common.errors import ToolError
from northwind_mcp_orders.book import OrderBook
from northwind_mcp_orders.policy_data import PolicyData
from northwind_mcp_orders.returns import MAX_KEYS, ReturnDesk, assess

pytestmark = pytest.mark.req("CHK-07")

POLICY = PolicyData.from_fixtures()
TURN = "0f8fad5b-d9cb-469f-a165-70867728950e"
OTHER_TURN = "0f8fad5b-d9cb-469f-a165-70867728950f"
VARIANT = "P0085-FOR-REG-M"
ARGS: dict[str, Any] = {
    "shopper": "S0188",
    "region": "US",
    "turn": TURN,
    "order_id": "O000800",
    "variant_id": VARIANT,
    "condition": "unused",
    "key": "return-key-1",
}


def add_order(book: OrderBook, order_id: str, shopper: str, region: str = "US") -> None:
    """A copy of O000800 (delivered 20 days before the fixed date) under another ID and owner."""
    orders = book.orders.state.orders
    orders[order_id] = replace(
        orders["O000800"], order_id=order_id, shopper_id=shopper, region=region
    )
    book.tracking_numbers[order_id] = book.tracking_numbers["O000800"]
    book.placed_on[order_id] = "2026-10-20"


@pytest.fixture
def desk() -> ReturnDesk:
    return ReturnDesk(OrderBook.from_data(), POLICY)


def ask(desk: ReturnDesk, **changes: Any) -> dict[str, Any]:
    return desk.request(**{**ARGS, **changes})


def code_of(desk: ReturnDesk, **changes: Any) -> str:
    with pytest.raises(ToolError) as caught:
        ask(desk, **changes)
    return caught.value.code


# ---- shopper and region ------------------------------------------------------------------------


@pytest.mark.req("NFR-01")
@pytest.mark.parametrize(
    ("shopper", "region"),
    [
        ("S0188", None),
        ("S0188", ""),
        ("S0188", "us"),
        ("S0188", "AU"),
        ("S0188", "EU"),
        ("S0031", "US"),  # right region, wrong shopper
        ("S0031", "EU"),
        ("s0188", "US"),
        ("S0188 ", "US"),
        ("", "US"),
    ],
)
def test_only_the_owner_in_the_orders_own_region_gets_the_order(
    desk: ReturnDesk, shopper: str, region: str | None
) -> None:
    for call in (
        lambda: ask(desk, shopper=shopper, region=region),
        lambda: assess(
            desk.book,
            desk.policy,
            shopper=shopper,
            region=region,
            order_id="O000800",
            variant_id=VARIANT,
            condition="unused",
        ),
    ):
        with pytest.raises(ToolError) as caught:
            call()
        assert caught.value.code == "not_found"
    assert desk.open == {} and not any(desk._keys.values())


def test_the_order_is_read_before_the_other_arguments_so_nothing_leaks_about_it(
    desk: ReturnDesk,
) -> None:
    """Another shopper's order gives the same refusal whatever else is wrong with the request."""
    bad = {"variant_id": "nope", "condition": "bogus"}
    mine = {"shopper": "S0031"}
    assert code_of(desk, **mine, **bad) == code_of(desk, **mine) == "not_found"
    assert code_of(desk, **bad) in ("invalid_condition",)


@pytest.mark.req("NFR-01")
def test_a_regional_order_is_decided_by_its_own_regions_policy() -> None:
    book = OrderBook.from_data()
    add_order(book, "O900001", "S0188", "UK")
    add_order(book, "O900002", "S0188", "CA")
    desk = ReturnDesk(book, POLICY)
    uk = desk.request(**{**ARGS, "order_id": "O900001", "region": "UK", "key": "key-uk-0001"})
    ca = desk.request(**{**ARGS, "order_id": "O900002", "region": "CA", "key": "key-ca-0001"})
    assert {f["document_id"] for f in uk["facts"]} == {"POL-RETURNS-UK"}
    assert {f["document_id"] for f in ca["facts"]} == {"POL-RETURNS-CA"}
    # The token's region is the order's region or nothing: the UK order is not found as a US caller.
    with pytest.raises(ToolError) as caught:
        desk.request(**{**ARGS, "order_id": "O900001", "region": "US", "key": "key-uk-0002"})
    assert caught.value.code == "not_found"


# ---- keys: shoppers, turns, lines --------------------------------------------------------------


def test_the_same_key_for_two_shoppers_is_two_requests_and_never_a_conflict_or_a_leak() -> None:
    book = OrderBook.from_data()
    add_order(book, "O900003", "S0031")
    desk = ReturnDesk(book, POLICY)
    first = ask(desk)
    other = desk.request(**{**ARGS, "shopper": "S0031", "order_id": "O900003"})
    assert (first["result"], other["result"]) == ("recorded", "recorded")
    assert (first["order_id"], other["order_id"]) == ("O000800", "O900003")
    assert len(desk.open) == 2
    # The second shopper cannot replay the first one's key to read the first's order.
    with pytest.raises(ToolError) as caught:
        desk.request(**{**ARGS, "shopper": "S0031", "key": "another-key-1"})
    assert caught.value.code == "not_found"


def test_a_key_used_in_one_turn_is_free_for_another_line_in_the_next_turn() -> None:
    book = OrderBook.from_data()
    add_order(book, "O900004", "S0188")
    desk = ReturnDesk(book, POLICY)
    assert ask(desk)["result"] == "recorded"
    again = ask(desk, turn=OTHER_TURN, order_id="O900004")
    assert again["result"] == "recorded" and again["order_id"] == "O900004"
    assert len(desk.open) == 2


def test_a_retry_with_the_same_key_after_a_recorded_one_returns_the_stored_result_unchanged() -> (
    None
):
    desk = ReturnDesk(OrderBook.from_data(), POLICY)
    first = ask(desk)
    # The caller's dict is its own: changing it does not change the next answer.
    first["reason"] = "tampered"
    first["result"] = "tampered"
    again = ask(desk)
    assert again["reason"] == "within_window" and again["result"] == "already_requested"


def test_a_refused_request_leaves_its_key_free_for_a_later_good_one(desk: ReturnDesk) -> None:
    assert code_of(desk, condition="wear_and_tear") == "wear_and_tear"
    assert ask(desk)["result"] == "recorded"


def test_a_conflict_changes_nothing_and_keeps_the_first_result(desk: ReturnDesk) -> None:
    first = ask(desk)
    assert code_of(desk, condition="sealed") == "idempotency_conflict"
    assert ask(desk) == {**first, "result": "already_requested"}
    assert len(desk.open) == 1


# ---- eviction at MAX_KEYS ----------------------------------------------------------------------


def test_evicting_old_keys_never_lets_a_line_have_a_second_request() -> None:
    book = OrderBook.from_data()
    ids = [f"O9{n:05d}" for n in range(10, 10 + MAX_KEYS + 5)]
    for order_id in ids:
        add_order(book, order_id, "S0188")
    desk = ReturnDesk(book, POLICY)
    for n, order_id in enumerate(ids):
        made = ask(desk, order_id=order_id, key=f"evict-key-{n:04d}")
        assert made["result"] == "recorded"
    assert len(desk.open) == len(ids) == MAX_KEYS + 5
    assert len(desk._keys["S0188"]) == MAX_KEYS
    # The first key is gone, but its line still has its one request, and nothing new is made.
    again = ask(desk, order_id=ids[0], key="evict-key-0000")
    assert again["result"] == "already_requested" and again["order_id"] == ids[0]
    assert len(desk.open) == len(ids)
    # The newest key is still remembered, so a changed argument under it is a conflict.
    assert code_of(
        desk, order_id=ids[-1], key=f"evict-key-{len(ids) - 1:04d}", condition="sealed"
    ) == ("idempotency_conflict")


def test_one_shoppers_keys_do_not_evict_anothers() -> None:
    book = OrderBook.from_data()
    ids = [f"O9{n:05d}" for n in range(100, 100 + MAX_KEYS + 2)]
    for order_id in ids:
        add_order(book, order_id, "S0031")
    add_order(book, "O900099", "S0188")
    desk = ReturnDesk(book, POLICY)
    ask(desk, order_id="O900099")
    for n, order_id in enumerate(ids):
        ask(desk, shopper="S0031", order_id=order_id, key=f"other-key-{n:04d}")
    assert code_of(desk, order_id="O900099", condition="sealed") == "idempotency_conflict"


# ---- concurrency -------------------------------------------------------------------------------


def run_threads(targets: list[Any]) -> None:
    threads = [threading.Thread(target=t) for t in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def test_concurrent_calls_with_one_key_and_one_set_of_arguments_record_once(
    desk: ReturnDesk,
) -> None:
    results: list[str] = []
    run_threads([lambda: results.append(ask(desk)["result"]) for _ in range(24)])
    assert results.count("recorded") == 1 and results.count("already_requested") == 23
    assert len(desk.open) == 1


def test_concurrent_calls_with_one_key_and_different_conditions_record_one_line_once(
    desk: ReturnDesk,
) -> None:
    outcomes: list[str] = []

    def go(condition: str) -> None:
        try:
            outcomes.append(ask(desk, condition=condition)["result"])
        except ToolError as error:
            outcomes.append(error.code)

    run_threads(
        [lambda c=c: go(c) for c in ("unused", "sealed", "opened_unused", "not_stated") * 4]
    )
    assert outcomes.count("recorded") == 1
    assert set(outcomes) <= {"recorded", "already_requested", "idempotency_conflict"}
    assert len(desk.open) == 1


def test_concurrent_calls_for_two_shoppers_with_one_key_each_record_their_own() -> None:
    book = OrderBook.from_data()
    add_order(book, "O900005", "S0031")
    desk = ReturnDesk(book, POLICY)
    results: list[tuple[str, str]] = []

    def go(shopper: str, order_id: str) -> None:
        made = desk.request(**{**ARGS, "shopper": shopper, "order_id": order_id})
        results.append((shopper, made["result"]))

    run_threads(
        [lambda: go("S0188", "O000800") for _ in range(8)]
        + [lambda: go("S0031", "O900005") for _ in range(8)]
    )
    assert results.count(("S0188", "recorded")) == 1
    assert results.count(("S0031", "recorded")) == 1
    assert len(desk.open) == 2


# ---- the scans move between the check and the request ------------------------------------------


def test_a_delivery_that_ages_out_between_the_check_and_the_request_is_refused(
    desk: ReturnDesk,
) -> None:
    args: dict[str, Any] = {
        "shopper": "S0188",
        "region": "US",
        "order_id": "O000800",
        "variant_id": VARIANT,
        "condition": "unused",
    }
    assert assess(desk.book, desk.policy, **args).decision.outcome == "eligible"
    number = desk.book.tracking_numbers["O000800"]
    events = desk.book.carrier.state.events
    old = (rp.AS_OF - timedelta(days=rp.WINDOW_DAYS["US"] + 1)).isoformat()
    events[number] = tuple(
        replace(e, on=old) if e.status == "delivered" else e for e in events[number]
    )
    assert code_of(desk) == "out_of_window"
    assert desk.open == {}


def test_a_scan_after_delivery_makes_the_order_not_delivered_for_the_request(
    desk: ReturnDesk,
) -> None:
    """A later non-delivered scan means the shopper status is no longer `delivered`."""
    desk.book.add_scan("O000800", "2026-11-01", "in_transit")
    assert code_of(desk) == "not_delivered"
    assert desk.open == {}


def test_a_recorded_request_stays_after_the_scans_change_and_is_not_redecided(
    desk: ReturnDesk,
) -> None:
    first = ask(desk)
    desk.book.add_scan("O000800", "2026-11-01", "in_transit")
    again = ask(desk, key="a-fresh-key-1")
    assert again == {**first, "result": "already_requested"}


def test_an_order_with_the_item_on_two_lines_is_referred_to_a_person_not_refused() -> None:
    book = OrderBook.from_data()
    orders = book.orders.state.orders
    line = orders["O000800"].lines[0]
    orders["O000800"] = replace(orders["O000800"], lines=(line, line))
    made = ReturnDesk(book, POLICY).request(**ARGS)
    assert (made["request_status"], made["reason"]) == ("needs_review", "more_than_one_line")
