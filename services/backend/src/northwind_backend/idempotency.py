"""Idempotency for state-changing calls (hard rule 9, CHK-05).

The same key with the same request returns the first result and runs no second effect. The same key
with a different request is refused, because returning the first result would hide a caller's bug.

Every definite result is recorded, business errors included. A transient failure is not: nothing
happened, and `is_retryable` has to be true of a retry with the same key.

Only a hash of the request is kept. The lock is held across the effect, so concurrent replays still
run it once.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import RLock
from typing import Any

from northwind_backend.errors import ErrorCategory, Result, ToolError, validation

IDEMPOTENCY_CONFLICT = validation(
    "idempotency_conflict", "This idempotency key was already used for a different request."
)


def fingerprint(operation: str, request: Mapping[str, Any]) -> str:
    body = json.dumps({"operation": operation, "request": request}, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _Entry:
    fingerprint: str
    result: Any


class IdempotencyStore:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = RLock()

    def exclusive[T](self, read: Callable[[], T]) -> T:
        """Runs `read` while no effect is in progress, which is what a dry run needs."""
        with self._lock:
            return read()

    def run[T](self, key: str, request_hash: str, effect: Callable[[], Result[T]]) -> Result[T]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                if entry.fingerprint != request_hash:
                    return IDEMPOTENCY_CONFLICT
                first: Result[T] = entry.result
                return first
            result = effect()
            if not _is_transient(result):
                self._entries[key] = _Entry(request_hash, result)
            return result


def _is_transient(result: Result[Any]) -> bool:
    return isinstance(result, ToolError) and result.error_category is ErrorCategory.TRANSIENT
