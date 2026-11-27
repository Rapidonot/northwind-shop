"""Test-only harness for SA-01 PR 2's portal writes, order history and demo checkout. Not product
code: nothing under `src` may import it.

A real mcp-catalog over the verified fixtures and a real mcp-orders over `data/backend`, both on
one key, with the gateway's readers and writers pointed at them. Requests go through the whole app
(session middleware included) on one event loop, so the checkout's per-shopper lock is shared.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from northwind_assistant.catalog_client import CartReader
from northwind_common.mcp_token import KEY_ENV
from northwind_gateway import checkout as checkout_module
from northwind_gateway import demo_orders as demo_orders_module
from northwind_gateway.app import app
from northwind_gateway.cart import cart_reader
from northwind_gateway.cart_writes import CartWriter, cart_writer
from northwind_gateway.orders import OrdersLister, orders_lister
from northwind_gateway.session import COOKIE, SHOPPER_COOKIE, session_key, sign, sign_shopper
from northwind_gateway.signin import DEMO_SHOPPER, DEMO_SHOPPERS
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.tools import TOOLS
from northwind_mcp_orders import testing as orders_testing
from northwind_mcp_orders.book import OrderBook

KEY = catalog_testing.KEY
OTHER_SHOPPER = "S0002"  # a second synthetic US shopper on file, for the two-shopper race
VARIANT = "P0004-BLK-REL-M"  # Brindlemoor Gale Rain Jacket, USD 160.00, in stock in the US
PORTAL = {
    "content-type": "application/json",
    "x-northwind-portal": "1",
    "sec-fetch-site": "same-origin",
}


def session_cookie(session: str) -> str:
    return f"{COOKIE}={sign(session_key(KEY), session)}"


def signed_in(session: str, shopper: str = DEMO_SHOPPER) -> str:
    signed = sign_shopper(session_key(KEY), session, shopper)
    return f"{session_cookie(session)}; {SHOPPER_COOKIE}={signed}"


@dataclass
class World:
    store: FixtureCatalog
    catalog_url: str
    orders_url: str

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    def run(self, work: Callable[[httpx.AsyncClient], Awaitable[Any]]) -> Any:
        async def go() -> Any:
            async with self.client() as client:
                return await work(client)

        return asyncio.run(go())


@contextlib.contextmanager
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setenv(KEY_ENV, KEY.decode())
    monkeypatch.setenv("DEV_SHOPPER_REGION", "US")
    monkeypatch.setenv("NORTHWIND_DEMO_SIGNIN", "1")
    monkeypatch.setitem(DEMO_SHOPPERS, OTHER_SHOPPER, "US")
    store = FixtureCatalog()
    checkout_module.checkout_desk.cache_clear()
    demo_orders_module.demo_orders.cache_clear()
    with (
        catalog_testing.running(CatalogServer(store, key=KEY, tools=TOOLS)) as catalog_url,
        orders_testing.running(orders_testing.server(OrderBook.from_data(), key=KEY)) as orders_url,
    ):
        app.dependency_overrides[cart_reader] = lambda: CartReader(catalog_url, KEY)
        app.dependency_overrides[cart_writer] = lambda: CartWriter(catalog_url, KEY)
        app.dependency_overrides[orders_lister] = lambda: OrdersLister(orders_url, KEY)
        try:
            yield World(store, catalog_url, orders_url)
        finally:
            app.dependency_overrides.clear()
            checkout_module.checkout_desk.cache_clear()
            demo_orders_module.demo_orders.cache_clear()


def key(n: int, salt: str = "k") -> str:
    """A portal-style key: letters only (never card-like), 24 characters."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    out, value = [], n * 7919 + sum(map(ord, salt))
    for _ in range(24):
        value, digit = divmod(value * 31 + 17, 26)
        out.append(letters[digit])
    return salt[:4] + "".join(out)


async def add(
    client: httpx.AsyncClient, cookie: str, variant: str = VARIANT, quantity: int = 1, n: int = 0
) -> httpx.Response:
    return await client.post(
        "/api/cart/items",
        headers={**PORTAL, "cookie": cookie},
        json={"variant_id": variant, "quantity": quantity, "idempotency_key": key(n, "add")},
    )


async def summary(client: httpx.AsyncClient, cookie: str) -> httpx.Response:
    return await client.get("/api/checkout/summary", headers={"cookie": cookie})


async def pay(client: httpx.AsyncClient, cookie: str, digest: str, n: int = 0) -> httpx.Response:
    return await client.post(
        "/api/checkout/pay",
        headers={**PORTAL, "cookie": cookie},
        json={"digest": digest, "idempotency_key": key(n, "pay")},
    )
