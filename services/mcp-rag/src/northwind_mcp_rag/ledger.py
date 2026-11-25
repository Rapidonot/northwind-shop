"""The search cap, kept by the server (decision 0007; RAG-10; CC-10 C10).

Each turn may run at most `MAX_SEARCHES` searches in all: the gateway's search before the first
model call and the model's refinements are counted against the same token `turn`. A stolen token
shares the counter, so it buys at most the turn's remaining searches until it expires.

In memory, one process (uvicorn runs one worker); an entry is dropped once its token has expired.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

MAX_SEARCHES = 3


class SearchLedger:
    def __init__(
        self, max_searches: int = MAX_SEARCHES, clock: Callable[[], float] = time.time
    ) -> None:
        self._max = max_searches
        self._clock = clock
        self._used: dict[str, tuple[int, int]] = {}  # turn -> (searches, token exp)
        self._lock = asyncio.Lock()

    async def take(self, turn: str, exp: int) -> bool:
        """Count one search for `turn` and say whether it may run."""
        async with self._lock:
            now = self._clock()
            if exp <= now:  # a token expiring between the gate and here gets nothing (review L2)
                return False
            for key in [k for k, (_, e) in self._used.items() if e <= now]:
                del self._used[key]
            used, held = self._used.get(turn, (0, exp))
            if used >= self._max:
                self._used[turn] = (used, max(exp, held))  # re-review N2: a refusal extends it too
                return False
            # Row #11 (security review L1): one turn can have two tokens (the planned first search's
            # and the CLI's) with different expiries; keep the later, so the count outlives both.
            self._used[turn] = (used + 1, max(exp, held))
            return True

    def used(self, turn: str) -> int:
        return self._used.get(turn, (0, 0))[0]

    def __len__(self) -> int:
        return len(self._used)
