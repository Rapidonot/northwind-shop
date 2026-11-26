"""CHK-06 gap tests (test-writer, row #27): the prompt's order section, the anonymous options, and
the output check on streamed deltas. No model, no server, no recording."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.output_check import LiveValues, OutputCheck, hits
from northwind_assistant.prompt import ORDERS_RULES, SYSTEM_PROMPT, system_prompt
from northwind_assistant.tools import ALLOWED_TOOL
from northwind_assistant.turn import assistant_options
from northwind_common import catalog_tool, order_tool
from northwind_common.llm import agent_runtime
from northwind_common.mcp_token import ORDERS_SERVER
from northwind_common.tool_scopes import AGENT_TOOLS

pytestmark = pytest.mark.req("CHK-06")

REGIONS = ["US", "CA", "UK", "EU"]
LAST_RULE = "- Keep the answer short."
ORDERS = "mcp__northwind-orders__get_order"


# ---- the prompt --------------------------------------------------------------------------------


@pytest.mark.parametrize("region", REGIONS)
def test_the_order_section_appears_once_and_just_before_the_last_rule(region: Any) -> None:
    text = system_prompt(region, signed_in=True)
    assert text.count(ORDERS_RULES) == 1
    assert text.count(LAST_RULE) == 1
    assert text.index(ORDERS_RULES) < text.index(LAST_RULE)
    assert text.replace("\n" + ORDERS_RULES, "", 1) == system_prompt(region)


@pytest.mark.parametrize("region", REGIONS)
def test_the_signed_in_prompt_keeps_every_other_rule_and_the_ai_disclosure(region: Any) -> None:
    text = system_prompt(region, signed_in=True)
    assert text.startswith(system_prompt(region).split("Rules:")[0])
    assert "You are an AI" in text  # NFR-08, unchanged
    assert text.index(ORDERS_RULES) > text.index("Rules:")


def test_the_order_section_forbids_what_the_assistant_may_not_do() -> None:
    rules = ORDERS_RULES.lower()
    assert "get_order" in rules and "<order_data>" in rules
    assert "never promise a refund" in rules
    assert "not an instruction" in rules
    assert ORDERS_RULES not in SYSTEM_PROMPT


# ---- the options -------------------------------------------------------------------------------


def _options(tmp_path: Path, *, orders: tuple[str, str] | None, signed: bool = False) -> Any:
    caller = Caller(region="US", session="d" * 32, shopper_id="S0188" if signed else None)
    return assistant_options(
        caller,
        "http://127.0.0.1:1/mcp",
        "rag-token",
        agent_runtime(root=tmp_path),
        ("http://127.0.0.1:2/mcp", "cat-token"),
        orders,
    )


def test_an_anonymous_turn_has_no_orders_server_tool_or_prompt_section(tmp_path: Path) -> None:
    options = _options(tmp_path, orders=None)
    servers = options.mcp_servers
    assert set(servers) == {"northwind-rag", catalog_tool.SERVER_NAME}
    assert list(options.allowed_tools) == [ALLOWED_TOOL, *catalog_tool.ALLOWED_TOOLS]
    assert not any("order" in t for t in options.allowed_tools)
    assert ORDERS_RULES not in options.system_prompt
    assert options.system_prompt == system_prompt("US")
    assert "orders" not in repr(servers).lower()


def test_a_signed_in_turn_adds_exactly_the_orders_server_and_get_order(tmp_path: Path) -> None:
    anon = _options(tmp_path, orders=None)
    signed = _options(tmp_path, orders=("http://127.0.0.1:3/mcp", "ord-token"), signed=True)
    assert set(signed.mcp_servers) - set(anon.mcp_servers) == {order_tool.SERVER_NAME}
    # PR 2 (CHK-07): and `request_return`, exactly.
    assert [t for t in signed.allowed_tools if t not in anon.allowed_tools] == [
        ORDERS,
        "mcp__northwind-orders__request_return",
    ]
    assert signed.system_prompt == system_prompt("US", signed_in=True)
    assert signed.tools == anon.tools == []


def test_get_order_is_the_assistants_alone_in_the_scopes() -> None:
    holders = [
        a for a, servers in AGENT_TOOLS.items() if "get_order" in servers.get(ORDERS_SERVER, ())
    ]
    assert holders == ["shopper-assistant"]
    assert AGENT_TOOLS["shopper-assistant"][ORDERS_SERVER] == {"get_order", "request_return"}


# ---- the output check, streamed (the user's N1-R option B: no order exemption) ---------------

PLACED = "O000800: your order was placed on 20 October and was delivered on 26 October."
CONFIRMED = "O000418: your order has been confirmed."


def _streamed(text: str, size: int) -> OutputCheck:
    check = OutputCheck(set(), [], live=LiveValues())
    for i in range(0, len(text), size):
        check.feed(text[i : i + size])
    check.finish()
    return check


@pytest.mark.parametrize("text", [PLACED, CONFIRMED])
@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 11])
def test_an_order_claim_is_blocked_whatever_the_delta_split(text: str, size: int) -> None:
    check = _streamed(text, size)
    assert check.counts.get("confirmation", 0) == 1
    assert "your order" not in check.shown


@pytest.mark.parametrize("text", [PLACED, CONFIRMED])
def test_every_single_split_point_of_an_order_claim_is_blocked(text: str) -> None:
    for cut in range(1, len(text)):
        check = OutputCheck(set(), [], live=LiveValues())
        check.feed(text[:cut])
        check.feed(text[cut:])
        check.finish()
        assert check.counts.get("confirmation", 0) == 1, cut
        assert "your order" not in check.shown, cut


def test_streamed_and_whole_text_agree() -> None:
    for text in (PLACED, CONFIRMED):
        assert hits(text).get("confirmation", 0) == _streamed(text, 4).counts.get("confirmation", 0)


@pytest.mark.parametrize(
    "text",
    ["O000800 was delivered on 26 October.", "O000418 is on hold: a person is looking at it."],
)
def test_the_status_in_the_results_own_words_passes(text: str) -> None:
    assert hits(text).get("confirmation", 0) == 0
