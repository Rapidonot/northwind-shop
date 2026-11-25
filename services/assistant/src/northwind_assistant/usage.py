"""Tokens and cost of one answer, from the `usage` on each model call (RAG-13).

The assistant's prompt caching is set by the Agent SDK's CLI, which puts `cache_control` on the
system blocks and the last message, so this module only measures it. Cost comes from our own
prices: `ResultMessage.total_cost_usd` is the CLI's table, not `northwind_common.pricing`.

The telemetry line holds counts only: no shopper text, queries or source IDs. `blocked` counts the
output check's blocks per class (NFR-03), never their text.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from northwind_common.pricing import UnpricedModelError, cost_usd

TELEMETRY_LOGGER = "northwind.telemetry"

telemetry = logging.getLogger(TELEMETRY_LOGGER)


def _count(usage: Mapping[str, Any], key: str) -> int:
    try:
        return int(usage.get(key) or 0)
    except (OverflowError, TypeError, ValueError):
        return 0


@dataclass
class TurnUsage:
    """Sums every model call in one turn, retried attempts included: they were paid for. Nothing
    here may fail a shopper's answer, so malformed upstream figures count as missing."""

    calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = 0.0
    blocked: dict[str, int] = field(default_factory=dict)
    # `stream_answer` reports once its output check has released the last text, so the line can
    # hold the check's counts: `report` then only keeps the outcome until `flush`.
    deferred: bool = False
    # Row #11 (RAG-04): how the first search's plan was made, and how long it took. A path name and
    # a duration only, never the message or the plan (NFR-04).
    qu_path: str = "off"
    qu_ms: int = 0
    _outcome: str | None = field(default=None, repr=False)
    _model: str = ""
    _open: dict[str, Any] | None = field(default=None, repr=False)

    def call_started(self, message: Any) -> None:
        self._close_call()
        details = message if isinstance(message, Mapping) else {}
        usage = details.get("usage")
        self._model = str(details.get("model") or "")
        self._open = dict(usage) if isinstance(usage, Mapping) else {}

    def understanding(self, path: str, ms: int, model: str = "", usage: Any = None) -> None:
        """Row #11: query understanding's path and time; a Haiku call counts as a call, with its
        cost."""
        self.qu_path, self.qu_ms = path, ms
        if model:
            details = {"model": model, "usage": usage if isinstance(usage, Mapping) else {}}
            self.call_started(details)
            self._close_call()

    def call_updated(self, usage: Any) -> None:
        """`message_delta` holds the final figures, as in the proxy's `parse_sse_usage`."""
        if self._open is not None and isinstance(usage, Mapping):
            self._open.update({key: value for key, value in usage.items() if value is not None})

    def _close_call(self) -> None:
        if self._open is None:
            return
        usage, self._open = self._open, None
        self.calls += 1
        self.input_tokens += _count(usage, "input_tokens")
        self.cache_read_tokens += _count(usage, "cache_read_input_tokens")
        self.cache_write_tokens += _count(usage, "cache_creation_input_tokens")
        self.output_tokens += _count(usage, "output_tokens")
        try:
            call_cost = cost_usd(self._model, usage)
        except (UnpricedModelError, AttributeError, OverflowError, TypeError, ValueError):
            self.cost_usd = None
            return
        if self.cost_usd is not None:
            self.cost_usd = round(self.cost_usd + call_cost, 9)

    @property
    def cache_hit_rate(self) -> float | None:
        """Share of prompt tokens served from cache; None when no prompt tokens were reported."""
        prompt = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        return round(self.cache_read_tokens / prompt, 4) if prompt else None

    def report(self, outcome: str) -> None:
        if self.deferred:
            self._outcome = outcome
            return
        self._log(outcome)

    def flush(self) -> None:
        """The deferred line, once. A turn that ended before it reported (the client went away,
        or the check raised) is logged as `aborted`, so every turn writes exactly one line."""
        outcome, self._outcome = self._outcome or "aborted", None
        self.deferred = False
        self._log(outcome)

    def _log(self, outcome: str) -> None:
        self._close_call()
        telemetry.info(
            json.dumps(
                {
                    "event": "assistant_turn",
                    "outcome": outcome,
                    "calls": self.calls,
                    "input_tokens": self.input_tokens,
                    "cache_read_tokens": self.cache_read_tokens,
                    "cache_write_tokens": self.cache_write_tokens,
                    "output_tokens": self.output_tokens,
                    "cost_usd": self.cost_usd,
                    "cache_hit_rate": self.cache_hit_rate,
                    "blocked": dict(sorted(self.blocked.items())),
                    "qu_path": self.qu_path,
                    "qu_ms": self.qu_ms,
                },
                sort_keys=True,
            )
        )
