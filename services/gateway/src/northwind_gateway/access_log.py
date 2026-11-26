"""No query string in the gateway's access log (SA-01 PR 1; hard rule 1, NFR-04: no PAN in logs).

uvicorn's access logger writes each request's path with its query string, from outside the app, so
a card number a shopper types into the shop's search box (`GET /api/products?q=…`) would reach the
log even though the route refuses it (security review F1). This filter replaces every query string
in an access record with a fixed marker before any handler formats it. It drops all queries, not
only those that look like card data: no detector then decides what may be logged.

`install()` runs when `northwind_gateway.app` is imported, which uvicorn does after it configures
logging, and again at startup, so the filter is on the logger uvicorn writes to.
"""

from __future__ import annotations

import logging

ACCESS_LOGGER = "uvicorn.access"
# uvicorn logs a WebSocket handshake's path, query included, on its error logger (re-review N1).
ERROR_LOGGER = "uvicorn.error"
REDACTED = "?[query removed]"


def strip_query(path: str) -> str:
    head, sep, _ = path.partition("?")
    return head + REDACTED if sep else head


class DropQueryStrings(logging.Filter):
    """uvicorn logs `'%s - "%s %s HTTP/%s" %d'` with args (client, method, path, version, status).
    Any string argument holding a `?` loses everything from it; a message with no args is
    rewritten the same way."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                strip_query(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        elif isinstance(record.msg, str) and not record.args:
            record.msg = strip_query(record.msg)
        return True


class DropWebSocketQueryStrings(DropQueryStrings):
    """On `uvicorn.error`, only the WebSocket handshake lines (`'%s - "WebSocket %s" 403'` and its
    siblings) lose their query strings; other error records are left whole."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and "WebSocket" in record.msg:
            return super().filter(record)
        return True


def install() -> None:
    for name, kind in (
        (ACCESS_LOGGER, DropQueryStrings),
        (ERROR_LOGGER, DropWebSocketQueryStrings),
    ):
        logger = logging.getLogger(name)
        if not any(type(f) is kind for f in logger.filters):
            logger.addFilter(kind())
