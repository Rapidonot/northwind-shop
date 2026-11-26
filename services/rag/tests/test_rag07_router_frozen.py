"""RAG-07: the router is frozen at `9c42876` before the held-out set is scored (row #31).

The user's decision (2026-11-26): no router change from here; the held-out set is scored once,
with no fix round. A change to the router section is a new decision for the user: update the pin
only with their answer.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("RAG-07")

QUERY = Path(__file__).resolve().parents[1] / "src/northwind_rag/query.py"
START = "# ---- which indexes a message asks about (RAG-07)"
ROUTER_SHA256 = "0caf738cd812c9913ec4cf9dc6e36ce1d8f6a9496389aff47caaa5eced0ff213"


def test_the_router_section_is_frozen() -> None:
    source = QUERY.read_text(encoding="utf-8")
    assert source.count(START) == 1
    section = source[source.index(START) :]
    assert hashlib.sha256(section.encode()).hexdigest() == ROUTER_SHA256, "router change: the user"
