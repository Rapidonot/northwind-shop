"""SA-01 PR 2: the open item from PR 1's security review, F4 (Low).

`CartReader` built an HTTP client for each read and never closed it (`streamable_http_client` leaves
a client it was given open), so each `GET /api/cart` leaked a connection pool. Every client a read
opens is now closed, on success and on failure alike."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CartReader
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.tools import TOOLS

pytestmark = pytest.mark.req("SA-01")

KEY = catalog_testing.KEY
SESSION = "c" * 32


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    with catalog_testing.running(CatalogServer(FixtureCatalog(), key=KEY, tools=TOOLS)) as running:
        yield running


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    made: list[Any] = []

    class Tracked(httpx2.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(httpx2, "AsyncClient", Tracked)  # the module catalog_client reads it from
    return made


def test_every_client_a_read_opens_is_closed(url: str, clients: list[Any]) -> None:
    reader = CartReader(url, KEY)
    for _ in range(3):
        view = asyncio.run(reader.read(Caller(region="US", session=SESSION)))
        assert view is not None and view["region"] == "US"
    assert len(clients) == 3
    assert all(client.is_closed for client in clients)


def test_a_failed_read_closes_its_client_too(clients: list[Any]) -> None:
    reader = CartReader("http://127.0.0.1:9/mcp", KEY)  # nothing listens there
    with pytest.raises(Exception):  # noqa: B017 - any transport failure; the route maps them all
        asyncio.run(reader.read(Caller(region="US", session=SESSION)))
    assert clients and all(client.is_closed for client in clients)
