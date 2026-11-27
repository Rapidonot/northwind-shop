"""Row #18 (RECORD-ALL), the user's option 1 (2026-11-27): the judge's live values follow the order
in which the model asked for the calls, not the order in which they finished.

EV-029's rescore missed on its judge request: the turn's 17 parallel `get_price` calls reached
`CatalogLog` as each finished, `live_of` numbered them in that order, and the judge prompt rendered
them so, while the proxy's `tool_use` reorder (row #16) covers only the model's own requests
(`docs/reviews/2026-11-27-18-wrapup.md`, 2a). The CLI numbers each MCP call with a `progressToken`
as it dispatches it, in `tool_use` order; the log now returns results in that order.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any

import pytest

from northwind_common import catalog_tool
from northwind_evals.answerers import CatalogLog, live_of, render_sources
from northwind_evals.contexts import ContextChunk
from northwind_evals.items import EvalItem
from northwind_evals.judge import Judge
from northwind_llm_replay.scripted import ScriptedLLM

pytestmark = pytest.mark.req("RAG-12")

VARIANTS = ("P0003-SLT-REL-S", "P0003-SLT-REL-M", "P0003-SLT-REL-L", "P0002-NAV-REL-S")
ITEM = EvalItem(
    "EV-1", "intent_filter", "Under $150?", "US", "answer", (("P1",),), ("f",), "w", None, "model"
)
CHUNK = ContextChunk("product:P1:0", "P1", "product", "A shell.")


def price(variant: str) -> str:
    return catalog_tool.label(
        "get_price", {"variant_id": variant, "region": "US", "amount": "150.00", "currency": "USD"}
    )


def variant_of(text: str) -> str:
    labelled = catalog_tool.labelled_result(text, False)
    assert labelled is not None, text
    return str(labelled[1]["variant_id"])


def filled(finish_order: list[int]) -> CatalogLog:
    """A log fed as the calls finish: call k (dispatched k-th) arrives at position finish_order."""
    log = CatalogLog()
    log.add("get_product", False, catalog_tool.label("get_product", {"product_id": "P0003"}), 1)
    for k in finish_order:
        log.add("get_price", False, price(VARIANTS[k]), k + 2)
    return log


def judge_body(log: CatalogLog) -> dict[str, Any]:
    blocks, _live = live_of(log.taken(), "US")
    with ScriptedLLM() as llm:
        llm.reply(
            json.dumps({"claims": [], "relevance": "full", "declined": False, "behaviour_ok": True})
        )
        from northwind_common.llm import anthropic_client

        Judge(anthropic_client(llm.url), "claude-sonnet-5-5").judge(
            ITEM, (CHUNK, *blocks), "Under $150."
        )
        body: dict[str, Any] = llm.requests[0]["body"]
    return body


def test_the_judge_request_does_not_depend_on_completion_order() -> None:
    in_order = judge_body(filled([0, 1, 2, 3]))
    for finish in ([3, 2, 1, 0], [2, 0, 3, 1], [1, 3, 0, 2]):
        assert judge_body(filled(finish)) == in_order, finish


def test_live_values_come_in_dispatch_order_and_are_numbered_so() -> None:
    blocks, _live = live_of(filled([3, 1, 0, 2]).taken(), "US")
    assert [b.source_id for b in blocks] == [f"live:get_price:{n}" for n in (1, 2, 3, 4)]
    assert [json.loads(b.text)["variant_id"] for b in blocks] == list(VARIANTS)
    rendered = render_sources(blocks)
    assert [rendered.index(v) for v in VARIANTS] == sorted(rendered.index(v) for v in VARIANTS)


def test_results_with_no_dispatch_number_keep_their_arrival_order() -> None:
    """Scripted tests and older callers pass no number: nothing about them changes."""
    log = CatalogLog()
    for variant in reversed(VARIANTS):
        log.add("get_price", False, price(variant))
    assert [variant_of(t) for _tool, _err, t in log.taken()] == list(reversed(VARIANTS))


def test_the_in_process_catalog_logs_each_calls_progress_token() -> None:
    """End to end through `serve_catalog`: calls that finish in reverse still come back in the
    order their progress tokens give, as the CLI sends them (probe: `claudecode/toolUseId` and an
    increasing `progressToken` on every call)."""
    from mcp.types import RequestParamsMeta

    import northwind_mcp_catalog.tools as tools
    from northwind_assistant.caller import Caller
    from northwind_assistant.catalog_client import CatalogEndpoint
    from northwind_evals.answerers import serve_catalog
    from northwind_mcp_catalog.testing import connect

    key = b"k" * 40
    log = CatalogLog()
    wanted = ["P0011-SAG-REG-S", "P0011-SAG-REG-M", "P0011-SAG-REG-L"]
    real = tools.get_price

    async def slow(server: Any, claims: Any, args: Any) -> Any:
        # The first-dispatched call finishes last.
        await asyncio.sleep(0.2 * (len(wanted) - wanted.index(args["variant_id"])))
        return await real(server, claims, args)

    spec = tools.READ_TOOLS[tools.GET_PRICE]
    patched = dataclasses.replace(spec, handler=slow)
    mp = pytest.MonkeyPatch()
    mp.setitem(tools.TOOLS, tools.GET_PRICE, patched)
    try:
        with serve_catalog(key, log) as url:
            token = CatalogEndpoint(url, key).mint(Caller(region="US"))

            async def calls() -> None:
                async with connect(url, token) as client:
                    await asyncio.gather(
                        *(
                            client.call_tool(
                                "get_price",
                                {"variant_id": v},
                                meta=RequestParamsMeta(progress_token=n),
                            )
                            for n, v in enumerate(wanted, start=1)
                        )
                    )

            asyncio.run(calls())
    finally:
        mp.undo()
    assert [variant_of(t) for _tool, err, t in log.taken() if not err] == wanted
