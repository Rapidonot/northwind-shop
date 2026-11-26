"""CHK-07 stage B, gateway gaps (test-writer): the policy endpoint is always wired beside the
orders endpoint, with the same key, and an anonymous chat is still handed both (the turn, not the
gateway, decides who gets a token). Fakes only."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_assistant.orders_client import OrdersEndpoint, PolicyEndpoint
from northwind_common.mcp_token import KEY_ENV
from northwind_gateway import chat as chat_module
from northwind_gateway.app import app
from northwind_gateway.chat import ChatServices, chat_services

pytestmark = pytest.mark.req("CHK-07")

KEY = b"w" * 40


def test_a_service_set_built_by_hand_has_no_policy_until_it_is_given_one() -> None:
    services = ChatServices(rag=None, runtime=None)  # type: ignore[arg-type]
    assert services.orders is None and services.policy is None


def test_chat_services_wires_policy_whenever_it_wires_orders_with_one_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    monkeypatch.setenv("MCP_ORDERS_URL", "http://127.0.0.1:18434/mcp")
    monkeypatch.setenv("MCP_POLICY_URL", "http://127.0.0.1:18435/mcp")
    chat_services.cache_clear()
    try:
        services = chat_services()
    finally:
        chat_services.cache_clear()
    assert isinstance(services.orders, OrdersEndpoint)
    assert isinstance(services.policy, PolicyEndpoint)
    assert services.orders.key == services.policy.key == KEY
    assert services.orders.url != services.policy.url
    assert services.policy.url == "http://127.0.0.1:18435/mcp"


def test_an_anonymous_chat_is_handed_both_endpoints_and_no_shopper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def nothing() -> AsyncIterator[Any]:
        return
        yield

    def stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        seen.append((args, kwargs))
        return nothing()

    monkeypatch.setattr(chat_module, "stream_answer", stream)
    orders, policy = (
        OrdersEndpoint("http://127.0.0.1:1/mcp", KEY),
        PolicyEndpoint("http://127.0.0.1:2/mcp", KEY),
    )
    services = ChatServices(rag=None, runtime=None, orders=orders, policy=policy)  # type: ignore[arg-type]
    app.dependency_overrides[chat_services] = lambda: services
    try:
        TestClient(app).post("/api/chat", json={"message": "Can I return my boots?"})
    finally:
        app.dependency_overrides.pop(chat_services, None)
    [(args, kwargs)] = seen
    assert kwargs["orders"] is orders and kwargs["policy"] is policy
    caller = args[1]
    assert caller.shopper_id is None  # no cookie, no shopper: the turn then mints no token
