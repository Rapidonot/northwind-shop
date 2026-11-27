"""CHK-01 acceptance criteria, written from the PRD and the plan's design rules (row #16).

Criterion (PRD): "Cart tools: add, remove, change quantity, with live stock check. Out-of-stock add
is refused with alternatives offered." Cart tools must not be able to place an order.

These tests add to `test_cart.py` and cover what it does not: stock that is per region, the stock
boundary, quantities that accumulate, money that adds up, the same refusal on a quantity increase,
a refused write that leaves the cart as it was, concurrent writes against one stock reading, the
registry and scopes (no tool that can place an order), and the session claim in the token.

Idempotency (hard rule 9, as the user restated it): each cart write requires an `idempotency_key`
of 8-64 characters from [A-Za-z0-9_-]; get_cart takes none; there is no dry_run. Writes below get a
fixed, counted key from `calls` unless a test passes its own.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Iterator
from typing import Any

import pytest

from northwind_common import tool_scopes
from northwind_common.catalog_tool import BLOCK_SEPARATOR, label, labelled_result, unlabel
from northwind_common.errors import ENVELOPE_KEYS
from northwind_common.mcp_token import (
    CATALOG_SERVER,
    OPTIONAL_CLAIMS,
    TokenError,
    mint,
    verify_for,
)
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.testing import KEY, connect, running, token
from northwind_mcp_catalog.tools import TOOLS

pytestmark = pytest.mark.req("CHK-01")

V1 = "P0001-BLK-REG-M"
V2 = "P0001-BLK-REG-S"
CART_TOOLS = ("get_cart", "add_to_cart", "remove_from_cart", "set_cart_quantity")
SESSION_A = "a1" * 16  # fixed, never random (a random digit run can look like card data)
SESSION_B = "b2" * 16
SESSION_C = "c3" * 16
SESSION_D = "d4" * 16
Outcome = tuple[bool, list[str]]
WRITES = ("add_to_cart", "remove_from_cart", "set_cart_quantity")
_counter = itertools.count(1)


def next_key() -> str:
    return f"chk01-key-{next(_counter):06d}"  # fixed pattern, never random


@pytest.fixture(scope="module")
def store() -> FixtureCatalog:
    return FixtureCatalog()


@pytest.fixture(scope="module")
def url(store: FixtureCatalog) -> Iterator[str]:
    with running(CatalogServer(store, key=KEY, tools=TOOLS)) as live_url:
        yield live_url


def calls(
    url: str,
    session: str | None,
    steps: list[tuple[str, dict[str, Any]]],
    region: str = "UK",
    agent: str = "shopper-assistant",
) -> list[Outcome]:
    bearer = token(agent, region, session=session)[0]

    async def go() -> list[Outcome]:
        out: list[Outcome] = []
        async with connect(url, bearer) as client:
            for name, args in steps:
                if name in WRITES and "idempotency_key" not in args:
                    args = {**args, "idempotency_key": next_key()}
                result = await client.call_tool(name, args)
                out.append(
                    (bool(result.is_error), [getattr(b, "text", "") for b in result.content])
                )
        return out

    return asyncio.run(go())


def call(url: str, session: str | None, name: str, args: dict[str, Any], **kw: Any) -> Outcome:
    return calls(url, session, [(name, args)], **kw)[0]


def ok(outcome: Outcome, tool: str) -> dict[str, Any]:
    is_error, blocks = outcome
    assert not is_error, blocks
    [text] = blocks
    labelled = unlabel(text)
    assert labelled is not None and labelled[0] == tool, text
    return dict(labelled[1])


def refused(outcome: Outcome) -> tuple[dict[str, Any], dict[str, Any]]:
    """The envelope and the alternatives block of a stock refusal."""
    is_error, blocks = outcome
    assert is_error and len(blocks) == 2, blocks
    envelope = json.loads(blocks[0])
    assert set(envelope) == ENVELOPE_KEYS
    labelled = unlabel(blocks[1])
    assert labelled is not None
    return dict(envelope), dict(labelled[1])


def cart_of(url: str, session: str, region: str = "UK") -> dict[str, Any]:
    return ok(call(url, session, "get_cart", {}, region=region), "get_cart")


def minor(amount: str) -> int:
    whole, _, cents = amount.partition(".")
    return int(whole) * 100 + int(cents)


# ---- the criterion: an out-of-stock add is refused, with alternatives offered ----


def test_the_criterion_out_of_stock_add_is_refused_and_alternatives_are_offered(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 0)
    envelope, body = refused(call(url, SESSION_A, "add_to_cart", {"variant_id": V1}))
    assert (envelope["errorCategory"], envelope["code"]) == ("business", "out_of_stock")
    assert envelope["isError"] is True and envelope["isRetryable"] is False
    assert body["alternatives"], "no alternative was offered"
    assert V1 not in {a["variant_id"] for a in body["alternatives"]}
    assert cart_of(url, SESSION_A)["lines"] == []


def test_stock_is_per_region_so_the_same_variant_is_refused_in_one_and_added_in_another(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 0)
    store.set_stock(V1, "US", 4)
    uk, us = (
        calls(url, SESSION_A, [("add_to_cart", {"variant_id": V1})], region="UK"),
        calls(url, SESSION_A, [("add_to_cart", {"variant_id": V1})], region="US"),
    )
    assert refused(uk[0])[0]["code"] == "out_of_stock"
    assert ok(us[0], "add_to_cart")["result"] == "added"


def test_a_quantity_increase_to_out_of_stock_offers_the_same_kind_of_alternatives(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 2)
    call(url, SESSION_B, "add_to_cart", {"variant_id": V1, "quantity": 2})
    store.set_stock(V1, "UK", 0)
    envelope, body = refused(
        call(url, SESSION_B, "set_cart_quantity", {"variant_id": V1, "quantity": 3})
    )
    assert envelope["code"] == "out_of_stock"
    assert body["requested"]["variant_id"] == V1
    assert 1 <= len(body["alternatives"]) <= 5
    assert all(a["in_stock"] is True and a["on_hand"] >= 1 for a in body["alternatives"])
    assert [ln["quantity"] for ln in cart_of(url, SESSION_B)["lines"]] == [2]


def test_partial_stock_on_a_quantity_increase_is_insufficient_not_out_of_stock(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 3)
    call(url, SESSION_C, "add_to_cart", {"variant_id": V1})
    envelope, body = refused(
        call(url, SESSION_C, "set_cart_quantity", {"variant_id": V1, "quantity": 5})
    )
    assert envelope["code"] == "insufficient_stock"
    assert body["requested"]["on_hand"] == 3
    assert [ln["quantity"] for ln in cart_of(url, SESSION_C)["lines"]] == [1]


# ---- the live stock check: boundaries ----


def test_asking_for_exactly_what_is_on_hand_succeeds_and_one_more_does_not(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V2, "UK", 4)
    exact = call(url, SESSION_A, "add_to_cart", {"variant_id": V2, "quantity": 4})
    assert ok(exact, "add_to_cart")["quantity"] == 4
    more = call(url, SESSION_B, "add_to_cart", {"variant_id": V2, "quantity": 5})
    assert refused(more)[0]["code"] == "insufficient_stock"


def test_a_refused_add_leaves_the_line_already_in_the_cart_as_it_was(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 3)
    call(url, SESSION_D, "add_to_cart", {"variant_id": V1, "quantity": 2})
    refusal = call(url, SESSION_D, "add_to_cart", {"variant_id": V1, "quantity": 2})
    assert refused(refusal)[0]["code"] == "insufficient_stock"
    [line] = cart_of(url, SESSION_D)["lines"]
    assert (line["variant_id"], line["quantity"]) == (V1, 2)


def test_adds_of_the_same_variant_accumulate_in_one_line(url: str, store: FixtureCatalog) -> None:
    store.set_stock(V1, "UK", 9)
    session = "e5" * 16
    call(url, session, "add_to_cart", {"variant_id": V1, "quantity": 1})
    added = ok(call(url, session, "add_to_cart", {"variant_id": V1, "quantity": 2}), "add_to_cart")
    [line] = added["cart"]["lines"]
    assert line["quantity"] == 3 and added["cart"]["item_count"] == 3


def test_concurrent_adds_against_one_stock_reading_never_oversell_one_cart(
    url: str, store: FixtureCatalog
) -> None:
    """Stock 3, five adds of 2 to one cart at once: the stock check and the write are one step, so
    exactly one succeeds and the line never holds more than the stock."""
    store.set_stock(V1, "UK", 3)
    session = "f6" * 16
    bearer = token("shopper-assistant", "UK", session=session)[0]

    async def one() -> bool:
        async with connect(url, bearer) as client:
            result = await client.call_tool(
                "add_to_cart",
                {"variant_id": V1, "quantity": 2, "idempotency_key": next_key()},
            )
            return not result.is_error

    async def go() -> list[bool]:
        return list(await asyncio.gather(*(one() for _ in range(5))))

    results = asyncio.run(go())
    assert results.count(True) == 1
    [line] = cart_of(url, session)["lines"]
    assert line["quantity"] == 2 <= line["on_hand"]


# ---- money: exact, and the sum of what is shown ----


def test_the_subtotal_is_the_exact_sum_of_the_line_totals_and_items_are_counted(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 9)
    store.set_stock(V2, "UK", 9)
    store.set_price(V1, "UK", "19.99", "GBP")
    store.set_price(V2, "UK", "0.10", "GBP")
    session = "a7" * 16
    call(url, session, "add_to_cart", {"variant_id": V1, "quantity": 3})
    call(url, session, "add_to_cart", {"variant_id": V2, "quantity": 7})
    cart = cart_of(url, session)
    by_variant = {ln["variant_id"]: ln for ln in cart["lines"]}
    assert by_variant[V1]["line_total"] == "59.97"
    assert by_variant[V2]["line_total"] == "0.70"
    assert cart["subtotal"] == "60.67"
    assert sum(minor(ln["line_total"]) for ln in cart["lines"]) == minor(cart["subtotal"])
    assert cart["item_count"] == 10 and cart["currency"] == "GBP"


def test_removing_a_line_updates_the_subtotal_from_the_remaining_lines(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 9)
    store.set_stock(V2, "UK", 9)
    store.set_price(V1, "UK", "20.00", "GBP")
    store.set_price(V2, "UK", "5.50", "GBP")
    session = "b8" * 16
    call(url, session, "add_to_cart", {"variant_id": V1, "quantity": 1})
    call(url, session, "add_to_cart", {"variant_id": V2, "quantity": 2})
    removed = ok(call(url, session, "remove_from_cart", {"variant_id": V1}), "remove_from_cart")
    assert [ln["variant_id"] for ln in removed["cart"]["lines"]] == [V2]
    assert removed["cart"]["subtotal"] == "11.00" and removed["cart"]["item_count"] == 2


def test_the_cart_stores_no_price_so_a_price_change_reaches_every_tool_result(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 9)
    store.set_price(V1, "UK", "30.00", "GBP")
    session = "c9" * 16
    call(url, session, "add_to_cart", {"variant_id": V1, "quantity": 2})
    store.set_price(V1, "UK", "31.50", "GBP")
    after_set = ok(
        call(url, session, "set_cart_quantity", {"variant_id": V1, "quantity": 3}),
        "set_cart_quantity",
    )
    assert after_set["cart"]["subtotal"] == "94.50"


def test_a_cart_line_reports_stock_that_falls_to_zero_after_the_add(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 2)
    session = "d1" * 16
    call(url, session, "add_to_cart", {"variant_id": V1, "quantity": 1})
    store.set_stock(V1, "UK", 0)
    [line] = cart_of(url, session)["lines"]
    assert (line["on_hand"], line["in_stock"], line["quantity"]) == (0, False, 1)


# ---- refusals reveal and echo nothing ----


def test_the_refusal_text_names_no_variant_and_the_two_refusals_share_one_message(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 0)
    envelope, _ = refused(call(url, SESSION_A, "add_to_cart", {"variant_id": V1}))
    assert V1 not in envelope["message"]
    store.set_stock(V2, "UK", 0)
    other, _ = refused(call(url, SESSION_B, "add_to_cart", {"variant_id": V2}))
    assert other["message"] == envelope["message"]


def test_a_refusal_is_the_same_for_a_cart_with_other_lines_in_it(
    url: str, store: FixtureCatalog
) -> None:
    """Alternatives depend on the store and the request, not on who is asking."""
    store.set_stock(V1, "UK", 0)
    store.set_stock(V2, "UK", 5)
    call(url, SESSION_C, "add_to_cart", {"variant_id": V2})
    with_line = call(url, SESSION_C, "add_to_cart", {"variant_id": V1})
    empty = call(url, "e2" * 16, "add_to_cart", {"variant_id": V1})
    assert with_line == empty


# ---- the cart tools cannot place an order ----

ORDER_WORDS = ("order", "checkout", "pay", "purchase", "refund", "capture", "assign", "buy")


def test_the_registry_holds_the_catalog_reads_and_the_four_cart_tools_and_nothing_else() -> None:
    # Row #11 (approved under stop 5): plus the server-only live filter, read-only. SA-01 PR 1
    # (approved by the user under stop 5, 2026-11-26): plus the portal's two reads, by name.
    assert set(TOOLS) == set(tool_scopes.CATALOG_READ_TOOLS) | set(CART_TOOLS) | {
        tool_scopes.ELIGIBLE_PRODUCTS,
        "browse_products",
        "get_prices",
        "list_reviews",  # SA-01 PR 2 (Amendment 3, approved by the user 2026-11-27)
    }
    assert tool_scopes.CART_TOOLS == frozenset(CART_TOOLS)
    for name in TOOLS:
        assert not any(word in name for word in ORDER_WORDS), name


# Row #27 (CHK-06, decision 0023): the one exception, exactly: the assistant's `get_order` on
# northwind-orders, a read of the signed-in shopper's own order that cannot place one (read-only, no
# write tool on that server). Flagged to the user under stop 5 with the row #27 report.
READ_ONLY_ORDER_TOOL = ("shopper-assistant", "northwind-orders", "get_order")


def test_no_agent_is_scoped_to_a_tool_that_could_place_an_order() -> None:
    for agent, servers in tool_scopes.AGENT_TOOLS.items():
        for server, names in servers.items():
            for name in names:
                if (agent, server, name) == READ_ONLY_ORDER_TOOL:
                    continue
                assert not any(word in name for word in ORDER_WORDS), (agent, name)


def test_the_one_order_read_is_read_only_and_the_orders_server_has_one_request_more() -> None:
    from northwind_mcp_orders.tools import TOOLS as ORDERS_TOOLS

    # Row #27 PR 2 (CHK-07, decision 0024): plus the shopper's own return request, exactly. It
    # places no order.
    assert set(ORDERS_TOOLS) == {"get_order", "request_return"} == set(tool_scopes.ORDER_TOOLS)
    made = ORDERS_TOOLS["request_return"].tool.annotations
    assert made is not None and made.read_only_hint is False and made.destructive_hint is False
    annotations = ORDERS_TOOLS["get_order"].tool.annotations
    assert annotations is not None and annotations.read_only_hint is True
    assert annotations.destructive_hint in (None, False)
    holders = {
        agent
        for agent, servers in tool_scopes.AGENT_TOOLS.items()
        if servers.get("northwind-orders")
    }
    assert holders == {"shopper-assistant"}


def test_each_agent_gets_exactly_its_cart_tools() -> None:
    catalog = tool_scopes.AGENT_TOOLS
    assert tool_scopes.CART_TOOLS <= catalog["shopper-assistant"]["northwind-catalog"]
    # SA-01 PR 1: the portal also reads the catalog for the shop pages; still no cart write.
    assert catalog["shopper-portal"]["northwind-catalog"] == frozenset(
        {"get_cart", "get_product", "browse_products", "get_prices", "list_reviews"}
    )
    assert not tool_scopes.CART_WRITE_TOOLS & catalog["shopper-portal"]["northwind-catalog"]
    assert not tool_scopes.CART_TOOLS & catalog["catalog-subagent"]["northwind-catalog"]
    assert catalog["shopper-portal"]["northwind-rag"] == frozenset()


def test_each_agent_lists_only_its_own_tools_on_a_real_server(url: str) -> None:
    async def names(agent: str, session: str | None) -> set[str]:
        async with connect(
            url, token(agent, None if agent == "catalog-subagent" else "UK", session=session)[0]
        ) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assistant = asyncio.run(names("shopper-assistant", SESSION_A))
    portal = asyncio.run(names("shopper-portal", SESSION_A))
    staff = asyncio.run(names("catalog-subagent", None))
    assert set(CART_TOOLS) <= assistant
    assert portal == {  # SA-01 PR 1; PR 2 adds `list_reviews`
        "get_cart",
        "get_product",
        "browse_products",
        "get_prices",
        "list_reviews",
    }
    assert not set(CART_TOOLS) & staff


def test_no_cart_result_carries_an_order_id_or_state(url: str, store: FixtureCatalog) -> None:
    store.set_stock(V1, "UK", 5)
    session = "a3" * 16
    steps: list[tuple[str, dict[str, Any]]] = [
        ("add_to_cart", {"variant_id": V1, "quantity": 2}),
        ("set_cart_quantity", {"variant_id": V1, "quantity": 3}),
        ("get_cart", {}),
        ("remove_from_cart", {"variant_id": V1}),
    ]

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {k for v in value.values() for k in keys(v)}
        if isinstance(value, list):
            return {k for v in value for k in keys(v)}
        return set()

    for tool, (_, blocks) in zip((s[0] for s in steps), calls(url, session, steps), strict=True):
        labelled = unlabel(blocks[0])
        assert labelled is not None and labelled[0] == tool
        found = keys(labelled[1])
        assert not {k for k in found if any(w in k for w in ("order", "payment", "status"))}


# ---- the shared reader ----


def test_the_reader_takes_a_success_only_as_exactly_one_label() -> None:
    one = label("get_cart", {"lines": []})
    assert labelled_result(one, False) == ("get_cart", {"lines": []})
    assert labelled_result(one + BLOCK_SEPARATOR + one, False) is None


def test_a_refusal_code_not_on_the_list_never_yields_a_label() -> None:
    envelope = {
        "isError": True,
        "errorCategory": "business",
        "isRetryable": False,
        "message": "x",
        "code": "not_in_cart",
    }
    text = json.dumps(envelope) + BLOCK_SEPARATOR + label("get_cart", {"lines": []})
    assert labelled_result(text, True) is None


# ---- the session claim ----


def minted(session: str | None, **kw: Any) -> tuple[str, Any]:
    return mint(
        audience=kw.pop("audience", "shopper"),
        region=kw.pop("region", "UK"),
        agent=kw.pop("agent", "shopper-assistant"),
        key=KEY,
        server=CATALOG_SERVER,
        session=session,
        **kw,
    )


def test_a_token_carries_the_session_it_was_minted_with_and_verifies_to_it() -> None:
    bearer, claims = minted(SESSION_A)
    assert claims.sid == SESSION_A
    assert verify_for(bearer, key=KEY, server=CATALOG_SERVER).sid == SESSION_A


def test_a_token_minted_without_a_session_has_no_sid_claim() -> None:
    bearer, claims = minted(None)
    assert claims.sid is None
    assert verify_for(bearer, key=KEY, server=CATALOG_SERVER).sid is None
    # Row #11 (RAG-04) adds `qf`, a query plan for northwind-rag only (decision 0022).
    # Row #27 (CHK-06) adds `shp`, the signed-in shopper, on a northwind-orders token only.
    assert OPTIONAL_CLAIMS == frozenset({"sid", "qf", "shp"})


@pytest.mark.parametrize(
    "session",
    ["A" * 32, "a" * 31, "a" * 33, "g" * 32, "", "a" * 32 + "\n", 12345, b"a" * 32],
)
def test_a_session_that_is_not_32_lowercase_hex_is_refused_at_minting(session: Any) -> None:
    with pytest.raises(ValueError):
        minted(session)


def test_a_staff_token_cannot_carry_a_session() -> None:
    with pytest.raises(ValueError):
        minted(SESSION_A, audience="staff", region=None, agent="catalog-subagent")


def test_a_token_with_a_changed_session_fails_verification() -> None:
    bearer, _ = minted(SESSION_A)
    version, payload, signature = bearer.split(".")
    import base64

    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    forged = raw.replace(SESSION_A, SESSION_B)
    assert forged != raw
    swapped = base64.urlsafe_b64encode(forged.encode()).rstrip(b"=").decode()
    with pytest.raises(TokenError):
        verify_for(f"{version}.{swapped}.{signature}", key=KEY, server=CATALOG_SERVER)


def test_a_token_signed_with_another_key_is_refused_by_the_server_even_with_a_session(
    url: str,
) -> None:
    other = mint(
        audience="shopper",
        region="UK",
        agent="shopper-assistant",
        key=b"z" * 40,
        server=CATALOG_SERVER,
        session=SESSION_A,
    )[0]

    async def go() -> bool:
        try:
            async with connect(url, other) as client:
                result = await client.call_tool("get_cart", {})
                return bool(result.is_error)
        except Exception:  # noqa: BLE001 - a refused connection is a refusal
            return True

    assert asyncio.run(go())


# ---- idempotency (hard rule 9) ----

KEY_ARGS: dict[str, dict[str, Any]] = {
    "add_to_cart": {"variant_id": V1, "quantity": 1},
    "remove_from_cart": {"variant_id": V1},
    "set_cart_quantity": {"variant_id": V1, "quantity": 2},
}


def error_of(outcome: Outcome) -> dict[str, Any]:
    is_error, blocks = outcome
    assert is_error, blocks
    return dict(json.loads(blocks[0]))


@pytest.mark.parametrize("name", WRITES)
def test_each_cart_write_declares_a_required_idempotency_key_and_no_dry_run(name: str) -> None:
    schema = TOOLS[name].tool.input_schema
    assert "idempotency_key" in schema["properties"]
    assert "idempotency_key" in schema["required"]
    assert "dry_run" not in schema["properties"]
    assert schema["additionalProperties"] is False


def test_get_cart_takes_no_idempotency_key_and_no_dry_run() -> None:
    props = set(TOOLS["get_cart"].tool.input_schema.get("properties", {}))
    assert not {"idempotency_key", "dry_run"} & props


@pytest.mark.parametrize("name", WRITES)
@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "short7!",
        "a" * 7,
        "a" * 65,
        "has space 123",
        "semi;colon1",
        "naïve-key-1",
        12345678,
        True,
        ["abcdefgh"],
    ],
)
def test_a_missing_or_malformed_key_is_refused_and_changes_nothing(
    url: str, store: FixtureCatalog, name: str, bad: Any
) -> None:
    store.set_stock(V1, "UK", 5)
    session = "a9" * 16
    args = {**KEY_ARGS[name]}
    if bad is not None:
        args["idempotency_key"] = bad
    outcome = calls(url, session, [(name, args)])[0] if bad is not None else None
    if outcome is None:  # a missing key: send without the helper's default
        bearer = token("shopper-assistant", "UK", session=session)[0]

        async def go() -> Outcome:
            async with connect(url, bearer) as client:
                result = await client.call_tool(name, args)
                return bool(result.is_error), [getattr(b, "text", "") for b in result.content]

        outcome = asyncio.run(go())
    envelope = error_of(outcome)
    assert (envelope["errorCategory"], envelope["code"]) == (
        "validation",
        "invalid_idempotency_key",
    )
    assert envelope["isRetryable"] is False
    assert cart_of(url, session)["lines"] == []


@pytest.mark.parametrize("good", ["abcdefgh", "A" * 64, "a-b_C-d_9", "12345678"])
def test_a_key_of_8_to_64_allowed_characters_is_accepted(
    url: str, store: FixtureCatalog, good: str
) -> None:
    store.set_stock(V1, "UK", 5)
    outcome = call(
        url, "b9" * 16, "add_to_cart", {**KEY_ARGS["add_to_cart"], "idempotency_key": good}
    )
    assert ok(outcome, "add_to_cart")["result"] == "added"


def test_the_same_key_with_the_same_arguments_returns_the_stored_result_and_changes_once(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 20)
    session = "c7" * 16  # its own session: "c9" * 16 is another test's cart
    args = {**KEY_ARGS["add_to_cart"], "quantity": 2, "idempotency_key": "replay-key-0001"}
    outcomes = calls(url, session, [("add_to_cart", args)] * 10)
    first = ok(outcomes[0], "add_to_cart")
    for outcome in outcomes[1:]:
        again = ok(outcome, "add_to_cart")
        assert again["cart"]["lines"] == first["cart"]["lines"]
        assert again["cart"]["item_count"] == 2
    [line] = cart_of(url, session)["lines"]
    assert line["quantity"] == 2


def test_a_replayed_remove_returns_the_stored_result_not_not_in_cart(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 5)
    session = "d9" * 16
    call(url, session, "add_to_cart", KEY_ARGS["add_to_cart"])
    args = {"variant_id": V1, "idempotency_key": "remove-key-0001"}
    first, second = calls(url, session, [("remove_from_cart", args)] * 2)
    assert ok(first, "remove_from_cart")["result"] == "removed"
    # Security review F1, re-review N1: never read as a new change; the cart read live.
    replayed = ok(second, "remove_from_cart")
    assert replayed["result"] == "already_applied"
    assert replayed["original"] == {"result": "removed", "variant_id": V1}
    assert replayed["cart"] == ok(first, "remove_from_cart")["cart"]


def test_a_replayed_set_quantity_changes_the_line_once(url: str, store: FixtureCatalog) -> None:
    store.set_stock(V1, "UK", 20)
    session = "e9" * 16
    call(url, session, "add_to_cart", {**KEY_ARGS["add_to_cart"]})
    args = {"variant_id": V1, "quantity": 4, "idempotency_key": "set-key-00001"}
    first, second = calls(url, session, [("set_cart_quantity", args)] * 2)
    # Security review F1, re-review N1: never read as a new change; the cart read live.
    replayed = ok(second, "set_cart_quantity")
    assert replayed["result"] == "already_applied" and replayed["original"]["quantity"] == 4
    assert replayed["cart"] == ok(first, "set_cart_quantity")["cart"]
    [line] = cart_of(url, session)["lines"]
    assert line["quantity"] == 4


@pytest.mark.parametrize(
    "other",
    [{"quantity": 3}, {"variant_id": V2}],
)
def test_the_same_key_with_other_arguments_is_a_conflict_and_changes_nothing(
    url: str, store: FixtureCatalog, other: dict[str, Any]
) -> None:
    store.set_stock(V1, "UK", 20)
    store.set_stock(V2, "UK", 20)
    session = ("f9" if "quantity" in other else "a2") * 16  # each case its own cart
    key = "conflict-key-1"
    # One `calls` is one token, so one turn: keys are remembered per turn (the user's option A).
    _, outcome = calls(
        url,
        session,
        [
            ("add_to_cart", {"variant_id": V1, "quantity": 1, "idempotency_key": key}),
            ("add_to_cart", {"variant_id": V1, "quantity": 1, **other, "idempotency_key": key}),
        ],
    )
    envelope = error_of(outcome)
    assert (envelope["errorCategory"], envelope["code"]) == ("validation", "idempotency_conflict")
    [line] = cart_of(url, session)["lines"]
    assert (line["variant_id"], line["quantity"]) == (V1, 1)


def test_the_same_key_on_another_tool_is_a_conflict(url: str, store: FixtureCatalog) -> None:
    store.set_stock(V1, "UK", 20)
    session = "a8" * 16
    key = "shared-key-001"
    _, outcome = calls(  # one turn (keys are remembered per turn)
        url,
        session,
        [
            ("add_to_cart", {"variant_id": V1, "quantity": 1, "idempotency_key": key}),
            ("remove_from_cart", {"variant_id": V1, "idempotency_key": key}),
        ],
    )
    assert error_of(outcome)["code"] == "idempotency_conflict"
    assert [ln["quantity"] for ln in cart_of(url, session)["lines"]] == [1]


def test_a_key_is_scoped_to_its_own_cart(url: str, store: FixtureCatalog) -> None:
    """The same key in another session, or the same session in another region, is a new key."""
    store.set_stock(V1, "UK", 5)
    store.set_stock(V1, "US", 5)
    key = "scoped-key-01"
    args = {**KEY_ARGS["add_to_cart"], "idempotency_key": key}
    call(url, "b8" * 16, "add_to_cart", args)
    other_session = call(url, "c8" * 16, "add_to_cart", {**args, "quantity": 3})
    other_region = call(url, "b8" * 16, "add_to_cart", {**args, "quantity": 3}, region="US")
    assert ok(other_session, "add_to_cart")["quantity"] == 3
    assert ok(other_region, "add_to_cart")["quantity"] == 3


def test_the_last_fifty_keys_are_kept_and_an_older_one_is_forgotten(
    url: str, store: FixtureCatalog
) -> None:
    store.set_stock(V1, "UK", 1000)
    session = "e8" * 16
    steps: list[tuple[str, dict[str, Any]]] = [
        (
            "set_cart_quantity" if i else "add_to_cart",
            {"variant_id": V1, "quantity": 1 + i % 2, "idempotency_key": f"bounded-key-{i:03d}"},
        )
        for i in range(51)
    ]
    # All in one turn: keys are remembered per (turn, key) (the user's option A).
    reused_args = {"variant_id": V1, "quantity": 7, "idempotency_key": "bounded-key-000"}
    recent = {"variant_id": V1, "quantity": 9, "idempotency_key": "bounded-key-050"}
    *_, reused, recent_outcome = calls(
        url, session, [*steps, ("set_cart_quantity", reused_args), ("set_cart_quantity", recent)]
    )
    # The 51st key evicted the first: its replay is no longer recognised, so a different
    # argument set under it is accepted as a new key rather than a conflict.
    assert not reused[0] or error_of(reused)["code"] != "idempotency_conflict"
    # A recent key is still remembered: other arguments under it conflict.
    assert error_of(recent_outcome)["code"] == "idempotency_conflict"
