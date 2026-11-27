"""Row #18 (RECORD-ALL), the user's option 1 (2026-11-27): edge cases for the dispatch-order
contract of `CatalogLog`, `dispatch_order` and `serve_catalog`, written from the contract.

Contract: `CatalogLog.taken()` is sorted by `order` ascending; unnumbered results come after every
numbered one, in arrival order; ties keep arrival order; `begin()` clears. `dispatch_order(params)`
is `params.meta["progress_token"]` when it is a real int, else None. `serve_catalog` logs
`dispatch_order(params)` for every call, a leak-replaced or error result included. The judge's
request built from `live_of(log.taken(), region)` is the same whatever order the calls finish in.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from types import SimpleNamespace
from typing import Any

import pytest

from northwind_common import catalog_tool
from northwind_common.llm import anthropic_client
from northwind_evals.answerers import CatalogLog, dispatch_order, live_of, serve_catalog
from northwind_evals.contexts import ContextChunk
from northwind_evals.items import EvalItem
from northwind_evals.judge import Judge
from northwind_llm_replay.scripted import ScriptedLLM

pytestmark = pytest.mark.req("RAG-12")

ITEM = EvalItem(
    "EV-1", "intent_filter", "Under $150?", "US", "answer", (("P1",),), ("f",), "w", None, "model"
)
CHUNK = ContextChunk("product:P1:0", "P1", "product", "A shell.")


def price(variant: str) -> str:
    return catalog_tool.label(
        "get_price", {"variant_id": variant, "region": "US", "amount": "150.00", "currency": "USD"}
    )


def stock(variant: str) -> str:
    return catalog_tool.label(
        "check_stock", {"variant_id": variant, "region": "US", "on_hand": 4, "in_stock": True}
    )


def product(pid: str) -> str:
    return catalog_tool.label("get_product", {"product_id": pid})


def variant_of(text: str) -> str:
    labelled = catalog_tool.labelled_result(text, False)
    assert labelled is not None, text
    return str(labelled[1]["variant_id"])


def meta(token: Any) -> SimpleNamespace:
    return SimpleNamespace(meta={"progress_token": token})


def judge_body(log: CatalogLog) -> dict[str, Any]:
    blocks, _live = live_of(log.taken(), "US")
    with ScriptedLLM() as llm:
        llm.reply(
            json.dumps({"claims": [], "relevance": "full", "declined": False, "behaviour_ok": True})
        )
        Judge(anthropic_client(llm.url), "claude-sonnet-5-5").judge(
            ITEM, (CHUNK, *blocks), "Under $150."
        )
        body: dict[str, Any] = llm.requests[0]["body"]
    return body


# --- CatalogLog ---------------------------------------------------------------------------------


def test_ties_keep_arrival_order() -> None:
    log = CatalogLog()
    log.add("get_price", False, "b", 5)
    log.add("get_price", False, "a", 5)
    log.add("get_price", False, "c", 5)
    assert [t for _tool, _err, t in log.taken()] == ["b", "a", "c"]


def test_ties_keep_arrival_order_around_other_numbers() -> None:
    log = CatalogLog()
    log.add("get_price", False, "late-7", 7)
    log.add("get_price", False, "tie-2-first", 2)
    log.add("get_price", False, "one", 1)
    log.add("get_price", False, "tie-2-second", 2)
    assert [t for _tool, _err, t in log.taken()] == [
        "one",
        "tie-2-first",
        "tie-2-second",
        "late-7",
    ]


def test_unnumbered_come_after_every_numbered_in_arrival_order() -> None:
    log = CatalogLog()
    log.add("check_stock", False, "u1")
    log.add("check_stock", False, "n9", 9)
    log.add("check_stock", False, "u2", None)
    log.add("check_stock", False, "n1", 1)
    log.add("check_stock", False, "u3")
    assert [t for _tool, _err, t in log.taken()] == ["n1", "n9", "u1", "u2", "u3"]


def test_order_zero_and_negative_are_numbers_not_missing() -> None:
    log = CatalogLog()
    log.add("get_price", False, "none")
    log.add("get_price", False, "one", 1)
    log.add("get_price", False, "zero", 0)
    log.add("get_price", False, "neg", -3)
    assert [t for _tool, _err, t in log.taken()] == ["neg", "zero", "one", "none"]


def test_taken_keeps_tool_and_error_flag_with_each_result() -> None:
    log = CatalogLog()
    log.add("get_price", True, "boom", 2)
    log.add("check_stock", False, "fine", 1)
    assert log.taken() == [("check_stock", False, "fine"), ("get_price", True, "boom")]


def test_begin_clears_everything_and_numbering_restarts() -> None:
    log = CatalogLog()
    log.add("get_price", False, "old", 1)
    log.add("get_price", False, "old-unnumbered")
    log.leak = True
    log.begin()
    assert log.taken() == []
    assert log.leak is False
    log.add("get_price", False, "new-b", 2)
    log.add("get_price", False, "new-a", 1)
    assert [t for _tool, _err, t in log.taken()] == ["new-a", "new-b"]


def test_begin_with_a_session_is_still_a_clear() -> None:
    log = CatalogLog()
    log.add("get_price", False, "old", 1)
    log.begin("some-session")
    assert log.taken() == []


# --- dispatch_order -----------------------------------------------------------------------------


@pytest.mark.parametrize("token", [0, 1, 17, 10**6])
def test_dispatch_order_returns_an_int_token(token: int) -> None:
    assert dispatch_order(meta(token)) == token


@pytest.mark.parametrize("token", ["3", "", 3.0, 2.5, True, False, None, [3], {"n": 3}])
def test_dispatch_order_rejects_a_non_int_token(token: Any) -> None:
    assert dispatch_order(meta(token)) is None


def test_dispatch_order_rejects_missing_meta_and_keys() -> None:
    assert dispatch_order(SimpleNamespace()) is None
    assert dispatch_order(SimpleNamespace(meta=None)) is None
    assert dispatch_order(SimpleNamespace(meta={})) is None
    assert dispatch_order(SimpleNamespace(meta={"progressToken": 3})) is None
    assert dispatch_order(None) is None


@pytest.mark.parametrize("not_a_mapping", ["progress_token", 5, [("progress_token", 1)], object()])
def test_dispatch_order_rejects_a_non_mapping_meta(not_a_mapping: Any) -> None:
    assert dispatch_order(SimpleNamespace(meta=not_a_mapping)) is None


def test_dispatch_order_reads_the_real_request_meta_type() -> None:
    from mcp.types import RequestParamsMeta

    params = SimpleNamespace(meta=RequestParamsMeta(progress_token=4))
    assert dispatch_order(params) == 4


# --- the judge request --------------------------------------------------------------------------

VARIANTS = ("P0003-SLT-REL-S", "P0003-SLT-REL-M", "P0003-SLT-REL-L", "P0002-NAV-REL-S")


def mixed_log(finish: list[int]) -> CatalogLog:
    """Five calls dispatched 1..5, of three tools; `finish` is the order they arrive in."""
    calls = [
        ("get_product", product("P0003")),
        ("get_price", price(VARIANTS[0])),
        ("check_stock", stock(VARIANTS[1])),
        ("get_price", price(VARIANTS[2])),
        ("check_stock", stock(VARIANTS[3])),
    ]
    log = CatalogLog()
    for k in finish:
        tool, text = calls[k]
        log.add(tool, False, text, k + 1)
    return log


def test_mixed_tools_keep_stable_numbering_whatever_finishes_first() -> None:
    expected = [
        ("live:get_price:1", VARIANTS[0]),
        ("live:check_stock:2", VARIANTS[1]),
        ("live:get_price:3", VARIANTS[2]),
        ("live:check_stock:4", VARIANTS[3]),
    ]
    for finish in ([0, 1, 2, 3, 4], [4, 3, 2, 1, 0], [2, 4, 0, 3, 1], [1, 0, 4, 2, 3]):
        blocks, _live = live_of(mixed_log(finish).taken(), "US")
        got = [(b.source_id, json.loads(b.text)["variant_id"]) for b in blocks]
        assert got == expected, finish


def test_mixed_tools_judge_request_is_identical_whatever_finishes_first() -> None:
    in_order = judge_body(mixed_log([0, 1, 2, 3, 4]))
    for finish in ([4, 3, 2, 1, 0], [2, 4, 0, 3, 1], [3, 0, 4, 1, 2]):
        assert judge_body(mixed_log(finish)) == in_order, finish


def test_judge_request_is_identical_with_ties_and_unnumbered_in_the_same_arrival_order() -> None:
    def build() -> CatalogLog:
        log = CatalogLog()
        log.add("get_price", False, price(VARIANTS[0]))
        log.add("get_price", False, price(VARIANTS[2]), 2)
        log.add("get_price", False, price(VARIANTS[1]), 1)
        return log

    assert judge_body(build()) == judge_body(build())
    blocks, _live = live_of(build().taken(), "US")
    assert [json.loads(b.text)["variant_id"] for b in blocks] == [
        VARIANTS[1],
        VARIANTS[2],
        VARIANTS[0],
    ]


# --- serve_catalog ------------------------------------------------------------------------------

WANTED = ["P0011-SAG-REG-S", "P0011-SAG-REG-M", "P0011-SAG-REG-L"]


def run_calls(
    calls: list[tuple[str, dict[str, Any], int | None]],
    delays: list[float],
    leaks: Any = None,
) -> CatalogLog:
    """Fire `calls` (tool, args, progress token) concurrently; call i is held for delays[i] seconds
    before its handler runs, so the finish order is chosen by the test."""
    from mcp.types import RequestParamsMeta

    import northwind_mcp_catalog.tools as tools
    from northwind_assistant.caller import Caller
    from northwind_assistant.catalog_client import CatalogEndpoint
    from northwind_mcp_catalog.testing import connect

    key = b"k" * 40
    log = CatalogLog()
    delay_of = {
        (tool, json.dumps(args, sort_keys=True)): d
        for (tool, args, _t), d in zip(calls, delays, strict=True)
    }
    mp = pytest.MonkeyPatch()
    for name in {tool for tool, _a, _t in calls}:
        spec = tools.READ_TOOLS[name]

        def make(name: str, real: Any) -> Any:
            async def slow(server: Any, claims: Any, args: Any) -> Any:
                await asyncio.sleep(delay_of[(name, json.dumps(dict(args), sort_keys=True))])
                return await real(server, claims, args)

            return slow

        mp.setitem(tools.TOOLS, name, dataclasses.replace(spec, handler=make(name, spec.handler)))
    try:
        with serve_catalog(key, log, leaks) as url:
            token = CatalogEndpoint(url, key).mint(Caller(region="US"))

            async def fire() -> None:
                async with connect(url, token) as client:
                    await asyncio.gather(
                        *(
                            client.call_tool(
                                tool,
                                args,
                                meta=None if t is None else RequestParamsMeta(progress_token=t),
                            )
                            for tool, args, t in calls
                        )
                    )

            asyncio.run(fire())
    finally:
        mp.undo()
    return log


def test_an_error_result_keeps_its_dispatch_slot() -> None:
    calls: list[tuple[str, dict[str, Any], int | None]] = [
        ("get_price", {"variant_id": WANTED[0]}, 1),
        ("get_price", {"variant_id": "NO-SUCH-VARIANT"}, 2),
        ("get_price", {"variant_id": WANTED[2]}, 3),
    ]
    log = run_calls(calls, delays=[0.6, 0.3, 0.0])
    taken = log.taken()
    assert [err for _tool, err, _t in taken] == [False, True, False]
    assert variant_of(taken[0][2]) == WANTED[0]
    assert variant_of(taken[2][2]) == WANTED[2]


def test_a_leak_replaced_result_keeps_its_dispatch_slot() -> None:
    def leaks(chunks: Any) -> bool:
        return any(WANTED[1] in c.text for c in chunks)

    calls: list[tuple[str, dict[str, Any], int | None]] = [
        ("get_price", {"variant_id": WANTED[0]}, 1),
        ("get_price", {"variant_id": WANTED[1]}, 2),
        ("get_price", {"variant_id": WANTED[2]}, 3),
    ]
    log = run_calls(calls, delays=[0.6, 0.3, 0.0], leaks=leaks)
    assert log.leak is True
    taken = log.taken()
    assert [err for _tool, err, _t in taken] == [False, True, False]
    assert WANTED[1] not in taken[1][2]
    assert variant_of(taken[0][2]) == WANTED[0]
    assert variant_of(taken[2][2]) == WANTED[2]


def test_mixed_tools_through_the_catalog_follow_the_tokens_not_the_finish() -> None:
    calls: list[tuple[str, dict[str, Any], int | None]] = [
        ("get_product", {"product_id": "P0011"}, 1),
        ("get_price", {"variant_id": WANTED[0]}, 2),
        ("check_stock", {"variant_id": WANTED[1]}, 3),
        ("get_price", {"variant_id": WANTED[2]}, 4),
    ]
    for delays in ([0.0, 0.1, 0.2, 0.3], [0.4, 0.3, 0.2, 0.0]):
        taken = run_calls(calls, delays=delays).taken()
        assert [tool for tool, _e, _t in taken] == [
            "get_product",
            "get_price",
            "check_stock",
            "get_price",
        ], delays
        blocks, _live = live_of(taken, "US")
        assert [b.source_id for b in blocks] == [
            "live:get_price:1",
            "live:check_stock:2",
            "live:get_price:3",
        ], delays


def test_calls_with_no_token_through_the_catalog_come_after_numbered_ones() -> None:
    calls: list[tuple[str, dict[str, Any], int | None]] = [
        ("get_price", {"variant_id": WANTED[0]}, None),
        ("get_price", {"variant_id": WANTED[1]}, 2),
        ("get_price", {"variant_id": WANTED[2]}, 1),
    ]
    taken = run_calls(calls, delays=[0.0, 0.3, 0.6]).taken()
    assert [variant_of(t) for _tool, _e, t in taken] == [WANTED[2], WANTED[1], WANTED[0]]
