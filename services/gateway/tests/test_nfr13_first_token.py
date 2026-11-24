"""First-token timing through the real gateway (row #17, NFR-13 partial): the harness and its tests.

The runner buffers each whole response to scan it, so a client cannot see first token through it
(#17a); it logs time to first upstream byte instead. This harness measures the other part, local
overhead, in replay: the real gateway, the real CLI and the real output check, in front of a paced
streaming fake model whose timing is known. The estimate is the sum of the two (decision 0007,
the row #17 plan, addendum 1 item 4).

The fake lives here, not in `northwind_llm_replay` (control surface). Its first delta ends
mid-sentence by default, so any hold-back by the output check is in the measurement.

The tests prove the harness and the stream, with no absolute timing threshold (the flaky-test
precedent, N-1): a scripted wait must show up as a lower bound, and the first token must arrive
before a long scripted pause in the rest of the answer, so the gateway really streams. The
30-turn measurement on `northwind_16` is a separate run after window B ships (addendum 1, item 4).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import RagEndpoint
from northwind_common.llm import agent_runtime
from northwind_gateway.app import app
from northwind_gateway.chat import ChatServices, chat_services
from northwind_llm_replay.scripted import ServerThread, message_json
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

# A realistic answer opening: the first delta stops mid-sentence (addendum 1, item 4).
DEFAULT_CHUNKS = ("The Larchpell Larch Area Rug is a ", "flatweave rug [P0178]. ", "It lies flat.")


def _event(name: str, data: dict[str, Any]) -> bytes:
    return f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n".encode()


@dataclass
class PacedModel:
    """A Messages API fake that streams one text answer per request, on a known clock.

    `first_delay`: seconds from the request to `message_start`. `pace`: seconds between deltas.
    `gaps`: extra seconds to wait after the delta at that index."""

    chunks: Sequence[str] = DEFAULT_CHUNKS
    first_delay: float = 0.0
    pace: float = 0.0
    gaps: dict[int, float] = field(default_factory=dict)
    requests: int = 0
    # (request arrival, first delta sent), perf_counter seconds, per request (the 30-turn run).
    marks: list[tuple[float, float]] = field(default_factory=list)

    async def _stream(self, arrived: float) -> AsyncIterator[bytes]:
        await asyncio.sleep(self.first_delay)
        usage = {"input_tokens": 10, "output_tokens": 1}
        start = message_json("paced", "", usage, "msg_paced") | {"content": [], "stop_reason": None}
        yield _event("message_start", {"message": start})
        yield _event(
            "content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}
        )
        for index, chunk in enumerate(self.chunks):
            if index:
                await asyncio.sleep(self.pace)
            delta = {"type": "text_delta", "text": chunk}
            if not index:
                self.marks.append((arrived, time.perf_counter()))
            yield _event("content_block_delta", {"index": 0, "delta": delta})
            await asyncio.sleep(self.gaps.get(index, 0.0))
        yield _event("content_block_stop", {"index": 0})
        yield _event(
            "message_delta",
            {
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
        )
        yield _event("message_stop", {})

    async def _handle(self, request: Request) -> StreamingResponse:
        arrived = time.perf_counter()
        await request.body()
        self.requests += 1
        return StreamingResponse(self._stream(arrived), media_type="text/event-stream")

    @contextmanager
    def serving(self) -> Iterator[str]:
        app = Starlette(routes=[Route("/{path:path}", self._handle, methods=["GET", "POST"])])
        server = ServerThread(app)
        server.start()
        try:
            yield server.url
        finally:
            server.stop()


@contextmanager
def serving_app(app: Any) -> Iterator[str]:
    """The gateway on a real socket, so its stream is read as a browser reads it."""
    server = ServerThread(app)
    server.start()
    try:
        yield server.url
    finally:
        server.stop()


@dataclass(frozen=True)
class Timing:
    first_token_ms: float | None
    done_ms: float
    events: tuple[str, ...]
    started: float = 0.0  # perf_counter at the POST


def measure(gateway_url: str, message: str, timeout: float = 60.0) -> Timing:
    """POST one message and time the first `token` event and the end of the stream."""
    names: list[str] = []
    first: float | None = None
    started = time.perf_counter()
    with httpx.stream(
        "POST", f"{gateway_url}/api/chat", json={"message": message}, timeout=timeout
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("event: "):
                continue
            name = line.removeprefix("event: ").strip()
            names.append(name)
            if name == "token" and first is None:
                first = (time.perf_counter() - started) * 1000
    done = (time.perf_counter() - started) * 1000
    return Timing(first, done, tuple(names), started)


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100), so p95 of 30 runs is the 29th value."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * q // 100))
    return ordered[int(rank) - 1]


# ---- tests ---------------------------------------------------------------------------------------

pytestmark = pytest.mark.req("NFR-13")

RUG = RetrievedChunk(
    "c1",
    "product",
    "P0178",
    "product",
    "Larchpell Larch Area Rug\n\nBrand: Larchpell. Department: Living.\n\nA flatweave rug.",
)


class OneRug:
    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        return [RUG]


@contextmanager
def gateway_before(model: PacedModel, root: Any) -> Iterator[str]:
    """The real gateway, CLI and output check in front of `model`; mcp-rag serves one source."""
    with model.serving() as model_url, serve_retriever(OneRug(), Caller) as (rag_url, _):
        services = ChatServices(
            RagEndpoint(rag_url, KEY), agent_runtime(base_url=model_url, root=root)
        )
        app.dependency_overrides[chat_services] = lambda: services
        try:
            with serving_app(app) as url:
                yield url
        finally:
            app.dependency_overrides.clear()


def test_the_first_token_arrives_before_a_long_pause_in_the_rest_of_the_answer(
    tmp_path: Any,
) -> None:
    """The gateway streams through the output check: the first sentence is shown while the model
    is still paused before the next one, not when the whole answer is in."""
    pause = 2.0
    model = PacedModel(gaps={1: pause})  # the pause comes after the first sentence ends
    with gateway_before(model, tmp_path / "rt") as url:
        timing = measure(url, "Tell me about the rug")
    assert timing.events[0] == "token" and timing.events[-1] == "done"
    assert timing.first_token_ms is not None
    assert timing.done_ms - timing.first_token_ms >= pause * 1000 * 0.75


def test_a_scripted_wait_before_the_model_answers_shows_up_in_the_first_token(
    tmp_path: Any,
) -> None:
    wait = 0.6
    with gateway_before(PacedModel(first_delay=wait), tmp_path / "rt") as url:
        timing = measure(url, "Tell me about the rug")
    assert timing.first_token_ms is not None and timing.first_token_ms >= wait * 1000


def test_the_fake_ends_its_first_delta_mid_sentence() -> None:
    first = DEFAULT_CHUNKS[0]
    assert not first.rstrip().endswith((".", "!", "?"))


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [([1.0], 95, 1.0), ([float(n) for n in range(1, 31)], 95, 29.0), ([3.0, 1.0, 2.0], 50, 2.0)],
)
def test_percentile_is_nearest_rank(values: list[float], q: float, expected: float) -> None:
    assert percentile(values, q) == expected


# ---- the 30-turn measurement (run by hand, outside any window) --------------------------------


def main() -> None:
    """Run: `DATABASE_URL=…/northwind_16 uv run --offline python <this file> [--runs 30]`.

    The production surface in replay: mcp-rag over the real index with the live FastEmbed embedder
    (mcp-rag's default), mcp-catalog over the verified fixtures, the real CLI and gateway, and the
    paced fake as the model. One warm-up turn, then `--runs` sequential turns over the five smoke
    questions. Prints JSON: per turn, POST to first token; the part before the model's request
    arrives (gateway, session, search, CLI start-up); and the part after its first delta (output
    check hold-back, transport). Local overhead = first token minus the fake's own wait (0 here)."""
    import argparse
    import os
    import statistics
    import tempfile
    from pathlib import Path

    import psycopg

    from northwind_assistant.catalog_client import CatalogEndpoint
    from northwind_mcp_catalog import testing as catalog_testing
    from northwind_mcp_catalog.live import FixtureCatalog
    from northwind_mcp_rag.testing import CallerIndex
    from northwind_rag.embedding import FastEmbedEmbedder

    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument(
        "--pace", type=float, default=0.05, help="seconds between the fake's deltas"
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    questions = list(json.loads((repo / "apps/portal/e2e/questions.json").read_text()).values())

    def connect() -> psycopg.Connection:
        return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)

    model = PacedModel(pace=args.pace)
    index = CallerIndex(connect, FastEmbedEmbedder())
    with (
        tempfile.TemporaryDirectory() as root,
        model.serving() as model_url,
        serve_retriever(index, Caller) as (rag_url, _),
        catalog_testing.running(catalog_testing.server(FixtureCatalog(), key=KEY)) as catalog_url,
    ):
        services = ChatServices(
            RagEndpoint(rag_url, KEY),
            agent_runtime(base_url=model_url, root=Path(root) / "rt"),
            CatalogEndpoint(catalog_url, KEY),
        )
        app.dependency_overrides[chat_services] = lambda: services
        try:
            with serving_app(app) as url:
                warm = measure(url, questions[0])
                turns: list[dict[str, Any]] = []
                for n in range(args.runs):
                    before = len(model.marks)
                    timing = measure(url, questions[n % len(questions)])
                    arrived, first_delta = model.marks[before]
                    turns.append(
                        {
                            "question": n % len(questions),
                            "first_token_ms": timing.first_token_ms,
                            "before_model_ms": (arrived - timing.started) * 1000,
                            "after_first_delta_ms": None
                            if timing.first_token_ms is None
                            else timing.first_token_ms - (first_delta - timing.started) * 1000,
                            "model_requests": len(model.marks) - before,
                        }
                    )
        finally:
            app.dependency_overrides.clear()
    firsts = [t["first_token_ms"] for t in turns if t["first_token_ms"] is not None]
    report = {
        "runs": args.runs,
        "pace_s": args.pace,
        "chunks": list(DEFAULT_CHUNKS),
        "warm_up_first_token_ms": warm.first_token_ms,
        "first_token_ms": {
            "p50": percentile(firsts, 50),
            "p95": percentile(firsts, 95),
            "max": max(firsts),
            "mean": statistics.fmean(firsts),
        },
        "before_model_ms_p50_p95": [
            percentile([t["before_model_ms"] for t in turns], q) for q in (50, 95)
        ],
        "after_first_delta_ms_p50_p95": [
            percentile([t["after_first_delta_ms"] for t in turns if t["after_first_delta_ms"]], q)
            for q in (50, 95)
        ],
        "turns": turns,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
