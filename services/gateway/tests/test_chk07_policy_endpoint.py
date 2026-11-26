"""Row #27 PR 2 (CHK-07): the gateway hands every chat turn the policy endpoint, and the endpoint
itself gives a token only for a signed-in caller. Fakes only."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_assistant.caller import Caller
from northwind_assistant.orders_client import PolicyEndpoint
from northwind_common.mcp_token import KEY_ENV, POLICY_SERVER, verify_for
from northwind_gateway import chat as chat_module
from northwind_gateway.app import app
from northwind_gateway.chat import ChatServices, chat_services

pytestmark = pytest.mark.req("CHK-07")

KEY = b"g" * 40


def test_a_chat_turn_gets_the_policy_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    async def nothing() -> AsyncIterator[Any]:
        return
        yield

    def stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        seen.append(kwargs)
        return nothing()

    monkeypatch.setattr(chat_module, "stream_answer", stream)
    services = ChatServices(
        rag=None,  # type: ignore[arg-type]
        runtime=None,  # type: ignore[arg-type]
        orders="orders-endpoint",  # type: ignore[arg-type]
        policy="policy-endpoint",  # type: ignore[arg-type]
    )
    app.dependency_overrides[chat_services] = lambda: services
    try:
        TestClient(app).post("/api/chat", json={"message": "Can I return my boots?"})
    finally:
        app.dependency_overrides.pop(chat_services, None)
    [kwargs] = seen
    assert (kwargs["orders"], kwargs["policy"]) == ("orders-endpoint", "policy-endpoint")


def test_chat_services_always_sets_the_policy_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    monkeypatch.setenv("MCP_POLICY_URL", "http://127.0.0.1:18435/mcp")
    chat_services.cache_clear()
    try:
        services = chat_services()
        assert services.policy == PolicyEndpoint("http://127.0.0.1:18435/mcp", KEY)
    finally:
        chat_services.cache_clear()


def test_the_endpoint_gives_a_token_only_to_a_signed_in_shopper_and_hides_its_key() -> None:
    endpoint = PolicyEndpoint("http://127.0.0.1:9/mcp", KEY)
    assert endpoint.mint(Caller(region="UK", session="a" * 32)) is None
    token = endpoint.mint(Caller(region="UK", session="a" * 32, shopper_id="S0188"))
    assert token is not None
    claims = verify_for(token, key=KEY, server=POLICY_SERVER)
    assert (claims.shp, claims.region, claims.aud) == ("S0188", "UK", "shopper")
    assert KEY.decode() not in repr(endpoint)
