"""Row #23 (SA-06; security read after 4821fc8, L3; the advisor's re-read, D3): the gateway's
routes refuse every body a cross-site page can send without a preflight.

That holds because FastAPI's body parsing is strict about content types by default
(`strict_content_type=True`, FastAPI 0.162): a body with no Content-Type, or a text, form or
multipart body, is not parsed as JSON, so the request model refuses it with chat's fixed 422.
These tests pin the behaviour on /api/chat, which this row does not change, and pin that nothing
in the gateway turns the default off. A FastAPI upgrade that changes the default fails here.
Measured first: docs/reviews/2026-11-26-SA-06-content-type-probe.md."""

from __future__ import annotations

import inspect
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import northwind_gateway.chat as chat_module
from northwind_common.mcp_token import KEY_ENV
from northwind_gateway.app import app
from northwind_gateway.chat import INVALID_MESSAGE, ChatServices, chat_services
from northwind_mcp_catalog import testing as catalog_testing

pytestmark = pytest.mark.req("SA-06")

GATEWAY_SRC = Path(chat_module.__file__).resolve().parent
SIMPLE_BODIES = [
    None,
    "",
    "text/plain",
    "application/x-www-form-urlencoded",
    "multipart/form-data; boundary=x",
]


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, list[str]]]:
    seen: list[str] = []

    async def stream(message: str, *_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        seen.append(message)
        if False:
            yield None

    monkeypatch.setenv(KEY_ENV, catalog_testing.KEY.decode())
    monkeypatch.setattr(chat_module, "stream_answer", stream)
    app.dependency_overrides[chat_services] = lambda: ChatServices(None, None)  # type: ignore[arg-type]
    try:
        yield TestClient(app), seen
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("kind", SIMPLE_BODIES)
def test_chat_refuses_every_body_a_cross_site_page_can_send(
    chat: tuple[TestClient, list[str]], kind: str | None
) -> None:
    client, seen = chat
    headers = {} if kind is None else {"content-type": kind}
    response = client.post("/api/chat", content=b'{"message": "hello there"}', headers=headers)
    assert response.status_code == 422 and response.json()["message"] == INVALID_MESSAGE
    assert seen == []


def test_chat_still_takes_a_json_body(chat: tuple[TestClient, list[str]]) -> None:
    client, seen = chat
    assert client.post("/api/chat", json={"message": "hello there"}).status_code == 200
    assert seen == ["hello there"]


def test_fastapi_parses_bodies_strictly_by_default() -> None:
    default = inspect.signature(APIRoute.__init__).parameters["strict_content_type"].default
    assert getattr(default, "value", default) is True


def test_no_gateway_route_or_app_turns_strict_content_types_off() -> None:
    sources = sorted(GATEWAY_SRC.glob("*.py"))
    assert sources
    for path in sources:
        assert not re.search(r"strict_content_type", path.read_text(encoding="utf-8")), path.name
    for route in app.routes:
        if isinstance(route, APIRoute):
            strict = getattr(route, "strict_content_type", True)
            assert getattr(strict, "value", strict) is True, route.path
