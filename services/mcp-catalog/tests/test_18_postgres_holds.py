"""Row #18 step 0, the user's option B (2026-11-27): `PostgresHolds` reads exactly the holds BE-04's
own `PostgresInventory` counts, region by region, and fails as a structured transient error.

Integration tests on a throwaway schema (no conftest.py: that is on the control surface), with
BE-04's store writing the holds, so "live" is BE-04's meaning, not a copy of it. One unit test
checks `app.holds()`'s wiring.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from northwind_backend.errors import Ok
from northwind_backend.inventory import HOLD, Line
from northwind_backend.inventory.live_holds import DEFAULT_DATABASE_URL
from northwind_backend.inventory.postgres import PostgresInventory, apply_schema, load_stock
from northwind_common.errors import ToolError
from northwind_mcp_catalog import app
from northwind_mcp_catalog.holds import NoHolds, PostgresHolds

pytestmark = pytest.mark.req("BE-04")

START = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
ROWS: list[dict[str, Any]] = [
    {"variant_id": "P0001-BLK-REG-M", "node": "DC-EAST", "region": "US", "on_hand": 5},
    {"variant_id": "P0001-BLK-REG-M", "node": "DC-WEST", "region": "US", "on_hand": 5},
    {"variant_id": "P0001-BLK-REG-M", "node": "DC-CA", "region": "CA", "on_hand": 5},
    {"variant_id": "P0001-BLK-REG-L", "node": "DC-EAST", "region": "US", "on_hand": 5},
]


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def schema() -> Iterator[Callable[[], psycopg.Connection[Any]]]:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    name = f"holds_reader_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        yield lambda: psycopg.connect(url, options=f"-csearch_path={name}")
    finally:
        admin.execute(f'DROP SCHEMA "{name}" CASCADE')
        admin.close()


def stocked(connect: Callable[[], psycopg.Connection[Any]]) -> tuple[PostgresInventory, Clock]:
    with connect() as conn:
        apply_schema(conn)
        load_stock(conn, ROWS)
    clock = Clock()
    return PostgresInventory(connect, clock), clock


def reserve(
    store: PostgresInventory, key: str, region: str, postal: str, variant: str, q: int
) -> None:
    done = store.reserve(
        idempotency_key=key,
        order_id="O900010",
        region=region,
        postal_code=postal,
        lines=[Line(variant, q)],
    )
    assert getattr(done, "value", None) is not None, done


def bes_held(store: PostgresInventory, variant: str, region: str) -> int:
    """What BE-04 itself counts as held in a region: on hand less available, over its nodes."""
    total = 0
    for row in ROWS:
        if (row["variant_id"], row["region"]) == (variant, region):
            available = store.available(variant, str(row["node"]))
            assert isinstance(available, Ok), available
            total += int(row["on_hand"]) - available.value
    return total


@pytest.mark.integration
def test_the_reader_agrees_with_bes_store_region_by_region(schema: Any) -> None:
    store, clock = stocked(schema)
    reader = PostgresHolds("unused", clock=clock, connect=schema)
    assert reader.live("US") == {}
    reserve(store, "reader-key-1", "US", "10001", "P0001-BLK-REG-M", 3)
    reserve(store, "reader-key-2", "US", "94103", "P0001-BLK-REG-M", 2)
    reserve(store, "reader-key-3", "CA", "M5V 2T6", "P0001-BLK-REG-M", 1)
    us, ca = reader.live("US"), reader.live("CA")
    assert (
        us
        == {"P0001-BLK-REG-M": bes_held(store, "P0001-BLK-REG-M", "US")}
        == {"P0001-BLK-REG-M": 5}
    )
    assert ca == {"P0001-BLK-REG-M": 1}
    assert reader.live("UK") == {}


@pytest.mark.integration
def test_released_and_expired_holds_are_not_read_but_a_sale_stays_subtracted(schema: Any) -> None:
    """Security review M1 of option B: BE-04's commit sweeps the holds and lowers its own `on_hand`,
    which this catalog does not read, so a sold unit must stay subtracted. A release gives the
    units back; an expired hold does too."""
    store, clock = stocked(schema)
    reader = PostgresHolds("unused", clock=clock, connect=schema)
    reserve(store, "reader-key-4", "US", "10001", "P0001-BLK-REG-M", 2)
    reserve(store, "reader-key-5", "US", "10001", "P0001-BLK-REG-L", 1)
    store.release(idempotency_key="reader-key-4")
    assert reader.live("US") == {"P0001-BLK-REG-L": 1}
    store.commit(idempotency_key="reader-key-5")
    assert reader.live("US") == {"P0001-BLK-REG-L": 1}  # sold: still not free to sell
    clock.now = START + timedelta(days=30)
    assert reader.live("US") == {"P0001-BLK-REG-L": 1}  # a sale never expires
    clock.now = START
    reserve(store, "reader-key-6", "US", "10001", "P0001-BLK-REG-M", 1)
    clock.now = START + HOLD - timedelta(seconds=1)
    assert reader.live("US") == {"P0001-BLK-REG-L": 1, "P0001-BLK-REG-M": 1}
    clock.now = START + HOLD  # `expires_at == now` is not live (BE-04)
    assert reader.live("US") == {"P0001-BLK-REG-L": 1}


@pytest.mark.integration
def test_the_catalog_reads_fixture_stock_less_sold_and_held(schema: Any) -> None:
    """End to end on the server: reserve, commit, then the catalog shows fixture stock less the
    committed units (security review M1's test)."""
    from northwind_mcp_catalog.live import FixtureCatalog
    from northwind_mcp_catalog.server import CatalogServer
    from northwind_mcp_catalog.testing import KEY
    from northwind_mcp_catalog.tools import TOOLS

    store, clock = stocked(schema)
    server = CatalogServer(
        FixtureCatalog(),
        key=KEY,
        tools=TOOLS,
        holds=PostgresHolds("u", clock=clock, connect=schema),
    )
    variant = "P0001-BLK-REG-M"
    fixture = server.catalog.stock(variant, "US") or 0
    reserve(store, "reader-key-7", "US", "10001", variant, 2)
    assert server.on_hand(variant, "US") == fixture - 2
    store.commit(idempotency_key="reader-key-7")
    assert server.on_hand(variant, "US") == fixture - 2


@pytest.mark.integration
def test_a_held_lock_is_a_transient_error_within_the_bound(schema: Any) -> None:
    """Security review M2: a lock on a BE-04 table makes the read fail fast and structured (BE-04's
    2 s lock timeout), never hang the server."""
    import time

    stocked(schema)
    blocker = schema()
    blocker.execute("LOCK TABLE inventory_holds IN ACCESS EXCLUSIVE MODE")
    try:
        started = time.monotonic()
        with pytest.raises(ToolError) as raised:
            PostgresHolds("unused", clock=Clock(), connect=schema).live("US")
        assert raised.value.code == "inventory_unavailable" and raised.value.retryable
        assert time.monotonic() - started < 10.0
    finally:
        blocker.rollback()
        blocker.close()


@pytest.mark.integration
def test_absent_tables_are_no_holds(schema: Any) -> None:
    assert PostgresHolds("unused", clock=Clock(), connect=schema).live("US") == {}


@pytest.mark.integration
def test_the_reader_cannot_write(schema: Any) -> None:
    """The reader's connection is read-only: a write through it is refused by the server."""
    stocked(schema)
    seen: list[psycopg.Connection[Any]] = []

    def spy() -> psycopg.Connection[Any]:
        conn: psycopg.Connection[Any] = schema()
        seen.append(conn)
        return conn

    PostgresHolds("unused", clock=Clock(), connect=spy).live("US")
    conn: psycopg.Connection[Any] = schema()
    conn.read_only = True
    with conn, pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        conn.execute("UPDATE inventory_stock SET on_hand = 0")
    assert seen and seen[0].read_only is True


def test_an_unreachable_database_is_a_transient_error_never_a_zero() -> None:
    reader = PostgresHolds("postgresql://u:p@127.0.0.1:1/none?connect_timeout=2", clock=Clock())
    with pytest.raises(ToolError) as raised:
        reader.live("US")
    error = raised.value
    assert (error.category, error.retryable, error.code) == (
        "transient",
        True,
        "inventory_unavailable",
    )


def test_a_naive_clock_is_refused() -> None:
    reader = PostgresHolds("unused", clock=lambda: datetime(2026, 11, 27))  # noqa: DTZ001
    with pytest.raises(ValueError):
        reader.live("US")


def test_the_app_wires_the_reader_only_when_database_url_is_set() -> None:
    assert isinstance(app.holds({}), NoHolds)
    assert isinstance(app.holds({"DATABASE_URL": ""}), NoHolds)
    assert isinstance(app.holds({"DATABASE_URL": "postgresql://localhost/x"}), PostgresHolds)


def test_the_server_reads_holds_off_the_event_loop_once_per_stock_call() -> None:
    """Security review M2: a stock tool's one reading runs in a worker thread, not on the loop."""
    import asyncio
    import threading
    import time as _time

    from mcp import types as t

    from northwind_common.mcp_token import CATALOG_SERVER, Claims
    from northwind_mcp_catalog.live import FixtureCatalog
    from northwind_mcp_catalog.server import CatalogServer
    from northwind_mcp_catalog.testing import KEY
    from northwind_mcp_catalog.tools import TOOLS

    threads: list[str] = []

    class Slow:
        def live(self, region: str) -> dict[str, int]:
            threads.append(threading.current_thread().name)
            return {}

    server = CatalogServer(FixtureCatalog(), key=KEY, tools=TOOLS, holds=Slow())
    claims = Claims(
        "shopper", "US", "shopper-assistant", "t", int(_time.time()) + 600, CATALOG_SERVER
    )

    class Ctx:
        pass

    async def go() -> t.CallToolResult:
        from northwind_mcp_catalog import server as srv

        original = srv.claims_of
        srv.claims_of = lambda ctx: claims
        try:
            params = t.CallToolRequestParams(
                name="check_stock", arguments={"variant_id": "P0001-BLK-REG-M"}
            )
            return await server.call_tool(Ctx(), params)
        finally:
            srv.claims_of = original

    result = asyncio.run(go())
    assert not result.is_error, result
    assert threads and threads[0] != threading.main_thread().name
    assert len(threads) == 1


def _dispatch(
    server: Any, name: str, args: dict[str, Any], agent: str = "shopper-assistant"
) -> Any:
    """`call_tool` as the MCP layer runs it, with the caller's claims patched in."""
    import asyncio
    import time as _time

    from mcp import types as t

    from northwind_common.mcp_token import CATALOG_SERVER, Claims
    from northwind_mcp_catalog import server as srv

    claims = Claims(
        "shopper",
        "US",
        agent,
        "t",
        int(_time.time()) + 600,
        CATALOG_SERVER,
        "s-nl1",
    )

    async def go() -> Any:
        original = srv.claims_of
        srv.claims_of = lambda ctx: claims
        try:
            return await server.call_tool(
                object(), t.CallToolRequestParams(name=name, arguments=args)
            )
        finally:
            srv.claims_of = original

    return asyncio.run(go())


def _envelope(result: Any) -> dict[str, Any]:
    import json

    found: dict[str, Any] = json.loads(result.content[0].text)
    return found


@pytest.mark.parametrize(
    ("name", "args", "code"),
    [
        ("check_stock", {"variant_id": "P0009-NOPE-REG-M"}, "not_found"),
        ("check_stock", {"variant_id": "P0001-BLK-REG-M", "extra": 1}, None),
    ],
)
def test_a_down_reader_never_hides_a_non_transient_answer(
    name: str, args: dict[str, Any], code: str | None
) -> None:
    """Option B's re-review N-L1: with the database down, an error that retrying cannot fix is
    still returned as itself, not as `inventory_unavailable`."""
    from northwind_mcp_catalog.holds import unavailable
    from northwind_mcp_catalog.live import FixtureCatalog
    from northwind_mcp_catalog.server import CatalogServer
    from northwind_mcp_catalog.testing import KEY
    from northwind_mcp_catalog.tools import TOOLS

    class Down:
        def live(self, region: str) -> dict[str, int]:
            raise unavailable()

    result = _dispatch(
        CatalogServer(FixtureCatalog(), key=KEY, tools=TOOLS, holds=Down()), name, args
    )
    body = _envelope(result)
    assert result.is_error and body["code"] != "inventory_unavailable", body
    assert body["isRetryable"] is False, body
    if code:
        assert body["code"] == code, body


def test_a_down_reader_fails_only_the_calls_that_read_stock() -> None:
    from northwind_mcp_catalog.holds import unavailable
    from northwind_mcp_catalog.live import FixtureCatalog
    from northwind_mcp_catalog.server import CatalogServer
    from northwind_mcp_catalog.testing import KEY
    from northwind_mcp_catalog.tools import TOOLS

    class Down:
        def live(self, region: str) -> dict[str, int]:
            raise unavailable()

    server = CatalogServer(FixtureCatalog(), key=KEY, tools=TOOLS, holds=Down())
    priced = _dispatch(server, "eligible_products", {"price_max": 50000}, "shopper-query")
    assert not priced.is_error, priced  # a price-only filter reads no stock
    stocked = _dispatch(
        server, "eligible_products", {"price_max": 50000, "in_stock": True}, "shopper-query"
    )
    assert stocked.is_error and _envelope(stocked)["code"] == "inventory_unavailable"
    checked = _dispatch(server, "check_stock", {"variant_id": "P0001-BLK-REG-M"})
    body = _envelope(checked)
    assert (body["code"], body["isRetryable"]) == ("inventory_unavailable", True)
