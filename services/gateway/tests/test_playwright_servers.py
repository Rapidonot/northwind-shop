"""The browser tests start every server the gateway attaches (row #16; the #12 lesson, when CI's
e2e failed because Playwright did not start mcp-catalog), and leave `DATABASE_URL` to make, so the
servers read the database make was given (the user's amendment 1: the scratch database)."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from northwind_gateway.chat import ChatServices

pytestmark = pytest.mark.req("CHK-01")

CONFIG = (Path(__file__).resolve().parents[3] / "apps/portal/playwright.config.ts").read_text()


def test_the_gateway_attaches_exactly_mcp_rag_mcp_catalog_and_mcp_orders() -> None:
    # A new server here must also be started in playwright.config.ts: this pin fails first.
    # Row #27 (CHK-06): mcp-orders joins, for signed-in turns. Still exact.
    fields = {f.name for f in dataclasses.fields(ChatServices)}
    assert fields == {"rag", "runtime", "catalog", "orders"}


@pytest.mark.parametrize(
    "module", ["northwind_mcp_rag.app", "northwind_mcp_catalog.app", "northwind_mcp_orders.app"]
)
def test_playwright_starts_each_attached_server(module: str) -> None:
    assert f"uv run python -m {module}" in CONFIG


def test_the_gateway_is_told_where_both_servers_are() -> None:
    block = CONFIG.split("northwind_gateway.app:app", 1)[1].split("}", 1)[0]
    [gateway_env] = re.findall(r"env: \{([^\n]*)", block)
    assert "...ragEnv" in gateway_env and "...catalogEnv" in gateway_env
    assert "...ordersEnv" in gateway_env  # row #27
    assert "MCP_RAG_URL" in CONFIG and "MCP_CATALOG_URL" in CONFIG and "MCP_ORDERS_URL" in CONFIG


def test_playwright_never_sets_the_database_itself() -> None:
    # Playwright starts each server with its own environment merged over the parent's, so the
    # DATABASE_URL make exports reaches the gateway and mcp-rag unless the config overrides it.
    assert "DATABASE_URL" not in CONFIG


def test_the_browser_cart_journey_is_the_scripted_models_journey() -> None:
    """cart.e2e.ts keeps its steps inline (a journey imports only ./journey, NFR-08's guard); the
    scripted model and the recorded journey read cart.json. Each message, answer and expected line
    must be in both, so the two cannot drift apart."""
    import json

    portal = Path(__file__).resolve().parents[3] / "apps/portal/e2e"
    journey = (portal / "cart.e2e.ts").read_text(encoding="utf-8")
    steps = json.loads((portal / "cart.json").read_text(encoding="utf-8"))["steps"]
    assert len(re.findall(r"\bmessage: \"", journey)) == len(steps)
    for step in steps:
        assert json.dumps(step["message"]) in journey
        assert json.dumps("".join(step["answer"])) in journey
        for line in step["expect"]:
            assert json.dumps(line["variant_id"]) in journey
