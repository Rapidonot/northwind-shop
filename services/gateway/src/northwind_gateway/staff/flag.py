"""OPS-1: the one switch for the demo staff console. Off unless DEMO_STAFF_DASHBOARD is exactly
"1": unset, "0", "true" or anything else is off (decision 0028)."""

from __future__ import annotations

import os

DEMO_STAFF_ENV = "DEMO_STAFF_DASHBOARD"


def staff_enabled() -> bool:
    return os.environ.get(DEMO_STAFF_ENV) == "1"
