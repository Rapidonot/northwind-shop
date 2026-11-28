"""OPS-1 C1, C2: the demo staff console exists only with DEMO_STAFF_DASHBOARD=1; only GET answers.

Off (the default): every staff path, under every method, is the same 404 as a path that does not
exist, and nothing is read first. On: each view answers GET with 200 and never cached; any other
method is that same 404. Every route under /api/staff, a later stream included, carries the guard.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from northwind_gateway.app import app
from northwind_gateway.staff import views
from northwind_gateway.staff.flag import DEMO_STAFF_ENV, staff_enabled
from northwind_gateway.staff.routes import ALL_METHODS, STAFF_PREFIX, guard

pytestmark = pytest.mark.req("OPS-1")

VIEWS = ("overview", "system", "activity", "handoffs", "orders", "stock")
UNKNOWN = "/api/no-such-path-anywhere"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _all_routes(routes: Iterable[Any] = app.routes) -> Iterator[Any]:
    """Every route, included routers opened up (FastAPI 0.16x nests them as `_IncludedRouter`)."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _all_routes(inner.routes)
        else:
            yield route


def _staff_routes() -> list[APIRoute]:
    routes = [
        r for r in _all_routes() if isinstance(r, APIRoute) and r.path.startswith(STAFF_PREFIX)
    ]
    assert routes, "no staff route found: the walk itself is broken"
    return routes


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", "TRUE", " 1", "1 "])
def test_off_unless_exactly_1(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(DEMO_STAFF_ENV, raising=False)
    else:
        monkeypatch.setenv(DEMO_STAFF_ENV, value)
    assert staff_enabled() is False


@pytest.mark.parametrize("value", [None, "0", "true"])
def test_off_every_path_and_method_is_the_unknown_path_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(DEMO_STAFF_ENV, raising=False)
    else:
        monkeypatch.setenv(DEMO_STAFF_ENV, value)

    def never() -> views.Sources:
        raise AssertionError("a store was read with the console off")

    app.dependency_overrides[views.sources] = never
    unknown = client.get(UNKNOWN)
    assert unknown.status_code == 404
    for route in _staff_routes():
        for method in ALL_METHODS:
            reply = client.request(method, route.path)
            assert reply.status_code == 404, (method, route.path)
            if method != "HEAD":
                assert reply.json() == unknown.json(), (method, route.path)


def test_on_each_view_answers_get_uncached_and_nothing_else(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEMO_STAFF_ENV, "1")
    unknown = client.get(UNKNOWN).json()
    for view in VIEWS:
        reply = client.get(f"{STAFF_PREFIX}/{view}")
        assert reply.status_code == 200, view
        assert reply.headers["cache-control"] == "no-store"
        assert "set-cookie" not in reply.headers  # no sign-in, no session
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            other = client.request(method, f"{STAFF_PREFIX}/{view}")
            assert other.status_code == 404 and other.json() == unknown, (method, view)


def test_every_staff_route_carries_the_guard() -> None:
    routes = _staff_routes()
    assert {r.path for r in routes} == {f"{STAFF_PREFIX}/{v}" for v in VIEWS}
    for route in routes:
        assert any(dep.call is guard for dep in route.dependant.dependencies), route.path
        # GET only (CHK-01's route pin): every other method is StaffGate's unknown-path 404.
        assert set(route.methods or ()) == {"GET"}, route.path


def test_no_other_route_mentions_staff() -> None:
    """A console route outside the prefix would escape the walk above."""
    for route in _all_routes():
        path = getattr(route, "path", "")
        assert "staff" not in path or path.startswith(STAFF_PREFIX), path
