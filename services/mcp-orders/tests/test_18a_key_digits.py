"""Row 18a, the user's D2 (b), 2026-11-26 (hard rule 1, PAY-05; row #27 PR 2's security L4): a
card number split between a well-formed order number and an all-digit `idempotency_key`
(`O411111` + `1111111111`) passed every card check. A key with a digit group longer than 6 (single
separators joined, as `cards.py` joins them) is now refused as a validation error before anything
is recorded: with the 6 digits of an order number at most 12 join, which is never a card. Measured
at $0: none of the 1,612 idempotency keys in every recorded suite has such a group. Failing
first."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from northwind_mcp_orders import testing
from northwind_mcp_orders.server import OrdersServer

pytestmark = [pytest.mark.req("PAY-05"), pytest.mark.req("CHK-07")]

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


def call(url: str, **args: Any) -> Any:
    return asyncio.run(testing.call(url, testing.token()[0], "request_return", args))


def test_the_split_card_of_security_l4_is_refused_and_nothing_is_recorded(
    live: tuple[OrdersServer, str],
) -> None:
    instance, url = live
    is_error, _text, body = call(
        url, **{**GOOD, "order_id": "O411111", "idempotency_key": "1111111111"}
    )
    assert is_error and body["code"] == "invalid_idempotency_key"
    assert body["isError"] is True and body["errorCategory"] == "validation"
    assert "1111111111" not in str(body)
    assert instance.desk.open == {}


@pytest.mark.parametrize(
    "key", ["42424242", "4111-1111-11", "key_1234567", "1234567-abc", "abc_1_2_3_4_5_6_7"]
)
def test_a_key_with_more_than_six_digits_in_a_row_is_refused(
    live: tuple[OrdersServer, str], key: str
) -> None:
    instance, url = live
    is_error, _text, body = call(url, **{**GOOD, "idempotency_key": key})
    assert is_error and body["code"] == "invalid_idempotency_key"
    assert instance.desk.open == {}


@pytest.mark.parametrize(
    "key", ["return-key-1", "RetO000800dmg01", "ret800sealed01", "k123456-x123456", "a1b2c3d4e5"]
)
def test_a_key_with_six_digits_in_a_row_or_fewer_is_taken(
    live: tuple[OrdersServer, str], key: str
) -> None:
    is_error, _text, body = call(live[1], **{**GOOD, "idempotency_key": key})
    assert not is_error and body["result"] == "recorded"


def test_a_key_that_holds_a_whole_card_is_still_card_data_first(
    live: tuple[OrdersServer, str],
) -> None:
    body = call(live[1], **{**GOOD, "idempotency_key": "4242424242424242a"})[2]
    assert body["code"] == "card_data_refused"
