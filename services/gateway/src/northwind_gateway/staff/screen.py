"""OPS-1: the screen every string passes before it leaves `/api/staff` (hard rules 1, 4 and 7).

The console shows full history to anyone (the user's decision, decision 0028), so what it must never
show is screened at the edge, on top of what each store already refuses or screens:
- card data, in the text or its NFKC form (`northwind_common.cards`), becomes `CARD_WITHHELD`;
- a key-shaped secret, by the same patterns the cassette scanner uses, becomes `SECRET_WITHHELD`;
- an internal-only canary from the answer key (hard rule 7) becomes `INTERNAL_WITHHELD`.
The key patterns are copied from `northwind_llm_replay.scan`, not imported: the proxy package is not
a gateway dependency and must not ship in the hosted server. A test pins the copy to the original
and proves every string `scan_text` flags is withheld here.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from northwind_backend.fakes.catalog import FIXTURES
from northwind_common.cards import contains_card_data

CARD_WITHHELD = "[withheld: card data]"
SECRET_WITHHELD = "[withheld: secret]"  # noqa: S105 - a placeholder, not a secret
INTERNAL_WITHHELD = "[withheld: internal]"
ANSWER_KEY = FIXTURES / "_answer_key.json"

# Copied from northwind_llm_replay.scan._KEY_PATTERNS (pinned equal by test_ops1_screen.py).
KEY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic-api-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")),
    ("generic-sk-key", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}")),
    ("bearer-token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/\-]{16,}")),
    (
        "x-api-key-header",
        re.compile(
            r"""(?i)x-api-key["']?\s*[:=]\s*["']?[^\s"',}]+"""
            r"""|(?i:x-api-key)\\+["']\s*[:=]\s*\\+["'](?!\\)[^\s"',}\\]+"""
        ),
    ),
)


@cache
def canaries(path: Path = ANSWER_KEY) -> frozenset[str]:
    """The answer key's internal-only canaries. A missing key file is no canaries: the stores the
    console reads never hold internal documents; this is the last line, not the only one."""
    if not path.exists():
        return frozenset()
    entries = json.loads(path.read_text(encoding="utf-8")).get("internal_only", [])
    return frozenset(str(e["canary"]) for e in entries)


def screen_text(text: str) -> str:
    if contains_card_data(text) or contains_card_data(unicodedata.normalize("NFKC", text)):
        return CARD_WITHHELD
    if any(
        pattern.search(line) for line in text.splitlines() or [text] for _, pattern in KEY_PATTERNS
    ):
        return SECRET_WITHHELD
    if any(canary in text for canary in canaries()):
        return INTERNAL_WITHHELD
    return text


def screen(value: Any) -> Any:
    """Every string in a JSON-shaped value, keys included, screened; other values unchanged."""
    if isinstance(value, str):
        return screen_text(value)
    if isinstance(value, Mapping):
        return {screen_text(str(k)): screen(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [screen(v) for v in value]
    return value
