"""The recording windows' hold guard (row #18; the user's decision, 2026-11-26).

Every recording window starts with `uv run python -m northwind_backend.inventory.live_holds`. It
counts the holds that are live now in BE-04's inventory tables and exits 1 when any is, so no
leftover hold is baked into a recording: the catalog subtracts live holds from every stock figure
(row #18, step 0 (c)), so a hold would change what the model is shown.

A hold is live exactly when `postgres._AVAILABLE` counts it: not swept, its reservation `held`, and
`expires_at` after now (`expires_at == now` is not live). The time is this process's clock in UTC,
passed as a parameter; SQL never calls `now()`, as in `postgres.py`.

Exit codes (step 0's security review L5: a database with no inventory tables is not reported as a
plain "0"):
- 0: the tables exist and no hold is live;
- 1: some hold is live, so the window must not open;
- 2: not checked (the database can't be reached, or any other database error);
- 3: BE-04's tables don't exist in this database: its schema isn't applied there, or
  `DATABASE_URL` names the wrong database. **Exit 3 always blocks a window** (the user's decision,
  2026-11-27): only a real 0 opens one. The line names the database.
It reads only; it never sweeps or releases a hold.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import errors as pg_errors

DEFAULT_DATABASE_URL = "postgresql://northwind:northwind-local@localhost:5432/northwind"

_COUNT_LIVE = """
SELECT COUNT(*)
FROM inventory_holds h
JOIN inventory_reservations r ON r.idempotency_key = h.idempotency_key
WHERE NOT h.swept AND r.status = 'held' AND r.expires_at > %(now)s
"""


def count_live(connect: Callable[[], psycopg.Connection[Any]], now: datetime) -> int | None:
    """How many holds are live at `now`, or None when BE-04's tables don't exist."""
    if now.tzinfo is None:
        raise ValueError("now must be an aware time")
    try:
        with connect() as conn:
            row = conn.execute(_COUNT_LIVE, {"now": now}).fetchone()
    except pg_errors.UndefinedTable:
        return None
    return int(row[0]) if row else 0


def main() -> int:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    database = "?"
    try:
        # Inside the `try`: a malformed URL is "not checked" (2), and its parse error, which can
        # quote part of the URL, is never printed: only its class (re-review N1).
        database = str(psycopg.conninfo.conninfo_to_dict(url).get("dbname", "?"))
        count = count_live(lambda: psycopg.connect(url, connect_timeout=3), datetime.now(UTC))
    except psycopg.Error as error:
        print(
            f"live holds: not checked in database {database!r} ({type(error).__name__})",
            file=sys.stderr,
        )
        return 2
    if count is None:
        print(f"live holds: none possible, BE-04's tables do not exist in database {database!r}")
        return 3
    print(f"live holds: {count} in database {database!r}")
    return 0 if count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
