"""A canned model for `make e2e-scripted` (SA-02; CHK-01 from row #16): all real but the model.

The reply depends on the request, not on arrival order, so journeys in parallel Playwright workers
never take each other's replies. The shopper message (inside `<shopper_message>`) picks a script,
and the number of assistant turns already in the request picks its step.

- The smoke question, and any message no script names, takes two steps: a `retrieve_context` call,
  then an answer citing P0011, which real retrieval returns for that search, so the citation check
  still means something.
- The cart journey's messages (`CART_SCRIPTS`, shared with `apps/portal/e2e/cart.e2e.ts` through
  `apps/portal/e2e/cart.json`) each call one cart tool, then answer. The real mcp-catalog runs the
  tool, and the gateway's cart panel shows its result. Nothing here decides what the cart holds.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import threading
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from northwind_assistant.tools import ALLOWED_TOOL
from northwind_common import catalog_tool
from northwind_common.llm import PLACEHOLDER_API_KEY
from northwind_llm_replay.scripted import ServerThread, blocks_sse

REPO = Path(__file__).resolve().parents[3]
CART = json.loads((REPO / "apps/portal/e2e/cart.json").read_text(encoding="utf-8"))

Step = tuple[str, Any]  # ("tool", (name, arguments)) or ("text", chunks)

DEFAULT: list[Step] = [
    ("tool", (ALLOWED_TOOL, {"query": "waterproof rating of the Brindlemoor Cairn Storm Jacket"})),
    ("text", ("Canned answer ", "for the browser path ", "[P0011].")),
]


def _cart_tool(name: str) -> str:
    return f"mcp__{catalog_tool.SERVER_NAME}__{name}"


CART_SCRIPTS: dict[str, list[Step]] = {
    step["message"]: [
        ("tool", (_cart_tool(step["tool"]), step["arguments"])),
        ("text", tuple(step["answer"])),
    ]
    for step in CART["steps"]
}

_SHOPPER = re.compile(r"<shopper_message>\s*(.*?)\s*</shopper_message>", re.DOTALL)
USAGE = {"input_tokens": 10, "output_tokens": 5}


def shopper_message(body: dict[str, Any]) -> str:
    """The shopper's words in the first user message, as `user_prompt` wrote them (unescaped)."""
    messages = body.get("messages") or []
    content = messages[0].get("content") if messages else ""
    if isinstance(content, list):
        content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    found = _SHOPPER.search(str(content or ""))
    return html.unescape(found.group(1)) if found else ""


def step_of(body: dict[str, Any]) -> Step | None:
    script = CART_SCRIPTS.get(shopper_message(body), DEFAULT)
    done = sum(1 for m in body.get("messages") or [] if m.get("role") == "assistant")
    return script[done] if done < len(script) else None


def reply(body: dict[str, Any]) -> Response:
    step = step_of(body)
    if step is None:
        error = {"type": "invalid_request_error", "message": "script empty"}
        return JSONResponse({"type": "error", "error": error}, 400)
    kind, value = step
    if kind == "tool":
        name, arguments = value
        blocks = [{"type": "tool_use", "id": "toolu_scripted", "name": name, "input": arguments}]
        sse = blocks_sse("scripted", blocks, "tool_use", USAGE, "msg_scripted_tool")
    else:
        sse = blocks_sse(
            "scripted", [{"type": "text", "chunks": list(value)}], "end_turn", USAGE, "msg_scripted"
        )
    return Response(content=sse.encode(), media_type="text/event-stream")


# SA-01 PR 2: every request this model receives, so the checkout journey can show Pay (demo) makes
# none (condition (3)). Read with GET /_requests; the count route itself is not counted.
_REQUESTS = {"count": 0}
_COUNT_LOCK = threading.Lock()


async def requests_seen(_request: Request) -> Response:
    with _COUNT_LOCK:
        return JSONResponse({"requests": _REQUESTS["count"]})


async def handle(request: Request) -> Response:
    with _COUNT_LOCK:
        _REQUESTS["count"] += 1
    headers = {k.lower(): v for k, v in request.headers.items()}
    creds = [headers.get("x-api-key"), headers.get("authorization")]
    if PLACEHOLDER_API_KEY not in creds and f"Bearer {PLACEHOLDER_API_KEY}" not in creds:
        return JSONResponse({"type": "error", "error": {"type": "authentication_error"}}, 401)
    raw = await request.body()
    return reply(json.loads(raw) if raw else {})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    app = Starlette(
        routes=[
            Route("/_requests", requests_seen, methods=["GET"]),
            Route("/{path:path}", handle, methods=["GET", "POST"]),
        ]
    )
    server = ServerThread(app, args.port)
    server.start()
    try:
        threading.Event().wait()
    finally:
        server.stop()


if __name__ == "__main__":
    main()
