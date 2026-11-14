"""mcp-catalog's logs name categories and codes only (security review L5, #10b; hard rules 1 and 4).

The same check as mcp-rag's `test_no_query_text_reaches_a_server_log_on_any_path`: every tool, every
refusal path, a failing store and a refused token, with the root logger at DEBUG. No argument, card
number, token or exception text may reach a server-side record.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest

from northwind_mcp_catalog.live import FixtureCatalog, Price
from northwind_mcp_catalog.testing import call, running, server, token

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("NFR-04")]

CARD = "4242" * 4  # synthetic
MARK = "LOGMARK-5c1f"
SECRET = "postgresql://user:hunter2@db/prod"  # noqa: S105 - a planted fake


class FailingCatalog(FixtureCatalog):
    def price(self, variant_id: str, region: str) -> Price | None:
        raise RuntimeError(SECRET)


CALLS: list[tuple[str, dict[str, Any]]] = [
    ("search_products", {"text": MARK}),
    ("search_products", {"text": f"jacket {CARD}"}),
    ("search_products", {MARK: "x"}),
    ("get_product", {"product_id": MARK}),
    ("get_product", {"product_id": "P0009"}),
    ("get_variant", {"variant_id": f"P0001-{MARK}"}),
    ("get_variant", {"variant_id": "P0001-BLK-REG-M", "note": CARD}),
    ("check_stock", {"variant_id": "P0001-BLK-REG-M", MARK: 1}),
    ("get_price", {"variant_id": "P0001-BLK-REG-M"}),  # the failing store
    ("set_price", {"variant_id": MARK}),
]


def test_no_argument_token_or_exception_text_reaches_a_server_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    tok, _ = token()
    with running(server(FailingCatalog())) as url:
        for name, arguments in CALLS:
            asyncio.run(call(url, tok, name, arguments))
        refused = httpx.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "accept": "application/json, text/event-stream",
                "authorization": f"Bearer v1.{MARK}.sig",
            },
            timeout=10,
        )
    assert refused.status_code == 401
    client_side = ("mcp.client", "httpx", "httpcore")
    server_side = [r for r in caplog.records if not r.name.startswith(client_side)]
    # Formatted, so a traceback or stack attached to a record is read too (re-review N2), and no
    # server-side record carries one at all.
    formatter = logging.Formatter()
    server_text = "\n".join(formatter.format(r) for r in server_side)
    assert [r.name for r in server_side if r.exc_info or r.stack_info] == []
    # Not vacuous: the failure, the refusal and the token gate all logged something.
    assert "tool failed" in server_text
    assert "tool refused" in server_text
    assert "request refused: token" in server_text
    # The full PAN, not a bare "4242", which a port or session ID could hold by chance.
    for needle in (MARK, CARD, tok, tok.split(".")[2], SECRET, "hunter2"):
        assert needle not in server_text
