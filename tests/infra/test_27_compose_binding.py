"""M18, bundled into row #27 (decision 0021 rule 6): the local Postgres is published on the loopback
interface only, never on every interface of the machine (it holds the lab's data and a fixed local
password). The running stack changes only after the user's `make down` / `make up` from the runner
clone, after runner-sync: `make up` reads the clone's copy of this file."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("FOUND-0")

COMPOSE = Path(__file__).resolve().parents[2] / "infra" / "docker-compose.yml"
_PORT_LINE = re.compile(r'^\s*-\s*"(?P<mapping>[^"]*:5432)"\s*$', re.MULTILINE)


def test_postgres_is_published_on_loopback_only() -> None:
    mappings = [m["mapping"] for m in _PORT_LINE.finditer(COMPOSE.read_text(encoding="utf-8"))]
    assert mappings == ["127.0.0.1:${POSTGRES_PORT:-5432}:5432"]


def test_no_other_port_is_published() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert text.count("ports:") == 1
