"""Row #11 (RAG-04): pins for the security review's fixes
(`docs/reviews/2026-11-24-11-security-review.raw.md`).

M1: the eval planner stops on any plan that did not come from a replayed reply. L1: the search cap
outlives both of a turn's tokens. L2/L3: the plan claim refuses card text in any item and a turn
with a trailing newline. L4: the eval scores the search the gateway would run (open bounds, token
size). L6/L7: the SDK's logger is quiet, and live mode makes no SDK retries.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant import rag_client
from northwind_assistant.understand import Understander, Understood
from northwind_common.mcp_token import RAG_SERVER, mint, plan_ok
from northwind_evals import query_plans
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_rag.ledger import SearchLedger
from northwind_rag.query import QueryPlan, rules_plan
from northwind_rag.retrieval import SearchFilter

pytestmark = pytest.mark.req("RAG-04")

MULTI = "A black fleece in M and a queen duvet under $200"


@pytest.mark.parametrize("path", ["error", "timeout", "skipped", "rules", "cache"])
def test_the_planner_stops_on_any_plan_not_from_a_replayed_reply(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def plan(self: Any, message: str, region: str, base_url: str) -> Understood:
        return Understood(rules_plan(message, region), path, 1)  # type: ignore[arg-type]

    monkeypatch.setattr(Understander, "plan", plan)
    with pytest.raises(SystemExit, match="did not answer every plan"):
        with query_plans.planner([(MULTI, "US")], cassettes=tmp_path):
            pass


@pytest.mark.parametrize("path", ["model", "invalid"])
def test_a_replayed_reply_is_accepted(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def plan(self: Any, message: str, region: str, base_url: str) -> Understood:
        return Understood(rules_plan(message, region), path, 1)  # type: ignore[arg-type]

    monkeypatch.setattr(Understander, "plan", plan)
    with query_plans.planner([(MULTI, "US")], cassettes=tmp_path) as parts:
        assert parts(MULTI, "US")


def test_the_cap_entry_keeps_the_later_expiry() -> None:
    now = [1000.0]
    ledger = SearchLedger(clock=lambda: now[0])
    turn = "t"

    async def take(exp: int) -> bool:
        return await ledger.take(turn, exp)

    assert asyncio.run(take(1100))  # the planned token, later
    assert asyncio.run(take(1050))  # the CLI's token
    assert asyncio.run(take(1050))
    now[0] = 1060.0  # past the CLI token's expiry, not the planned token's
    assert not asyncio.run(take(1100))  # still capped at 3


@pytest.mark.parametrize("key", ["c", "s"])
def test_card_text_in_a_colour_or_size_item_is_refused(key: str) -> None:
    assert not plan_ok({"parts": [{key: ["4111111111111111"]}]})


def test_a_turn_with_a_trailing_newline_is_refused() -> None:
    with pytest.raises(ValueError):
        mint(
            audience="shopper",
            region="US",
            agent="shopper-assistant",
            key=b"k" * 40,
            server=RAG_SERVER,
            turn=str(uuid.uuid4()) + "\n",
        )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Pants for around $0.01", None),  # both bounds open: no price filter
        ("A rug for around $10,000", (800000, 1000000)),  # the top bound is capped
    ],
)
def test_around_bounds_stay_in_range(message: str, expected: tuple[int, int] | None) -> None:
    price = rules_plan(message, "US").parts[0].filters.price
    assert (None if price is None else (price.min, price.max)) == expected


def test_a_plan_the_token_cannot_hold_is_scored_as_the_plain_search() -> None:
    """Three 200-character non-ASCII parts (JSON escapes each character as six) do not fit one
    token: the gateway's `mint_planned` falls back to the plain search, and so does the eval."""
    from northwind_rag.query import Filters, Part

    wide = Part("é" * 200, Filters(colour=("Navy",), availability="in_stock"))
    plan = QueryPlan(parts=(wide, wide, wide), source="model")
    message = "something"
    assert query_plans.shopper_parts(plan, message, "US", FixtureCatalog()) == [
        (message, SearchFilter())
    ]


def test_a_plain_plan_is_the_plain_search() -> None:
    message = "What's your returns policy?"
    parts = query_plans.shopper_parts(rules_plan(message, "US"), message, "US", FixtureCatalog())
    assert parts == [(message, SearchFilter())]


def test_the_sdk_logger_is_held_quiet() -> None:
    assert "anthropic" in rag_client.QUIET_LOGGERS
    logging.getLogger("anthropic").setLevel(logging.DEBUG)
    rag_client.quiet_client_logs()
    assert logging.getLogger("anthropic").level == logging.WARNING


def test_live_mode_makes_no_sdk_retries() -> None:
    assert Understander(live=True)._client_for("http://127.0.0.1:9").max_retries == 0
    assert Understander(live=False)._client_for("http://127.0.0.1:9").max_retries == 2


def test_the_sdk_logger_is_quiet_when_the_request_is_sent() -> None:
    """Re-review N1: building the client may import `anthropic`, which can set its logger to DEBUG;
    the logger is held quiet after that, when the request goes out."""
    from types import SimpleNamespace

    from northwind_assistant.understand import MARKER  # noqa: F401 - the module under test

    levels: list[int] = []

    class Client:
        def __init__(self) -> None:
            logging.getLogger("anthropic").setLevel(logging.DEBUG)  # what the import can do
            self.messages = self

        def create(self, **_: Any) -> Any:
            levels.append(logging.getLogger("anthropic").level)
            return SimpleNamespace(stop_reason="end_turn", model="m", usage=None, content=[])

    understand = Understander(live=False, client_for=lambda _: Client())
    asyncio.run(understand.plan(MULTI, "US", "http://unused"))
    assert levels == [logging.WARNING]


def test_a_refused_take_extends_the_cap_entry() -> None:
    now = [1000.0]
    ledger = SearchLedger(clock=lambda: now[0])
    for _ in range(3):
        assert asyncio.run(ledger.take("t", 1050))  # the CLI's token uses the cap
    assert not asyncio.run(ledger.take("t", 1100))  # a later-expiring token is refused...
    now[0] = 1060.0
    assert not asyncio.run(ledger.take("t", 1100))  # ...and stays refused after the first expiry
