"""`make enrich`: spends through one runner window to fill the enrichment cache (RAG-02).

Decision 0009. Calls are plain Messages requests with prompt caching, in `live` mode: the ledger
records each one and the cache in `data/enrichment` is the record of the run, so no cassettes.

The runner settles a 429 at its worst case and the proxy drops `retry-after`, so this paces itself:
few workers, one shared pause, and a request rate that halves when the API pushes back. A retry the
SDK made would be charged without being seen, so its own retries are off. The cache is written per
parent, and any stop keeps what was paid for: a second window resumes from there.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic

from northwind_common.llm import ROUTINE_MODEL, anthropic_client
from northwind_common.pricing import cost_usd
from northwind_llm_replay.proxy import RunningProxy
from northwind_llm_replay.runner import RunnerUnavailableError, proxy_config
from northwind_llm_replay.scan import load_canaries
from northwind_rag.enrichment import (
    CACHE_DIR,
    CacheEntry,
    EnrichmentCache,
    Parent,
    build_request,
    judge,
    plan_work,
)
from northwind_rag.models import Chunk
from northwind_rag.sources import build_chunks

SUITE = "rag-enrichment"
FIXTURES = Path("data/fixtures")
WORKERS = 3  # under the runner's limit of 4 in flight
START_RPM = 180.0
FLOOR_RPM = 30.0
MIN_PAUSE_SECONDS = 2.0
MAX_PAUSE_SECONDS = 60.0  # well under the runner's idle limit
MAX_RATE_LIMITED = 150  # about $0.10 of worst-case settlements
MAX_ATTEMPTS = 3  # for a failure that is not a 429
MAX_FAILURES_IN_A_ROW = 5
SELF_CHECK_CALLS = 20
PROJECTION_LIMIT_USD = 1.50
REQUEST_TIMEOUT_SECONDS = 60.0
RETRYABLE_STATUS = frozenset({500, 503, 504, 529})


class Pacer:
    """One rate and one pause for all workers, so a 429 slows the run and not just its worker."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self._streak = 0
        self.rpm = START_RPM
        self.rate_limited_count = 0

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot)
            self._next_slot = slot + 60.0 / self.rpm
        if slot > now:
            self._sleep(slot - now)

    def rate_limited(self) -> None:
        with self._lock:
            self.rate_limited_count += 1
            if self._streak == 0:
                self.rpm = max(FLOOR_RPM, self.rpm / 2)
        self.pause()

    def pause(self) -> None:
        """Everyone waits, longer each time in a row, without touching the rate."""
        with self._lock:
            pause = min(MAX_PAUSE_SECONDS, MIN_PAUSE_SECONDS * 2**self._streak)
            self._streak += 1
            self._next_slot = max(
                self._next_slot, self._clock() + pause * (0.5 + self._jitter() / 2)
            )

    def succeeded(self) -> None:
        with self._lock:
            self._streak = 0


@dataclass
class RunReport:
    calls: int = 0
    ok: int = 0
    rejected: int = 0
    skipped: int = 0
    cost_usd: float = 0.0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    rate_limited: int = 0
    stopped: str | None = None


def _retryable(error: anthropic.APIStatusError) -> bool:
    """A 502 is either an upstream failure or the runner blocking a key fragment. Only the proxy's
    own error envelope can say it is the first."""
    if error.status_code == 502:
        body = error.body if isinstance(error.body, dict) else {}
        return body.get("isRetryable") is True
    return error.status_code in RETRYABLE_STATUS


def _text(response: Any) -> str:
    return "".join(block.text for block in response.content if block.type == "text")


class Run:
    def __init__(
        self,
        client: Any,
        cache: EnrichmentCache,
        model: str,
        total: int,
        canaries: frozenset[str],
        pacer: Pacer | None = None,
        out: Callable[[str], None] = print,
    ) -> None:
        self._client = client
        self._cache = cache
        self._model = model
        self._total = total
        self._canaries = canaries
        self._pacer = pacer or Pacer()
        self._out = out
        self._lock = threading.Lock()
        self._failures_in_a_row = 0
        self.report = RunReport()

    def _stop(self, reason: str) -> None:
        with self._lock:
            self.report.stopped = self.report.stopped or reason

    @property
    def stopped(self) -> bool:
        return self.report.stopped is not None

    def _account(self, response: Any, entry: CacheEntry) -> None:
        usage = response.usage.model_dump()
        with self._lock:
            report = self.report
            report.calls += 1
            report.ok += entry.status == "ok"
            report.rejected += entry.status == "rejected"
            report.cost_usd += cost_usd(self._model, usage)
            report.cache_read_tokens += usage.get("cache_read_input_tokens") or 0
            report.cache_write_tokens += usage.get("cache_creation_input_tokens") or 0
            self._failures_in_a_row = 0
            check = report.calls == SELF_CHECK_CALLS
        if check:
            self._self_check()

    def _self_check(self) -> None:
        report = self.report
        projected = report.cost_usd / report.calls * self._total
        self._out(
            f"after {report.calls} calls: ${report.cost_usd:.6f}, "
            f"cache read {report.cache_read_tokens}, written {report.cache_write_tokens}, "
            f"projected ${projected:.2f} for {self._total}"
        )
        if report.cache_read_tokens == 0:
            self._out("warning: no cache reads yet")
        if projected > PROJECTION_LIMIT_USD:
            self._stop(f"projected ${projected:.2f} is over ${PROJECTION_LIMIT_USD:.2f}")

    def _failed(self, reason: str) -> None:
        with self._lock:
            self._failures_in_a_row += 1
            over = self._failures_in_a_row >= MAX_FAILURES_IN_A_ROW
        if over:
            self._stop(f"{MAX_FAILURES_IN_A_ROW} failures in a row, last {reason}")

    def one(self, chunk: Chunk, parent: Parent) -> CacheEntry | None:
        """One chunk's entry, or None if it must wait for another run. Only a 429 retries without
        limit, and the run stops on those too."""
        request = build_request(chunk, parent, self._model)
        attempts = 0
        while not self.stopped:
            self._pacer.wait()
            try:
                response = self._client.messages.create(**request)
            except anthropic.RateLimitError:
                self._pacer.rate_limited()
                self.report.rate_limited = self._pacer.rate_limited_count
                if self._pacer.rate_limited_count >= MAX_RATE_LIMITED:
                    self._stop(f"{MAX_RATE_LIMITED} rate limit replies")
                continue
            except anthropic.APIStatusError as error:
                if not _retryable(error):
                    self._stop(f"status {error.status_code}")  # budget, auth, a closed window
                    return None
                reason = f"status {error.status_code}"
            except anthropic.APIConnectionError as error:
                reason = type(error).__name__
            else:
                self._pacer.succeeded()
                entry = judge(
                    chunk,
                    parent,
                    self._model,
                    _text(response),
                    response.stop_reason,
                    self._canaries,
                )
                self._account(response, entry)
                return entry
            attempts += 1
            self._failed(reason)
            if attempts >= MAX_ATTEMPTS:
                self.report.skipped += 1
                return None
            self._pacer.pause()
        return None

    def parent_job(self, work: tuple[Parent, Sequence[Chunk]]) -> None:
        parent, chunks = work
        entries: list[CacheEntry] = []
        for chunk in chunks:
            entry = self.one(chunk, parent)
            if entry is not None:
                entries.append(entry)
            if self.stopped:
                break
        with self._lock:
            self._cache.add(entries)

    def all(self, work: list[tuple[Parent, list[Chunk]]]) -> RunReport:
        """The first call runs alone, so the shared system prompt is cached before the rest."""
        if work:
            parent, chunks = work[0]
            self.parent_job((parent, chunks[:1]))
            rest: list[tuple[Parent, Sequence[Chunk]]] = [(parent, chunks[1:]), *work[1:]]
            if not self.stopped:
                with ThreadPoolExecutor(WORKERS) as pool:
                    list(pool.map(self.parent_job, rest))
        return self.report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_dir = Path(os.environ.get("ENRICH_CACHE_DIR") or CACHE_DIR)
    parser.add_argument("--cache-dir", type=Path, default=default_dir)
    args = parser.parse_args()
    mode = os.environ.get("LLM_MODE")
    if mode != "live":
        print(f"make enrich runs in live mode, not {mode!r}", file=sys.stderr)
        return 2
    chunks = build_chunks(FIXTURES)
    cache = EnrichmentCache(args.cache_dir)
    work = plan_work(chunks, cache)
    total = sum(len(group) for _, group in work)
    if not work:
        print("every chunk has an entry: nothing to call")
        return 0
    canaries = load_canaries(Path("."), required=True)
    try:
        config = proxy_config(
            "live",
            SUITE,
            cassette_root=Path("cassettes"),
            log_path=Path(f".llm-proxy-log/{SUITE}.jsonl"),
        )
    except RunnerUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    with RunningProxy(config) as running:
        client = anthropic_client(running.url, max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS)
        report = Run(client, cache, ROUTINE_MODEL, total, canaries).all(work)
    cache.compact()
    print(
        f"{report.calls} calls, {report.ok} ok, {report.rejected} rejected, "
        f"{report.skipped} skipped, {report.rate_limited} rate limited, "
        f"${report.cost_usd:.6f}, cache read {report.cache_read_tokens}"
    )
    if report.stopped:
        print(f"stopped: {report.stopped}. Open another window to resume.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
