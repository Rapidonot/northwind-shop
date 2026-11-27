"""SA-01 PR 1, security review F1 (hard rule 1, NFR-04): no card number in the gateway's access log.

A real uvicorn, with its default logging and access log, serves the gateway; the test reads the
`uvicorn.access` records as its handlers would format them. TestClient never reaches that logger,
which is why the shop's own tests could not catch this.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect as ws_connect

from northwind_common.cards import contains_card_data
from northwind_common.mcp_token import KEY_ENV
from northwind_gateway.access_log import (
    ACCESS_LOGGER,
    ERROR_LOGGER,
    REDACTED,
    DropQueryStrings,
    DropWebSocketQueryStrings,
    strip_query,
)
from northwind_gateway.app import app
from northwind_mcp_catalog import testing as catalog_testing

pytestmark = [pytest.mark.req("SA-01"), pytest.mark.req("NFR-04")]

PAN = "4242 4242 4242 4242"


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Capture]]:
    monkeypatch.setenv(KEY_ENV, catalog_testing.KEY.decode())
    port = catalog_testing.free_port()
    # uvicorn's own default log config, access log on, as `make dev` and Playwright start it.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", access_log=True)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("gateway failed to start")
        time.sleep(0.02)
    capture = Capture()
    for name in (ACCESS_LOGGER, ERROR_LOGGER):
        logging.getLogger(name).addHandler(capture)
    try:
        yield f"http://127.0.0.1:{port}", capture
    finally:
        for name in (ACCESS_LOGGER, ERROR_LOGGER):
            logging.getLogger(name).removeHandler(capture)
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize(
    "query",
    [
        {"q": PAN},
        {"department": PAN.replace(" ", "")},
        {"id": PAN.replace(" ", "-")},
        {PAN.replace(" ", ""): "x"},
        {"q": "a", "other": PAN},
    ],
)
def test_a_card_number_in_the_query_never_reaches_the_access_log(
    served: tuple[str, Capture], query: dict[str, str]
) -> None:
    base, capture = served
    path = "/api/product" if "id" in query else "/api/products"
    response = httpx.get(base + path, params=query, timeout=10)
    assert response.status_code == 422
    deadline = time.monotonic() + 5
    while not capture.lines and time.monotonic() < deadline:
        time.sleep(0.02)
    assert capture.lines, "no access record was captured"
    for line in capture.lines:
        assert not contains_card_data(line), line
        assert "4242" not in line, line
    assert any(path + REDACTED in line for line in capture.lines)


def test_a_card_number_in_a_websocket_query_never_reaches_the_error_log(
    served: tuple[str, Capture],
) -> None:
    """Re-review N1: uvicorn logs a refused WebSocket handshake on `uvicorn.error`, query too."""
    base, capture = served
    url = base.replace("http://", "ws://") + "/api/x?q=" + PAN.replace(" ", "")
    with pytest.raises(InvalidStatus):
        ws_connect(url, open_timeout=5).close()
    deadline = time.monotonic() + 5
    while not any("WebSocket" in line for line in capture.lines) and time.monotonic() < deadline:
        time.sleep(0.02)
    handshake = [line for line in capture.lines if "WebSocket" in line]
    assert handshake, capture.lines
    for line in capture.lines:
        assert not contains_card_data(line), line
    assert any("/api/x" + REDACTED in line for line in handshake)


def test_the_filters_are_on_the_access_and_error_loggers() -> None:
    access = logging.getLogger(ACCESS_LOGGER).filters
    assert sum(type(f) is DropQueryStrings for f in access) == 1
    error = logging.getLogger(ERROR_LOGGER).filters
    assert sum(type(f) is DropWebSocketQueryStrings for f in error) == 1


def test_other_error_records_are_left_whole() -> None:
    record = logging.LogRecord(ERROR_LOGGER, logging.ERROR, "", 0, "Bad %s", ("a?b",), None)
    assert DropWebSocketQueryStrings().filter(record)
    assert record.getMessage() == "Bad a?b"


@pytest.mark.parametrize(
    ("path", "logged"),
    [
        ("/api/products?q=4242424242424242", "/api/products" + REDACTED),
        ("/api/products?", "/api/products" + REDACTED),
        ("/api/healthz", "/api/healthz"),
        ("/api/a?b?c", "/api/a" + REDACTED),
    ],
)
def test_strip_query(path: str, logged: str) -> None:
    assert strip_query(path) == logged


def test_a_record_with_no_args_loses_its_query_too() -> None:
    record = logging.LogRecord(
        ACCESS_LOGGER, logging.INFO, "", 0, "GET /x?q=1 HTTP/1.1", None, None
    )
    assert DropQueryStrings().filter(record)
    assert record.getMessage() == "GET /x" + REDACTED
