"""Row #18, step 0 (b) and (c): the catalog's stock reads less BE-04's live holds, and the cart's
out-of-stock alternatives are BE-04's substitutes (row #25).

Holds come from BE-04's own `InventoryFake` through a small adapter, so "live" means exactly what
BE-04 means by it (held, not swept, `expires_at` after now), not a copy of that rule. The
import-closure test pins the user's condition on the new dependency (2026-11-26): mcp-catalog may
use northwind-backend only for its pure substitutes module.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from northwind_backend.inventory import InventoryFake, Line
from northwind_backend.inventory import substitutes as subs
from northwind_backend.inventory.nodes import region_nodes
from northwind_backend.ports import PriceQuote
from northwind_common.catalog_tool import labelled_result
from northwind_common.errors import ToolError
from northwind_common.mcp_token import CATALOG_SERVER, Claims
from northwind_mcp_catalog import cart, tools
from northwind_mcp_catalog.holds import NoHolds, unavailable
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.testing import KEY

pytestmark = pytest.mark.req("BE-04")

REGION = "US"  # two nodes, DC-EAST and DC-WEST, so a region's figure is a sum
V = "P0001-BLK-REG-M"
T0 = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class FakeHolds:
    """BE-04's `InventoryFake` seen as the catalog's `Holds` port: on hand less available, per
    node of the region, summed by variant."""

    def __init__(self, inventory: InventoryFake) -> None:
        self.inventory = inventory
        self.nodes = region_nodes()
        self.reads = 0

    def live(self, region: str) -> Mapping[str, int]:
        self.reads += 1
        held: dict[str, int] = {}
        nodes = set(self.nodes.get(region, ()))
        for (variant, node), row in self.inventory.state.rows.items():
            if node not in nodes:
                continue
            available = self.inventory.available(variant, node)
            units = row.on_hand - available.value  # type: ignore[union-attr]
            if units:
                held[variant] = held.get(variant, 0) + units
        return held


def claims(region: str = REGION) -> Claims:
    return Claims(
        aud="shopper",
        region=region,
        agent="shopper-assistant",
        turn="t-1",
        exp=int(time.time()) + 600,
        srv=CATALOG_SERVER,
        sid="s-18",
    )


def setup() -> tuple[CatalogServer, InventoryFake, Clock]:
    clock = Clock()
    inventory = InventoryFake(clock)
    server = CatalogServer(FixtureCatalog(), key=KEY, tools=tools.TOOLS, holds=FakeHolds(inventory))
    return server, inventory, clock


def hold(inventory: InventoryFake, key: str, variant: str, quantity: int) -> None:
    done = inventory.reserve(
        idempotency_key=key,
        order_id="O900001",
        region=REGION,
        postal_code="10001",
        lines=[Line(variant, quantity)],
    )
    assert getattr(done, "value", None) is not None, done


def stock_of(server: CatalogServer, variant: str = V, region: str = REGION) -> int:
    result = asyncio.run(tools.check_stock(server, claims(region), {"variant_id": variant}))
    text = result.content[0].text  # type: ignore[union-attr]
    found = labelled_result(text, bool(result.is_error))
    assert found is not None, text
    value: dict[str, Any] = found[1]
    assert value["in_stock"] is (value["on_hand"] > 0)
    return int(value["on_hand"])


# ---- (c) live holds --------------------------------------------------------------------------


def test_a_live_hold_lowers_check_stock_by_its_units() -> None:
    server, inventory, _ = setup()
    before = stock_of(server)
    assert before == server.catalog.stock(V, REGION) and before >= 3
    hold(inventory, "hold-key-1", V, 2)
    assert stock_of(server) == before - 2


def test_an_expired_hold_and_one_at_its_expiry_do_not_count() -> None:
    server, inventory, clock = setup()
    before = stock_of(server)
    hold(inventory, "hold-key-2", V, 2)
    clock.now = T0 + timedelta(minutes=15)  # `expires_at == now` is not live (BE-04)
    assert stock_of(server) == before
    clock.now = T0 + timedelta(hours=1)
    assert stock_of(server) == before


def test_a_released_or_committed_hold_does_not_count_as_held() -> None:
    server, inventory, _ = setup()
    before = stock_of(server)
    hold(inventory, "hold-key-3", V, 2)
    inventory.release(idempotency_key="hold-key-3")
    assert stock_of(server) == before


def test_a_hold_in_another_region_does_not_count() -> None:
    server, inventory, _ = setup()
    uk = stock_of(server, region="UK")
    hold(inventory, "hold-key-4", V, 2)  # a US hold
    assert stock_of(server, region="UK") == uk


def test_stock_never_goes_below_zero() -> None:
    server, inventory, _ = setup()

    class Over:
        def live(self, region: str) -> Mapping[str, int]:
            return {V: 10_000}

    server.holds = Over()
    assert stock_of(server) == 0


def test_the_cart_add_check_sees_holds() -> None:
    server, inventory, _ = setup()
    on_hand = stock_of(server)
    hold(inventory, "hold-key-5", V, 3)
    assert cart._on_hand(server, V, REGION) == stock_of(server) == on_hand - 3


def test_the_in_stock_filter_sees_holds() -> None:
    server, _, _ = setup()
    from northwind_mcp_catalog.live import eligible_products

    every = eligible_products(server.catalog, REGION, currency="USD", in_stock=True)
    product = every[0]
    variants = [v.variant_id for v in server.catalog.variants_of(product)]
    held = {v: server.catalog.stock(v, REGION) or 0 for v in variants}
    narrowed = eligible_products(server.catalog, REGION, currency="USD", in_stock=True, held=held)
    assert product not in narrowed
    assert set(narrowed) == set(every) - {product}


def test_the_default_port_holds_nothing() -> None:
    server = CatalogServer(FixtureCatalog(), key=KEY, tools=tools.TOOLS)
    assert isinstance(server.holds, NoHolds)
    assert stock_of(server) == server.catalog.stock(V, REGION)


# ---- (b) the cart's alternatives are BE-04's substitutes ---------------------------------------


def _expected(server: CatalogServer, variant: str, wanted: int) -> list[str]:
    def price(v: str, r: str) -> PriceQuote | None:
        found = server.catalog.price(v, r)
        return None if found is None else PriceQuote(v, r, found.minor_units, found.currency)

    held = server.holds.live(REGION)
    chosen = subs.find(
        variant,
        REGION,
        wanted,
        lambda v: max(0, (server.catalog.stock(v, REGION) or 0) - held.get(v, 0)),
        catalog=subs.SubstituteCatalog.load(),
        price=price,
    )
    return [s.variant_id for s in chosen]


@pytest.mark.parametrize("variant", [V, "P0002-CHR-REL-2XL", "P0010-CHR-REG-L"])
def test_the_alternatives_are_bes_substitutes_in_order(variant: str) -> None:
    server, _, _ = setup()
    server.catalog.set_stock(variant, REGION, 0)
    refused = server.catalog.variant(variant)
    assert refused is not None
    got = [a["variant_id"] for a in cart.alternatives(server, claims(), REGION, refused, 1)]
    assert got == _expected(server, variant, 1)
    assert len(got) <= subs.MAX_SUBSTITUTES == cart.MAX_ALTERNATIVES


def test_a_held_candidate_is_not_offered() -> None:
    server, inventory, _ = setup()
    server.catalog.set_stock(V, REGION, 0)
    refused = server.catalog.variant(V)
    assert refused is not None
    first = [a["variant_id"] for a in cart.alternatives(server, claims(), REGION, refused, 1)]
    assert first
    top = first[0]
    # Hold all of it in the catalog's view: the region's units, at whatever nodes have them.
    units = server.catalog.stock(top, REGION) or 0
    for node in region_nodes()[REGION]:
        row = inventory.state.rows.get((top, node))
        if row is not None and row.on_hand:
            hold(inventory, f"hold-{node}-top", top, row.on_hand)
    assert server.on_hand(top, REGION) == 0, units
    again = [a["variant_id"] for a in cart.alternatives(server, claims(), REGION, refused, 1)]
    assert top not in again
    assert again == _expected(server, V, 1)


def test_the_live_price_is_what_the_band_reads() -> None:
    """Hard rule 2: the band is applied to this server's live price, not the fixture table."""
    server, _, _ = setup()
    server.catalog.set_stock(V, REGION, 0)
    refused = server.catalog.variant(V)
    assert refused is not None
    first = [a["variant_id"] for a in cart.alternatives(server, claims(), REGION, refused, 1)]
    assert first
    base = server.catalog.price(V, REGION)
    assert base is not None
    for variant in first:  # push every offered one far out of the ±20% band, live
        units = base.minor_units * 3
        server.catalog.set_price(variant, REGION, f"{units // 100}.{units % 100:02d}", "USD")
    again = [a["variant_id"] for a in cart.alternatives(server, claims(), REGION, refused, 1)]
    assert not set(first) & set(again)


def test_one_holds_read_per_refusal() -> None:
    server, _, _ = setup()
    server.catalog.set_stock(V, REGION, 0)
    refused = server.catalog.variant(V)
    assert refused is not None
    holds = server.holds
    assert isinstance(holds, FakeHolds)
    before = holds.reads
    cart.alternatives(server, claims(), REGION, refused, 1)
    assert holds.reads - before == 1


def test_the_model_visible_tool_list_is_unchanged() -> None:
    """Step 0 (b) and (c) keep the tool list a model can see as it was, `add_to_cart`'s
    description included: the alternatives' source changed, not what any tool says (sha256 at
    `bcc135b`). Row #18 after the main merge (the user's stop-5 yes, 2026-11-27): SA-01 added the
    portal-only specs to the server's registry, which no model holds a token for, so the pin covers
    `TOOLS` without `PORTAL_SPECS`. The next test holds that only portal-only tools are left out."""
    import hashlib

    from northwind_mcp_catalog.portal import PORTAL_SPECS

    visible = sorted(n for n in tools.TOOLS if n not in PORTAL_SPECS)
    dump = [tools.TOOLS[n].tool.model_dump(mode="json") for n in visible]
    digest = hashlib.sha256(json.dumps(dump, sort_keys=True).encode()).hexdigest()
    assert digest == "fc86e9e8ab4944583a460d96de5d091fde2728dba3c282fa75ac90b3a8a87756"


def test_the_pin_leaves_out_only_the_portal_only_tools() -> None:
    """What the pin above skips is exactly the portal-only set in `tool_scopes`, which no model
    agent's scope names: a model tool cannot leave the pin by being added to `PORTAL_SPECS`."""
    from northwind_common import tool_scopes
    from northwind_mcp_catalog.portal import PORTAL_SPECS

    assert set(PORTAL_SPECS) == set(tool_scopes.PORTAL_ONLY_TOOLS)
    for agent, servers in tool_scopes.AGENT_TOOLS.items():
        if agent == "shopper-portal":
            continue
        for names in servers.values():
            assert not set(names) & set(PORTAL_SPECS), agent


# ---- the dependency is the substitutes module only (the user's condition 2) ------------------

MODEL_CODE = (
    "northwind_common.llm",
    "northwind_backend.agents",
    "northwind_assistant",
    "anthropic",
    "claude_agent_sdk",
)
FORBIDDEN = (*MODEL_CODE, "psycopg")
# What the substitutes module pulls in today, all pure (security review L4): a new module here is a
# change to the user's condition and fails this test until it is looked at.
BACKEND_ALLOWED = frozenset(
    {
        "northwind_backend",
        "northwind_backend.errors",
        "northwind_backend.fakes",
        "northwind_backend.fakes._base",
        "northwind_backend.fakes.catalog",
        "northwind_backend.idempotency",
        "northwind_backend.inventory",
        "northwind_backend.inventory.memory",
        "northwind_backend.inventory.model",
        "northwind_backend.inventory.nodes",
        "northwind_backend.inventory.substitutes",
        "northwind_backend.ports",
        "northwind_backend.recovery",
    }
)


def _loaded(*modules: str) -> list[str]:
    script = "import json, sys\n" + "".join(f"import {m}\n" for m in modules)
    script += "print(json.dumps(sorted(sys.modules)))\n"
    done = subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-c", script], capture_output=True, text=True, check=True, timeout=120
    )
    found: list[str] = json.loads(done.stdout)
    return found


def _bad(loaded: list[str], forbidden: tuple[str, ...]) -> list[str]:
    return [m for m in loaded for f in forbidden if m == f or m.startswith(f + ".")]


def test_the_catalog_servers_import_closure_has_no_model_code() -> None:
    """The whole server: no model code. `psycopg` may load here: `holds.py` imports it, and
    `test_tools.py` checks no other module of this package does. This test no longer forbids it
    (it did after security review L4); `test_the_substitutes_dependency_alone_is_pure` keeps
    `psycopg` out of the backend dependency (option B; advisor read 3, finding 2: put to the
    user)."""
    loaded = _loaded("northwind_mcp_catalog.app", "northwind_mcp_catalog.cart")
    assert not _bad(loaded, MODEL_CODE), _bad(loaded, MODEL_CODE)
    backend = {m for m in loaded if m.startswith("northwind_backend")}
    assert "northwind_backend.inventory.substitutes" in backend
    assert backend <= BACKEND_ALLOWED, sorted(backend - BACKEND_ALLOWED)


def test_the_substitutes_dependency_alone_is_pure() -> None:
    """What mcp-catalog takes from northwind-backend loads no driver and no model code, and only
    today's 13 pure modules (security review L4; the user's option 1 for condition 2)."""
    loaded = _loaded("northwind_backend.inventory.substitutes")
    assert not _bad(loaded, FORBIDDEN), _bad(loaded, FORBIDDEN)
    backend = {m for m in loaded if m.startswith("northwind_backend")}
    assert backend <= BACKEND_ALLOWED, sorted(backend - BACKEND_ALLOWED)


# ---- security review M1: a failing reader fails a write before the cart changes ---------------


class FailsAfter:
    """Reads succeed `ok` times, then every read raises the reader's transient error."""

    def __init__(self, ok: int) -> None:
        self.ok, self.reads = ok, 0

    def live(self, region: str) -> Mapping[str, int]:
        self.reads += 1
        if self.reads > self.ok:
            raise unavailable()
        return {}


def write(server: CatalogServer, name: str, **args: Any) -> Any:
    spec = tools.TOOLS[name]

    async def go() -> Any:
        key = f"m1-key-{uuid.uuid4().hex[:12]}"
        return await spec.handler(server, claims(), {**args, "idempotency_key": key})

    return asyncio.run(go())


def lines(server: CatalogServer) -> dict[str, int]:
    with server.carts.open("s-18", REGION, create=False) as found:
        return dict(found.lines)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("add_to_cart", {"variant_id": V, "quantity": 2}),
        ("set_cart_quantity", {"variant_id": V, "quantity": 3}),
        ("set_cart_quantity", {"variant_id": V, "quantity": 1}),
        ("remove_from_cart", {"variant_id": V}),
    ],
)
def test_a_reader_that_fails_leaves_the_cart_as_it_was(name: str, args: dict[str, Any]) -> None:
    server = CatalogServer(FixtureCatalog(), key=KEY, tools=tools.TOOLS)
    if name != "add_to_cart":
        assert not write(server, "add_to_cart", variant_id=V, quantity=2).is_error
    before = lines(server)
    server.holds = FailsAfter(0)
    with pytest.raises(ToolError) as raised:
        write(server, name, **args)
    assert raised.value.code == "inventory_unavailable" and raised.value.retryable
    assert lines(server) == before


@pytest.mark.parametrize("ok", [1, 2])
def test_a_write_reads_holds_once_so_a_later_failure_cannot_follow_it(ok: int) -> None:
    """The reading is taken before the write; the result's cart view reuses it, so a reader that
    would fail on a second read cannot report a failure after the cart has changed."""
    server = CatalogServer(FixtureCatalog(), key=KEY, tools=tools.TOOLS)
    server.holds = FailsAfter(ok)
    result = write(server, "add_to_cart", variant_id=V, quantity=2)
    assert not result.is_error
    assert lines(server) == {V: 2}
    assert server.holds.reads == 1


def test_get_cart_reads_holds_once_for_every_line() -> None:
    """Re-review N3: one reading for the whole view, so its lines agree."""
    server = CatalogServer(FixtureCatalog(), key=KEY, tools=tools.TOOLS)
    for variant in (V, "P0002-CHR-REL-2XL", "P0010-CHR-REG-L"):
        assert not write(server, "add_to_cart", variant_id=variant, quantity=1).is_error
    server.holds = FailsAfter(10)
    reads = server.holds.reads
    spec = tools.TOOLS["get_cart"]

    async def go() -> Any:
        return await spec.handler(server, claims(), {})

    result = asyncio.run(go())
    assert not result.is_error
    assert len(lines(server)) == 3
    assert server.holds.reads - reads == 1
