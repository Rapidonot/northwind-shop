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


# ---- the user's D6 (F3): the same limit on every argument ---------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"variant_id": "P4111-111111", "idempotency_key": "k-111111-x"},  # F3
        {"order_id": "O000800", "variant_id": "P1111-111111"},  # F3
        {"variant_id": "P0003-1234567"},
    ],
)
def test_an_argument_with_more_than_six_digits_in_a_row_is_refused(
    live: tuple[OrdersServer, str], fields: dict[str, str]
) -> None:
    instance, url = live
    is_error, _text, body = call(url, **{**GOOD, **fields})
    assert is_error and body["code"] == "invalid_argument"
    assert body["errorCategory"] == "validation"
    assert "111111" not in str(body)
    assert instance.desk.open == {}


def test_an_order_number_with_too_many_digits_is_the_same_not_found_as_an_unknown_one(
    live: tuple[OrdersServer, str],
) -> None:
    """The lookalike pin (test_chk07_tw_tools.py) holds: no reply tells an order number apart."""
    unknown = call(live[1], **{**GOOD, "order_id": "O999999"})
    got = call(live[1], **{**GOOD, "order_id": "O4111111", "idempotency_key": "k-111111-x"})
    assert got[1] == unknown[1] and got[2]["code"] == "not_found"
    assert live[0].desk.open == {}


def test_every_real_order_line_passes_the_limit() -> None:
    """Measured at $0: in every variant ID of the fixtures and recordings the longest group is 4."""
    import re

    from northwind_mcp_orders.tools import MAX_KEY_DIGITS, too_many_digits

    ids = ["P0003-SLT-REL-M", "P0085-FOR-REG-M", "P0002-NAV-REL-4XL", "P0010-CHR-REG-XS"]
    assert all(not too_many_digits(v) for v in [*ids, "O000800", "O999999"])
    assert MAX_KEY_DIGITS == 6 and re.fullmatch(r"O\d{6}", "O000800")
