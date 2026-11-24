"""A scripted stand-in for the Messages API (FOUND-2).

Two uses:
- the `scripted_llm` pytest fixture: plumbing tests that need a model to answer something but no
  cassette (the factory points clients at it like at the proxy);
- the fake upstream behind the proxy in the proxy's own tests, so they never touch the network.

Responses are served in order. Each is a text answer (rendered as JSON or as an SSE stream,
whichever the request asked for) or a raw (status, content-type, body) triple. A script can
also cut a stream short (no message_delta usage), to test the spend guard's worst-case charge.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Self

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from northwind_common.llm import PLACEHOLDER_API_KEY


class ServerThread:
    """Serve an ASGI app with uvicorn on 127.0.0.1 (random port) in a daemon thread."""

    def __init__(self, app: Any, port: int = 0) -> None:
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"
        )
        self.server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        while not self.server.started:
            if not self._thread.is_alive():
                raise RuntimeError("server failed to start")
            threading.Event().wait(0.02)

    def stop(self) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=10)

    @property
    def url(self) -> str:
        sock = self.server.servers[0].sockets[0]
        host, port = sock.getsockname()[:2]
        return f"http://{host}:{port}"


@dataclass
class Scripted:
    text: str | None = None
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 10, "output_tokens": 5})
    raw: tuple[int, str, str] | None = None  # (status, content-type, body)
    cut_stream: bool = False  # SSE without message_delta / message_stop


def message_json(model: str, text: str, usage: dict[str, int], msg_id: str) -> dict[str, Any]:
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": usage,
    }


def message_sse(
    model: str, text: str, usage: dict[str, int], msg_id: str, cut: bool = False
) -> str:
    def ev(name: str, data: dict[str, Any]) -> str:
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    start_usage = {k: v for k, v in usage.items() if k != "output_tokens"} | {"output_tokens": 1}
    msg = message_json(model, "", start_usage, msg_id) | {"content": [], "stop_reason": None}
    parts = [
        ev("message_start", {"type": "message_start", "message": msg}),
        ev(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        ev(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
    ]
    if cut:
        return "".join(parts)
    parts += [
        ev("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ev(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": usage.get("output_tokens", 0)},
            },
        ),
        ev("message_stop", {"type": "message_stop"}),
    ]
    return "".join(parts)


def blocks_sse(
    model: str,
    blocks: list[dict[str, Any]],
    stop_reason: str,
    usage: dict[str, int],
    msg_id: str,
) -> str:
    """A full stream of `text` blocks (`chunks`: one delta each) and `tool_use` blocks."""

    def ev(name: str, data: dict[str, Any]) -> str:
        return f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n"

    start_usage = {k: v for k, v in usage.items() if k != "output_tokens"} | {"output_tokens": 1}
    msg = message_json(model, "", start_usage, msg_id) | {"content": [], "stop_reason": None}
    parts = [ev("message_start", {"message": msg})]
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            start: dict[str, Any] = {"type": "text", "text": ""}
            deltas = [{"type": "text_delta", "text": chunk} for chunk in block["chunks"]]
        else:
            start = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
            deltas = [{"type": "input_json_delta", "partial_json": json.dumps(block["input"])}]
        parts.append(ev("content_block_start", {"index": index, "content_block": start}))
        parts += [ev("content_block_delta", {"index": index, "delta": d}) for d in deltas]
        parts.append(ev("content_block_stop", {"index": index}))
    parts.append(
        ev(
            "message_delta",
            {
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": usage.get("output_tokens", 0)},
            },
        )
    )
    parts.append(ev("message_stop", {}))
    return "".join(parts)


class _Replies:
    """The ways to script a reply. Each appends to `queue`, which is served in order."""

    queue: deque[Scripted]

    def reply(self, text: str, **usage: int) -> Self:
        self.queue.append(
            Scripted(text=text, usage=usage or {"input_tokens": 10, "output_tokens": 5})
        )
        return self

    def reply_raw(self, status: int, body: str, content_type: str = "application/json") -> Self:
        self.queue.append(Scripted(raw=(status, content_type, body)))
        return self

    def reply_stream(self, *chunks: str, model: str = "scripted") -> Self:
        """A text answer in several deltas, as a real stream arrives."""
        blocks = [{"type": "text", "chunks": list(chunks)}]
        usage = {"input_tokens": 10, "output_tokens": 5}
        body = blocks_sse(model, blocks, "end_turn", usage, "msg_scripted_stream")
        return self.reply_raw(200, body, "text/event-stream")

    def reply_tool_use(
        self, name: str, arguments: dict[str, Any], tool_id: str = "toolu_scripted"
    ) -> Self:
        """A turn that calls one tool. Use `reply` or `reply_stream` for the turn after it."""
        blocks = [{"type": "tool_use", "id": tool_id, "name": name, "input": arguments}]
        usage = {"input_tokens": 10, "output_tokens": 5}
        body = blocks_sse("scripted", blocks, "tool_use", usage, f"msg_{tool_id}")
        return self.reply_raw(200, body, "text/event-stream")

    def reply_cut_stream(self, text: str = "partial") -> Self:
        self.queue.append(Scripted(text=text, cut_stream=True))
        return self


class ScriptedRoute(_Replies):
    """Replies kept for requests whose system prompt contains `marker` (row #24): agents that run
    at the same time each get their own script, in order, whatever order their requests arrive."""

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.queue = deque()


def system_text(body: Any) -> str:
    """The request's system prompt as one string: a plain string, or the text of its blocks."""
    system = body.get("system") if isinstance(body, dict) else None
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n".join(str(block.get("text", "")) for block in system if isinstance(block, dict))
    return ""


class ScriptedLLM(_Replies):
    """`with ScriptedLLM() as llm: llm.reply("hi"); client = anthropic_client(base_url=llm.url)`.

    By default every request takes the next reply from one queue. `route(marker)` keeps a separate
    queue for requests whose system prompt contains `marker`; a request that matches no route still
    takes from the default queue, so a script with no routes behaves exactly as before."""

    def __init__(self, *, require_placeholder: bool = True, port: int = 0) -> None:
        self.queue = deque()
        self.routes: list[ScriptedRoute] = []
        self.requests: list[dict[str, Any]] = []
        self.require_placeholder = require_placeholder
        self._lock = threading.Lock()
        app = Starlette(routes=[Route("/{path:path}", self._handle, methods=["GET", "POST"])])
        self._server = ServerThread(app, port)

    def route(self, marker: str) -> ScriptedRoute:
        """The first route whose marker the system prompt contains wins."""
        if not marker:
            raise ValueError("a route needs a marker")
        route = ScriptedRoute(marker)
        self.routes.append(route)
        return route

    def _queue_for(self, body: Any) -> deque[Scripted]:
        if not self.routes:
            return self.queue
        text = system_text(body)
        for route in self.routes:
            if route.marker in text:
                return route.queue
        return self.queue

    @property
    def url(self) -> str:
        return self._server.url

    def __enter__(self) -> ScriptedLLM:
        self._server.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.stop()

    async def _handle(self, request: Request) -> Response:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
        headers = {k.lower(): v for k, v in request.headers.items()}
        with self._lock:
            self.requests.append(
                {
                    "path": request.url.path,
                    "query": request.url.query,
                    "headers": headers,
                    "body": body,
                }
            )
            n = len(self.requests)
            queue = self._queue_for(body)
            item = queue.popleft() if queue else None
        if self.require_placeholder:
            creds = [headers.get("x-api-key"), headers.get("authorization")]
            if PLACEHOLDER_API_KEY not in creds and f"Bearer {PLACEHOLDER_API_KEY}" not in creds:
                return JSONResponse(
                    {"type": "error", "error": {"type": "authentication_error"}}, 401
                )
        if item is None:
            return JSONResponse(
                {
                    "type": "error",
                    "error": {"type": "invalid_request_error", "message": "script empty"},
                },
                400,
            )
        if item.raw is not None:
            status, ctype, text = item.raw
            return Response(content=text.encode(), status_code=status, media_type=ctype)
        model = str(body.get("model", "scripted"))
        msg_id = f"msg_scripted{n:04d}"
        text = item.text or ""
        if body.get("stream"):
            sse = message_sse(model, text, item.usage, msg_id, cut=item.cut_stream)
            return Response(content=sse.encode(), media_type="text/event-stream")
        return JSONResponse(message_json(model, text, item.usage, msg_id))
