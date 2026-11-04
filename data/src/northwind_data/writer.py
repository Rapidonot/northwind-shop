"""Canonical serialization: same objects -> same bytes (FOUND-1 determinism)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from decimal import Decimal
from typing import Any


def _default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError(f"not serializable: {type(obj).__name__}")


def jsonl(records: Iterable[dict[str, Any]]) -> bytes:
    lines = [
        json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_default)
        for r in records
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def pretty(obj: Any) -> bytes:
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, default=_default)
    return (text + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
