"""RAG-02: `make enrich`. The run loop on a fake client with a fake clock, `main`, and the real
`make enrich` with no runner window open. Nothing here calls a model: the fake client raises real
anthropic exceptions, and the end-to-end test is stopped before any call by the missing window."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2 as httpx
import pytest

from northwind_common.pricing import cost_usd
from northwind_evals import enrich
from northwind_evals.enrich import (
    FLOOR_RPM,
    MAX_ATTEMPTS,
    MAX_FAILURES_IN_A_ROW,
    MAX_PAUSE_SECONDS,
    MAX_RATE_LIMITED,
    MIN_PAUSE_SECONDS,
    PROJECTION_LIMIT_USD,
    SELF_CHECK_CALLS,
    START_RPM,
    WORKERS,
    Pacer,
    Run,
    RunReport,
)
from northwind_rag.enrichment import (
    CacheEntry,
    EnrichmentCache,
    Parent,
    judge,
    parents_of,
    plan_work,
)
from northwind_rag.models import Chunk, ChunkMetadata
from northwind_rag.sources import build_chunks

pytestmark = pytest.mark.req("RAG-02")

REPO = Path(__file__).resolve().parents[2]
MODEL = "claude-haiku-5-5"
SENTENCE = "This passage sits inside the Northwind catalog record it belongs to."
USAGE = {"input_tokens": 400, "output_tokens": 30}


# -- fakes ---------------------------------------------------------------------------------------


def status_error(cls: type[anthropic.APIStatusError], status: int, body: Any = None) -> Exception:
    request = httpx.Request("POST", "http://127.0.0.1/v1/messages")
    response = httpx.Response(status, request=request, json=body if body is not None else {})
    return cls(f"status {status}", response=response, body=body)


def rate_limited() -> Exception:
    return status_error(anthropic.RateLimitError, 429)


def connection_error() -> Exception:
    return anthropic.APIConnectionError(request=httpx.Request("POST", "http://127.0.0.1/"))


class Usage:
    def __init__(self, data: dict[str, int]) -> None:
        self.data = data

    def model_dump(self) -> dict[str, int]:
        return dict(self.data)


def ok_response(text: str = SENTENCE, usage: dict[str, int] | None = None) -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=Usage(usage or USAGE),
    )


Step = Exception | Any


class FakeClient:
    """Plays `script` in order, then `default()`. Counts calls and calls in flight."""

    def __init__(
        self,
        script: Sequence[Step] = (),
        default: Callable[[], Step] = ok_response,
        delay: float = 0.0,
        decide: Callable[[dict[str, Any]], Step | None] | None = None,
    ) -> None:
        self.script = deque(script)
        self.default = default
        self.delay = delay
        self.decide = decide
        self.lock = threading.Lock()
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.events: list[tuple[str, int]] = []
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **request: Any) -> Any:
        with self.lock:
            number = self.calls
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.events.append(("start", number))
            self.requests.append(request)
            step = self.decide(request) if self.decide else None
            if step is None:
                step = self.script.popleft() if self.script else self.default()
        try:
            if self.delay:
                time.sleep(self.delay)
            if isinstance(step, Exception):
                raise step
            return step
        finally:
            with self.lock:
                self.active -= 1
                self.events.append(("end", number))


class Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []
        self.lock = threading.Lock()

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        with self.lock:
            self.sleeps.append(seconds)
            self.t += seconds


def pacer(clock: Clock, jitter: float = 1.0) -> Pacer:
    return Pacer(clock.now, clock.sleep, lambda: jitter)


META = ChunkMetadata(
    category="Base Layers", brand="Tolland", size=None, colour=None, material=None,
    price_band=None, region=("US",), updated=date(2026, 9, 1), visibility="public",
)  # fmt: skip


def review(i: int, product: str = "P9999") -> Chunk:
    text = f"Review of Test Item\n\nVerified purchase. Rating: 4 of 5.\n\nTitle {i}\n\nBody {i}."
    return Chunk("review", f"R9{i:04d}", 0, text, META, parent_id=product)


def one_group(n: int) -> tuple[list[Chunk], list[tuple[Parent, list[Chunk]]]]:
    chunks = [review(i) for i in range(n)]
    work = plan_work(chunks, EnrichmentCache(Path("/nonexistent")))
    assert len(work) == 1
    return chunks, work


def make_run(
    tmp_path: Path,
    client: FakeClient,
    clock: Clock | None = None,
    total: int = 100,
    out: list[str] | None = None,
) -> tuple[Run, Clock, list[str]]:
    clock = clock or Clock()
    lines: list[str] = [] if out is None else out
    run = Run(
        client, EnrichmentCache(tmp_path), MODEL, total, frozenset(), pacer(clock), lines.append
    )
    return run, clock, lines


def parent_of(chunk: Chunk) -> Parent:
    return parents_of([chunk])[chunk.chunk_id]


# -- the pacer -----------------------------------------------------------------------------------


def test_the_first_429_of_a_streak_halves_the_rate_and_later_ones_do_not() -> None:
    p = pacer(Clock())
    assert p.rpm == START_RPM
    p.rate_limited()
    assert p.rpm == START_RPM / 2
    p.rate_limited()
    p.rate_limited()
    assert p.rpm == START_RPM / 2
    p.succeeded()
    p.rate_limited()
    assert p.rpm == START_RPM / 4


def test_the_rate_never_falls_below_the_floor() -> None:
    p = pacer(Clock())
    assert FLOOR_RPM == 30
    for _ in range(12):
        p.rate_limited()
        p.succeeded()
    assert p.rpm == FLOOR_RPM


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 7, 10, 40])
def test_the_pause_doubles_per_429_and_caps_at_a_minute(count: int) -> None:
    clock = Clock()
    p = pacer(clock)
    for _ in range(count):
        p.rate_limited()
    p.wait()
    assert clock.sleeps[-1] == pytest.approx(
        min(MAX_PAUSE_SECONDS, MIN_PAUSE_SECONDS * 2 ** (count - 1))
    )
    assert MAX_PAUSE_SECONDS == 60


def test_jitter_only_shortens_the_pause_to_half_at_most() -> None:
    low, high = Clock(), Clock()
    for clock, jitter in ((low, 0.0), (high, 1.0)):
        p = pacer(clock, jitter)
        p.rate_limited()
        p.wait()
    assert low.sleeps[-1] == pytest.approx(MIN_PAUSE_SECONDS / 2)
    assert high.sleeps[-1] == pytest.approx(MIN_PAUSE_SECONDS)


def test_a_success_resets_the_pause() -> None:
    clock = Clock()
    p = pacer(clock)
    for _ in range(5):
        p.rate_limited()
    p.succeeded()
    clock.t += 1000
    clock.sleeps.clear()
    p.rate_limited()
    p.wait()
    assert clock.sleeps[-1] == pytest.approx(MIN_PAUSE_SECONDS)


def test_a_429_seen_by_one_worker_pauses_another() -> None:
    clock = Clock()
    p = pacer(clock)
    p.rate_limited()  # one worker's 429
    other = threading.Thread(target=p.wait)  # another worker's next call
    other.start()
    other.join(timeout=5)
    assert clock.sleeps and clock.sleeps[-1] >= MIN_PAUSE_SECONDS / 2


def test_calls_are_spaced_by_the_rate() -> None:
    clock = Clock()
    p = pacer(clock)
    for _ in range(3):
        p.wait()
    gap = 60.0 / START_RPM
    assert clock.sleeps == [pytest.approx(gap), pytest.approx(gap)]
    assert clock.t == pytest.approx(2 * gap)


# -- one chunk: what is retried and what stops the run -------------------------------------------


def test_a_429_pauses_halves_the_rate_and_retries_the_same_chunk(tmp_path: Path) -> None:
    client = FakeClient([rate_limited()])
    run, clock, _ = make_run(tmp_path, client)
    chunk = review(1)
    entry = run.one(chunk, parent_of(chunk))
    assert entry is not None and entry.status == "ok"
    assert client.calls == 2
    assert client.requests[0] == client.requests[1]
    assert run.report.rate_limited == 1
    assert run._pacer.rpm == START_RPM / 2
    assert any(s >= MIN_PAUSE_SECONDS for s in clock.sleeps)
    assert run.report.stopped is None


def test_a_streak_of_429s_halves_once_and_the_pause_grows(tmp_path: Path) -> None:
    client = FakeClient([rate_limited() for _ in range(4)])
    run, clock, _ = make_run(tmp_path, client)
    chunk = review(1)
    assert run.one(chunk, parent_of(chunk)) is not None
    assert run._pacer.rpm == START_RPM / 2
    pauses = [s for s in clock.sleeps if s > 1.0]
    assert pauses == sorted(pauses) and len(set(pauses)) == len(pauses) == 4
    assert pauses[-1] == pytest.approx(MIN_PAUSE_SECONDS * 2**3)


def test_the_pause_reaches_the_cap_and_stays(tmp_path: Path) -> None:
    client = FakeClient([rate_limited() for _ in range(12)])
    run, clock, _ = make_run(tmp_path, client)
    chunk = review(1)
    assert run.one(chunk, parent_of(chunk)) is not None
    assert max(clock.sleeps) == pytest.approx(MAX_PAUSE_SECONDS)


def test_the_run_stops_at_the_rate_limit_ceiling_and_keeps_the_cache(tmp_path: Path) -> None:
    chunks, work = one_group(3)
    client = FakeClient([ok_response()], default=rate_limited)
    run, _, _ = make_run(tmp_path, client)
    report = run.all(work)
    assert report.stopped is not None and "rate limit" in report.stopped
    assert report.rate_limited >= MAX_RATE_LIMITED
    assert client.calls <= 1 + MAX_RATE_LIMITED + WORKERS
    kept = EnrichmentCache(tmp_path)
    parents = parents_of(chunks)
    assert kept.get(chunks[0], parents[chunks[0].chunk_id]) is not None
    assert kept.get(chunks[1], parents[chunks[1].chunk_id]) is None


@pytest.mark.parametrize(
    ("cls", "status"),
    [
        (anthropic.APIStatusError, 402),
        (anthropic.BadRequestError, 400),
        (anthropic.AuthenticationError, 401),
        (anthropic.PermissionDeniedError, 403),
    ],
)
def test_budget_request_and_auth_errors_stop_at_once_with_no_retry(
    tmp_path: Path, cls: type[anthropic.APIStatusError], status: int
) -> None:
    client = FakeClient([status_error(cls, status)])
    run, _, _ = make_run(tmp_path, client)
    chunk = review(1)
    assert run.one(chunk, parent_of(chunk)) is None
    assert client.calls == 1
    assert run.report.stopped is not None and str(status) in run.report.stopped
    assert run.report.skipped == 0


@pytest.mark.parametrize("body", [{"isRetryable": False}, {}, None, "blocked"])
def test_a_502_that_is_not_marked_retryable_is_the_key_block_and_stops(
    tmp_path: Path, body: Any
) -> None:
    client = FakeClient([status_error(anthropic.InternalServerError, 502, body)])
    run, _, _ = make_run(tmp_path, client)
    chunk = review(1)
    assert run.one(chunk, parent_of(chunk)) is None
    assert client.calls == 1
    assert run.report.stopped is not None and "502" in run.report.stopped


def test_a_502_marked_retryable_is_retried(tmp_path: Path) -> None:
    error = status_error(anthropic.InternalServerError, 502, {"isRetryable": True})
    client = FakeClient([error])
    run, _, _ = make_run(tmp_path, client)
    chunk = review(1)
    entry = run.one(chunk, parent_of(chunk))
    assert entry is not None and entry.status == "ok"
    assert client.calls == 2
    assert run.report.stopped is None


@pytest.mark.parametrize("status", [500, 503, 504, 529])
def test_a_server_error_retries_up_to_the_attempt_limit_then_skips(
    tmp_path: Path, status: int
) -> None:
    client = FakeClient(default=lambda: status_error(anthropic.InternalServerError, status))
    run, _, _ = make_run(tmp_path, client)
    chunk = review(1)
    assert run.one(chunk, parent_of(chunk)) is None
    assert client.calls == MAX_ATTEMPTS == 3
    assert run.report.skipped == 1
    assert run.report.stopped is None


def test_a_dropped_connection_is_retried(tmp_path: Path) -> None:
    client = FakeClient([connection_error()])
    run, _, _ = make_run(tmp_path, client)
    chunk = review(1)
    entry = run.one(chunk, parent_of(chunk))
    assert entry is not None and client.calls == 2


def test_five_failures_in_a_row_stop_the_run(tmp_path: Path) -> None:
    assert MAX_FAILURES_IN_A_ROW == 5
    client = FakeClient(default=lambda: status_error(anthropic.InternalServerError, 500))
    run, _, _ = make_run(tmp_path, client)
    first, second = review(1), review(2)
    assert run.one(first, parent_of(first)) is None
    assert run.report.stopped is None  # three failures
    assert run.one(second, parent_of(second)) is None
    assert run.report.stopped is not None and "5 failures in a row" in run.report.stopped
    calls = client.calls
    assert run.one(review(3), parent_of(review(3))) is None
    assert client.calls == calls  # a stopped run makes no more calls


def test_a_success_resets_the_failure_count(tmp_path: Path) -> None:
    boom = status_error(anthropic.InternalServerError, 500)
    script: list[Step] = [boom, boom, ok_response(), boom, boom, boom]
    client = FakeClient(script, default=lambda: boom)
    run, _, _ = make_run(tmp_path, client)
    for i in (1, 2):
        chunk = review(i)
        run.one(chunk, parent_of(chunk))
    # 2 failures, a success, then 3 more: five in all, but never five in a row.
    assert run.report.stopped is None


# -- the whole run -------------------------------------------------------------------------------


def test_the_first_call_runs_alone_before_the_pool(tmp_path: Path) -> None:
    chunks = [review(i, product=f"P{i % 4}") for i in range(8)]
    work = plan_work(chunks, EnrichmentCache(Path("/nonexistent")))
    assert len(work) == 4
    client = FakeClient(delay=0.05)
    run, _, _ = make_run(tmp_path, client)
    run.all(work)
    assert client.events[0] == ("start", 0)
    assert client.events[1] == ("end", 0)  # nothing else began until it finished
    assert client.calls == 8


def test_concurrency_never_exceeds_the_worker_count(tmp_path: Path) -> None:
    chunks = [review(i, product=f"P{i}") for i in range(18)]
    work = plan_work(chunks, EnrichmentCache(Path("/nonexistent")))
    client = FakeClient(delay=0.03)
    run, _, _ = make_run(tmp_path, client)
    run.all(work)
    assert WORKERS <= 4  # the runner's limit of requests in flight
    assert client.max_active <= WORKERS
    assert client.max_active >= 2
    assert client.calls == 18


def test_the_self_check_prints_after_20_calls_and_a_cheap_run_goes_on(tmp_path: Path) -> None:
    assert SELF_CHECK_CALLS == 20
    _, work = one_group(25)
    client = FakeClient()
    run, _, lines = make_run(tmp_path, client, total=25)
    report = run.all(work)
    checks = [line for line in lines if line.startswith("after 20 calls")]
    assert len(checks) == 1 and "projected" in checks[0]
    assert report.calls == 25 and report.stopped is None


def test_there_is_no_self_check_before_20_calls(tmp_path: Path) -> None:
    _, work = one_group(19)
    run, _, lines = make_run(tmp_path, FakeClient(), total=19)
    run.all(work)
    assert not [line for line in lines if line.startswith("after")]


def test_a_projected_cost_over_the_limit_stops_the_run(tmp_path: Path) -> None:
    per_call = cost_usd(MODEL, USAGE)
    assert PROJECTION_LIMIT_USD == 1.50
    total = int(PROJECTION_LIMIT_USD / per_call) + 10
    _, work = one_group(30)
    client = FakeClient()
    run, _, lines = make_run(tmp_path, client, total=total)
    report = run.all(work)
    assert report.stopped is not None and "projected" in report.stopped
    assert client.calls == SELF_CHECK_CALLS
    assert any(line.startswith("after 20 calls") for line in lines)


def test_a_projected_cost_just_under_the_limit_does_not_stop(tmp_path: Path) -> None:
    per_call = cost_usd(MODEL, USAGE)
    total = int(PROJECTION_LIMIT_USD * 0.9 / per_call)
    _, work = one_group(30)
    run, _, _ = make_run(tmp_path, FakeClient(), total=total)
    assert run.all(work).stopped is None


def test_a_second_window_calls_only_what_is_missing(tmp_path: Path) -> None:
    chunks, work = one_group(6)
    first = FakeClient(
        [ok_response(), ok_response(), ok_response(), status_error(anthropic.APIStatusError, 402)]
    )
    run, _, _ = make_run(tmp_path, first)
    report = run.all(work)
    assert report.stopped is not None and "402" in report.stopped
    assert first.calls == 4
    left = plan_work(chunks, EnrichmentCache(tmp_path))
    assert [c.chunk_id for _, group in left for c in group] == [c.chunk_id for c in chunks[3:]]
    second = FakeClient()
    run2, _, _ = make_run(tmp_path, second)
    assert run2.all(left).stopped is None
    assert second.calls == 3
    assert plan_work(chunks, EnrichmentCache(tmp_path)) == []


def test_a_complete_cache_means_the_run_makes_no_call(tmp_path: Path) -> None:
    chunks, work = one_group(4)
    make_run(tmp_path, FakeClient())[0].all(work)
    client = FakeClient()
    run, _, _ = make_run(tmp_path, client)
    assert plan_work(chunks, EnrichmentCache(tmp_path)) == []
    report = run.all(plan_work(chunks, EnrichmentCache(tmp_path)))
    assert client.calls == 0 and report.calls == 0 and report.stopped is None


def test_the_report_counts_ok_rejected_cost_and_cache_tokens(tmp_path: Path) -> None:
    usage = {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 3000}
    _, work = one_group(2)
    client = FakeClient([ok_response(usage=usage), ok_response("Costs $49.99 today.", usage=usage)])
    run, _, _ = make_run(tmp_path, client)
    report = run.all(work)
    assert (report.calls, report.ok, report.rejected) == (2, 1, 1)
    assert report.cache_read_tokens == 6000
    assert report.cost_usd == pytest.approx(2 * cost_usd(MODEL, usage))


# -- main ----------------------------------------------------------------------------------------


def write_complete_cache(directory: Path, chunks: Sequence[Chunk]) -> None:
    parents = parents_of(chunks)
    entries: dict[str, dict[str, CacheEntry]] = {}
    for chunk in chunks:
        entry = judge(chunk, parents[chunk.chunk_id], MODEL, SENTENCE, "end_turn")
        entries.setdefault(entry.visibility, {})[entry.key] = entry
    directory.mkdir(parents=True, exist_ok=True)
    for visibility, found in entries.items():
        lines = [json.dumps(asdict(e), ensure_ascii=False, sort_keys=True) for e in found.values()]
        (directory / f"{visibility}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


class Tripwire:
    """Stands in for anything that would reach a model, and fails the test if it is touched."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, *args: object, **kwargs: object) -> Any:
        raise AssertionError(f"{self.name} was called")


def arm(monkeypatch: pytest.MonkeyPatch, cache_dir: Path, mode: str | None = "live") -> None:
    monkeypatch.chdir(REPO)
    monkeypatch.setattr(sys, "argv", ["enrich", "--cache-dir", str(cache_dir)])
    if mode is None:
        monkeypatch.delenv("LLM_MODE", raising=False)
    else:
        monkeypatch.setenv("LLM_MODE", mode)
    monkeypatch.delenv("ENRICH_CACHE_DIR", raising=False)


def test_main_with_a_complete_cache_makes_no_client_no_proxy_and_no_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_complete_cache(tmp_path, build_chunks(REPO / "data" / "fixtures"))
    arm(monkeypatch, tmp_path)
    for name in ("anthropic_client", "proxy_config", "RunningProxy", "Run"):
        monkeypatch.setattr(enrich, name, Tripwire(name))
    assert enrich.main() == 0
    assert "nothing to call" in capsys.readouterr().out


@pytest.mark.parametrize("mode", [None, "", "replay", "record", "LIVE", "live "])
def test_main_refuses_any_mode_but_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str | None,
) -> None:
    arm(monkeypatch, tmp_path, mode)
    for name in ("anthropic_client", "proxy_config", "RunningProxy", "Run"):
        monkeypatch.setattr(enrich, name, Tripwire(name))
    assert enrich.main() != 0
    assert "live" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_main_builds_its_client_without_sdk_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry the SDK made would be charged without being seen."""
    arm(monkeypatch, tmp_path)
    built: list[dict[str, Any]] = []

    @contextmanager
    def window(config: object) -> Iterator[Any]:
        yield SimpleNamespace(url="http://127.0.0.1:9")

    class StubRun:
        def __init__(self, client: object, *args: object) -> None:
            self.client = client

        def all(self, work: object) -> RunReport:
            return RunReport()

    def fake_client(*args: object, **kwargs: object) -> object:
        built.append(dict(kwargs))
        return object()

    monkeypatch.setattr(enrich, "proxy_config", lambda *a, **k: object())
    monkeypatch.setattr(enrich, "RunningProxy", window)
    monkeypatch.setattr(enrich, "anthropic_client", fake_client)
    monkeypatch.setattr(enrich, "Run", StubRun)
    assert enrich.main() == 0
    assert built and built[0]["max_retries"] == 0


# -- 9. the real command, with no window open ------------------------------------------------------


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_make_enrich_with_no_window_open_exits_before_any_call_and_leaves_every_file(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes(b'{"id": "synthetic", "cost_usd": 0.0}\n\x00 tail')
    before = ledger.read_bytes()
    real = Path.home() / ".northwind" / "ledger.jsonl"
    real_before = real.read_bytes() if real.exists() else None
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "FASTEMBED_CACHE_PATH": os.environ.get(
            "FASTEMBED_CACHE_PATH", str(Path.home() / ".cache" / "fastembed")
        ),
        "DATABASE_URL": os.environ.get(
            "DATABASE_URL",
            "postgresql://northwind:northwind-local@localhost:5432/northwind_rag02_plain",
        ),
        "LLM_RUNNER_PORT": str(closed_port()),
        "LLM_LEDGER_PATH": str(ledger),
        "ENRICH_CACHE_DIR": str(cache_dir),
    }
    env.update({k: v for k, v in os.environ.items() if k.startswith("UV_") or k == "TMPDIR"})
    done = subprocess.run(  # noqa: S603
        ["make", "-s", "enrich"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=300,
    )
    output = done.stdout + done.stderr
    assert done.returncode != 0, output
    assert "window" in output and "llm-runner" in output, output
    assert ledger.read_bytes() == before
    if real_before is not None:
        assert real.read_bytes() == real_before
    assert list(cache_dir.iterdir()) == []
