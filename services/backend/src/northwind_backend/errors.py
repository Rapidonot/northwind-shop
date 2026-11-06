"""Structured results for every port call (hard rule 10).

A call returns `Ok` or `ToolError`. An outage is a transient `ToolError`, and a query that found
nothing is an `Ok` holding an empty value, so "access failed" and "valid empty result" can't be
confused. `to_envelope()` gives the wire keys the tool design rules name.

Messages are fixed text. Nothing here echoes an argument, so a refused card number can't reach a log
through an error (hard rule 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


class ErrorCategory(StrEnum):
    TRANSIENT = "transient"
    VALIDATION = "validation"
    BUSINESS = "business"
    PERMISSION = "permission"


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T
    is_error: Literal[False] = False

    def to_envelope(self) -> dict[str, Any]:
        return {"isError": False, "value": self.value}


@dataclass(frozen=True, slots=True)
class ToolError:
    """`is_retryable` means the same call, with the same key, may succeed later. A soft decline is
    not retryable in this sense: its result is recorded, and a new attempt takes a new key."""

    error_category: ErrorCategory
    is_retryable: bool
    code: str
    message: str
    is_error: Literal[True] = True

    def to_envelope(self) -> dict[str, Any]:
        return {
            "isError": True,
            "errorCategory": self.error_category.value,
            "isRetryable": self.is_retryable,
            "code": self.code,
            "message": self.message,
        }


type Result[T] = Ok[T] | ToolError


def transient(code: str, message: str) -> ToolError:
    return ToolError(ErrorCategory.TRANSIENT, True, code, message)


def validation(code: str, message: str) -> ToolError:
    return ToolError(ErrorCategory.VALIDATION, False, code, message)


def business(code: str, message: str) -> ToolError:
    return ToolError(ErrorCategory.BUSINESS, False, code, message)


def permission(code: str, message: str) -> ToolError:
    return ToolError(ErrorCategory.PERMISSION, False, code, message)
