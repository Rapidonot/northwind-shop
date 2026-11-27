"""OPS-1: the demo staff console's routes: `GET /api/staff/<view>`, read-only, no sign-in.

With DEMO_STAFF_DASHBOARD off (the default), every method on every staff path is the same 404 as a
path that does not exist, so nothing tells a caller the console is there. With it on, only GET
answers; any other method is that same 404. A stream, when part 2 adds one, registers through
`staff_route` and so gets the same guard (test_ops1_flag.py walks the app's routes for it).
Every response is screened (`screen.screen`) and never cached.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from northwind_gateway.staff import views
from northwind_gateway.staff.flag import staff_enabled
from northwind_gateway.staff.screen import screen

STAFF_PREFIX = "/api/staff"
ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
NO_STORE = {"Cache-Control": "no-store"}

router = APIRouter()
Src = Annotated[views.Sources, Depends(views.sources)]


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


def guard(request: Request) -> None:
    """Off: 404, before anything is read. On: GET only."""
    if not staff_enabled() or request.method != "GET":
        raise not_found()


def staff_route(view: str, build: Callable[[views.Sources], dict[str, Any]]) -> None:
    @router.api_route(
        f"{STAFF_PREFIX}/{view}",
        methods=ALL_METHODS,
        include_in_schema=False,
        dependencies=[Depends(guard)],
    )
    def read(src: Src) -> JSONResponse:
        return JSONResponse(content=screen(build(src)), headers=NO_STORE)

    read.__name__ = f"staff_{view}"


for _view, _build in views.VIEWS.items():
    staff_route(_view, _build)
