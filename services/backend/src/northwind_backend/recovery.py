"""Local recovery of transient failures, and a real deadline on a port call (BE-02, row #20).

`call_with_recovery` retries only transient, retryable errors, with the same call (so the same
idempotency key), at most `max_attempts` times. Validation, business and permission errors are tried
once. The view inside `call` audits each attempt, so a retry is visible as its own attempt.

A policy can narrow that to `retry_codes` (row #21). `WRITE_POLICY`, for every state-changing call
an agent makes, retries only the two codes that mean nothing ran: `service_unavailable` (refused
before anything) and `audit_unavailable` (the attempt wasn't written, so the call wasn't run). A
`timeout`, `unexpected_error` or `audit_incomplete` on a write may have taken effect, so it never
leads to a second call: the caller records the action as uncertain, and it is settled as BE-08
settles it.

`with_deadline` runs a synchronous call in a worker thread, so the event loop is never blocked and
the deadline really fires. On expiry it sets the call's `CANCEL` event and then waits for the worker
to return, so the call's one outcome is written before the caller moves on: there is no late
outcome. A fake's `hang` waits on that event before its effect and, once it is set, returns
`TIMEOUT` with no effect, which is what the caller gets.

Known limit: this relies on the call being cancellable, which the fakes' `hang` is. A real adapter
stuck in code that ignores `CANCEL` would keep the caller waiting; that case, a hung call that
completes late, is an open item owned by PAY-08 (P4).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass

from northwind_backend.errors import UNEXPECTED, ErrorCategory, Result, ToolError

log = logging.getLogger(__name__)

# The current call's cancel event. Set before the worker task is created, because `ensure_future`
# copies the context at that moment and `to_thread` copies the task's context into the worker (S3).
CANCEL: ContextVar[threading.Event | None] = ContextVar("northwind_cancel", default=None)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    deadline_seconds: float | None = 5.0
    # The wait before attempt 2, 3, ...; a missing entry means no wait.
    backoff_seconds: tuple[float, ...] = (0.05, 0.2)
    # None retries every transient, retryable error; a set retries only those codes.
    retry_codes: frozenset[str] | None = None


# Nothing ran under either code, so the same call with the same key is a first attempt again.
NOTHING_RAN = frozenset({"service_unavailable", "audit_unavailable"})
WRITE_POLICY = RetryPolicy(retry_codes=NOTHING_RAN)


@dataclass(frozen=True, slots=True)
class Recovered[T]:
    result: Result[T]
    attempts: int


def retryable(result: Result[object], codes: frozenset[str] | None = None) -> bool:
    return (
        isinstance(result, ToolError)
        and result.error_category is ErrorCategory.TRANSIENT
        and result.is_retryable
        and (codes is None or result.code in codes)
    )


def _guarded[T](call: Callable[[], Result[T]]) -> Callable[[], Result[T]]:
    def run() -> Result[T]:
        try:
            return call()
        except Exception as error:  # noqa: BLE001 - its text must go nowhere (hard rule 1)
            log.error("port call failed: %s", type(error).__name__)
            return UNEXPECTED

    return run


async def with_deadline[T](call: Callable[[], Result[T]], seconds: float) -> Result[T]:
    cancel = threading.Event()
    token = CANCEL.set(cancel)
    try:
        task = asyncio.ensure_future(asyncio.to_thread(_guarded(call)))
    finally:
        CANCEL.reset(token)
    try:
        return await asyncio.wait_for(asyncio.shield(task), seconds)
    except TimeoutError:
        cancel.set()
        # The worker writes its own outcome before it returns, so waiting here keeps it to one, and
        # the caller gets what was recorded: a cancelled hang returns TIMEOUT with no effect.
        return await task


async def call_with_recovery[T](
    call: Callable[[], Result[T]],
    policy: RetryPolicy | None = None,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Recovered[T]:
    policy = policy or RetryPolicy()
    if policy.max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    result: Result[T] = UNEXPECTED
    for attempt in range(1, policy.max_attempts + 1):
        if attempt > 1 and attempt - 2 < len(policy.backoff_seconds):
            await sleep(policy.backoff_seconds[attempt - 2])
        if policy.deadline_seconds is None:
            result = _guarded(call)()
        else:
            result = await with_deadline(call, policy.deadline_seconds)
        if not retryable(result, policy.retry_codes):
            return Recovered(result, attempt)
    return Recovered(result, policy.max_attempts)
