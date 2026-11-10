"""The structured error every Northwind tool returns (hard rule 10; CC-10).

An error result carries `isError`, `errorCategory` (transient | validation | business |
permission), `isRetryable` and `message`, plus a stable `code`. Messages are fixed text chosen by
the server: they never echo an argument, an exception or a connection string. A valid empty
result is not an error, so "access failed" and "found nothing" stay distinct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, get_args

ErrorCategory = Literal["transient", "validation", "business", "permission"]
CATEGORIES: frozenset[str] = frozenset(get_args(ErrorCategory))
ENVELOPE_KEYS = frozenset({"isError", "errorCategory", "isRetryable", "message", "code"})


@dataclass(frozen=True)
class ToolError(Exception):
    category: ErrorCategory
    retryable: bool
    code: str
    message: str

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown error category: {self.category!r}")
        if not isinstance(self.retryable, bool):
            raise TypeError("retryable must be a bool")
        if not self.code or not self.code.replace("_", "").isalnum() or not self.code.islower():
            raise ValueError("code must be lower_snake_case")
        if not self.message.strip():
            raise ValueError("message must not be empty")

    def to_envelope(self) -> dict[str, Any]:
        return {
            "isError": True,
            "errorCategory": self.category,
            "isRetryable": self.retryable,
            "message": self.message,
            "code": self.code,
        }
