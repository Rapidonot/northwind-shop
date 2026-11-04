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
from typing import Any

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


class ScriptedLLM:
    """`with ScriptedLLM() as llm: llm.reply("hi"); client = anthropic_client(base_url=llm.url)`."""

    def __init__(self, *, require_placeholder: bool = True) -> None:
        self.queue: deque[Scripted] = deque()
        self.requests: list[dict[str, Any]] = []
        self.require_placeholder = require_placeholder
        self._lock = threading.Lock()
        app = Starlette(routes=[Route("/{path:path}", self._handle, methods=["GET", "POST"])])
        self._server = ServerThread(app)

    def reply(self, text: str, **usage: int) -> ScriptedLLM:
        self.queue.append(
            Scripted(text=text, usage=usage or {"input_tokens": 10, "output_tokens": 5})
        )
        return self

    def reply_raw(
        self, status: int, body: str, content_type: str = "application/json"
    ) -> ScriptedLLM:
        self.queue.append(Scripted(raw=(status, content_type, body)))
        return self

    def reply_cut_stream(self, text: str = "partial") -> ScriptedLLM:
        self.queue.append(Scripted(text=text, cut_stream=True))
        return self

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
            self.requests.append({"path": request.url.path, "headers": headers, "body": body})
            n = len(self.requests)
            item = self.queue.popleft() if self.queue else None
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
