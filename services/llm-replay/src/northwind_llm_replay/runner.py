"""The key-holding runner and the client side of a spending run (CC-27 F1/F3, decision 0005).

The runner is the one process that reads the API key. It is the record/replay proxy in live mode
plus a `RunnerGate`, started from the user's terminal in a separate clean clone that the sandbox
cannot write. It writes only the ledger and never touches a worktree.

The user opens a window: one suite, a run budget, a time limit. The first caller claims it and gets
a random token that it keeps in memory, and every later request must present that token. Release,
the time limit or idleness closes the window and the runner exits. One window is one run, so with
no window nothing spends, whatever a sandboxed command does.

`proxy_config` is how everything else gets a proxy: in record or live mode it claims the window and
points the in-process proxy at the runner. Nothing on that path holds a ledger or a key.
"""

from __future__ import annotations

import atexit
import io
import json
import math
import os
import re
import secrets
import socket
import stat
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx2 as httpx
from dotenv import dotenv_values
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from northwind_common import localenv
from northwind_common.llm import DEFAULT_MAX_OUTPUT_TOKENS
from northwind_llm_replay.ledger import Ledger, new_id
from northwind_llm_replay.proxy import (
    DEFAULT_UPSTREAM,
    FORWARD_HEADERS,
    PRICED_PATH,
    Proxy,
    ProxyConfig,
    auth_verdict,
    error_response,
    same_secret,
)
from northwind_llm_replay.scan import KEY_FRAGMENT_LEN, key_fragments
from northwind_llm_replay.scripted import ServerThread

ALLOWED_MODELS = frozenset({"claude-sonnet-5-5", "claude-haiku-5-5"})
ALLOWED_QUERIES = frozenset({"", "beta=true"})  # the Agent SDK's CLI sends beta=true
# Measured from every recorded request. The worst case the ledger reserves only bounds a request
# built from these: server tools, URL sources and MCP servers bill what it cannot see, so they are
# refused. A new need widens these lists in a reviewed merge.
ALLOWED_FIELDS = frozenset(
    {
        "context_management",
        "max_tokens",
        "messages",
        "metadata",
        "model",
        "output_config",
        "stream",
        "system",
        "thinking",
        "tools",
    }
)
ALLOWED_BETAS = frozenset(
    {
        "claude-code-20250219",
        "context-management-2025-06-27",
        "effort-2025-11-24",
        "interleaved-thinking-2025-05-14",
        "per-turn-control-2026-07-01",
        "thinking-display-updates-2026-08-18",
        "thinking-token-count-2026-05-13",
    }
)
# Each block type and the keys it may carry. Text is recorded. The other four are what the assistant
# will send back in a tool loop. Images and documents bill by pixels and pages, which a reservation
# cannot see, so they stay out until a reviewed need.
BLOCK_KEYS = {
    "text": frozenset({"type", "text", "cache_control"}),
    "thinking": frozenset({"type", "thinking", "signature"}),
    "redacted_thinking": frozenset({"type", "data"}),
    "tool_use": frozenset({"type", "id", "name", "input", "cache_control"}),
    "tool_result": frozenset({"type", "tool_use_id", "content", "is_error", "cache_control"}),
}
MESSAGE_KEYS = frozenset({"role", "content", "output_config"})
ROLES = frozenset({"user", "assistant", "system"})
OUTPUT_CONFIG_KEYS = frozenset({"effort", "format"})
THINKING_KEYS = frozenset({"type", "display"})
THINKING_TYPES = frozenset({"adaptive", "between_tools"})
METADATA_KEYS = frozenset({"user_id"})
CONTEXT_EDIT = {"type": "clear_thinking_20251015", "keep": "all"}
TEXT_FIELDS = frozenset({"text", "thinking", "partial_json"})
KEY_PREFIX = re.compile(r"sk-ant-[a-z0-9]+-")  # public: the same in every key of a kind
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_CLAIM_BYTES = 1024
MAX_IN_FLIGHT = 4
MAX_REFUSALS = 5  # a real run has none, so a few mean someone is probing
MAX_NOISE_LINES = 20  # callers without the token must not scroll a warning off the terminal
IN_FLIGHT_GRACE_SECONDS = 60


class RunnerError(ValueError):
    """The runner refuses to start. The CLI prints the message and exits 2."""


class RunnerUnavailableError(RuntimeError):
    """A spending run could not claim a window."""


# -- starting the runner ---------------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    try:
        done = subprocess.run(  # noqa: S603 - fixed git arguments, no shell
            ["git", *args],  # noqa: S607 - git from PATH is the developer's own
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunnerError(f"git {args[0]} failed in {root}") from exc
    return done.stdout.strip()


def preflight(root: Path, expected: Path) -> None:
    """Refuse anything but a clean clone at origin/main. This catches accidents, not attacks: a
    tampered copy could delete this check, so always start it with `make -C <runner>`."""
    if root.resolve() != expected.resolve():
        raise RunnerError(f"the runner holds the key: run it from {expected}, not {root}")
    if localenv.is_linked_worktree(root):
        raise RunnerError(f"{root} is a linked worktree; the sandbox can write its git directory")
    if _git(root, "status", "--porcelain"):
        raise RunnerError(f"{root} is not clean")
    if _git(root, "rev-parse", "HEAD") != _git(root, "rev-parse", "origin/main"):
        raise RunnerError(f"{root} is not at origin/main: run `make runner-sync`")


def secret_part(key: str) -> str:
    """The key without its public prefix. A fragment overlapping the prefix would hold few unknown
    characters: `sk-ant-api03-` plus three guesses is 16, and a hit on it could be extended one
    character at a time. Every fragment must be all secret."""
    prefix = KEY_PREFIX.match(key)
    return key[prefix.end() :] if prefix else key


def load_runner_key(path: Path) -> str:
    """ANTHROPIC_API_KEY alone, from a private regular file. Nothing else in it is used."""
    try:
        mode = path.lstat().st_mode
        parent_mode = path.parent.lstat().st_mode
    except FileNotFoundError as exc:
        raise RunnerError(f"key file {path} is missing") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise RunnerError(f"key file {path} is a symlink or not a regular file")
    if mode & 0o077:
        raise RunnerError(f"key file {path} must be 0600")
    if parent_mode & 0o077:
        raise RunnerError(f"key directory {path.parent} must be 0700")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding="utf-8") as handle:
        key = dotenv_values(stream=io.StringIO(handle.read())).get("ANTHROPIC_API_KEY")
    if not key:
        raise RunnerError(f"ANTHROPIC_API_KEY is not set in {path}")
    if not KEY_PREFIX.match(key):  # an unknown prefix would stay in the fragments
        raise RunnerError(f"ANTHROPIC_API_KEY in {path} does not have a known key prefix")
    if len(secret_part(key)) < KEY_FRAGMENT_LEN:  # the fragment scan would have nothing to match
        raise RunnerError(f"ANTHROPIC_API_KEY in {path} is too short to be a key")
    return key


def refuse_if_port_taken(port: int) -> None:
    """A connect, not a bind: with uvicorn's SO_REUSEADDR a bind could succeed on 127.0.0.1 beside a
    listener on 0.0.0.0, and the sandbox can open listeners."""
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RunnerError(f"port {port} is already in use: is another runner open?")


def checked_run_budget(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise RunnerError(f"run budget must be a finite amount above 0, not {value!r}")
    return value


# -- the window ------------------------------------------------------------------------------


class Window:
    """One suite, one claimed run. Thread-safe: the server and the main loop both use it."""

    def __init__(
        self,
        *,
        suite: str,
        minutes: float,
        idle_minutes: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.suite = suite
        self.closed = threading.Event()
        self.reason: str | None = None
        self.in_flight = 0
        self._clock = clock
        self._deadline = clock() + minutes * 60
        self._idle_seconds = idle_minutes * 60
        self._last_activity = clock()
        self._token: str | None = None
        self._lock = threading.Lock()

    def claim(self) -> str | None:
        """A new token, or None if the window is closed or already claimed."""
        with self._lock:
            if self._token is not None or self.closed.is_set():
                return None
            self._last_activity = self._clock()
            self._token = secrets.token_urlsafe(32)
            return self._token

    def credential(self) -> str | None:
        """The token while the window is open and in time, otherwise None, which refuses all."""
        with self._lock:
            if self.closed.is_set() or self._clock() >= self._deadline:
                return None
            return self._token

    def begin(self) -> bool:
        with self._lock:
            if self.in_flight >= MAX_IN_FLIGHT:
                return False
            self.in_flight += 1
            return True

    def end(self) -> None:
        with self._lock:
            self.in_flight -= 1
            self._last_activity = self._clock()

    def close(self, reason: str) -> None:
        with self._lock:
            if not self.closed.is_set():
                self.reason = reason
                self.closed.set()

    def tick(self) -> None:
        now = self._clock()
        with self._lock:
            idle = self.in_flight == 0 and now - self._last_activity >= self._idle_seconds
        if now >= self._deadline:
            self.close("time limit reached")
        elif idle:
            self.close("idle")


# -- what the runner lets through ------------------------------------------------------------


def _text_leaves(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for name, item in value.items():
            if name in TEXT_FIELDS and isinstance(item, str):
                yield item
            else:
                yield from _text_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _text_leaves(item)


def readable_forms(text: str) -> list[str]:
    """The raw text, each JSON document decoded (so `\\uXXXX` escapes count), and all model text
    joined up, because a key split across two SSE deltas only shows once they are joined."""
    decoded: list[str] = []
    leaves: list[str] = []
    for candidate in [text, *(s[5:] for s in text.splitlines() if s.startswith("data:"))]:
        try:  # ValueError too: an oversized integer. Text that cannot parse is still scanned raw.
            document = json.loads(candidate)
            decoded.append(json.dumps(document, ensure_ascii=False))
            leaves.extend(_text_leaves(document))
        except (ValueError, RecursionError):
            continue
    return [text, *decoded, "".join(leaves)]


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({name for name, _ in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


def _has_one_meaning(raw: bytes) -> bool:
    """Strict UTF-8, because `json.loads` would accept other encodings the fragment scan cannot
    read. No duplicate keys, because the gate reads the last and upstream might read the first."""
    try:
        json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (ValueError, RecursionError):
        return False
    return True


def _content_allowed(content: Any) -> bool:
    if isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    for block in content:
        kind = block.get("type") if isinstance(block, dict) else None
        keys = BLOCK_KEYS.get(kind) if isinstance(kind, str) else None
        if keys is None or set(block) - keys:
            return False
        if block["type"] == "tool_result" and not _content_allowed(block.get("content", "")):
            return False
    return True


def _body_content_allowed(body: dict[str, Any]) -> bool:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) - MESSAGE_KEYS
            or message.get("role") not in ROLES
            or not _keys_within(message.get("output_config", {}), OUTPUT_CONFIG_KEYS)
            or not _content_allowed(message.get("content"))
        ):
            return False
    return _content_allowed(body.get("system", ""))


def _keys_within(value: Any, keys: frozenset[str]) -> bool:
    return isinstance(value, dict) and not set(value) - keys


def _settings_allowed(body: dict[str, Any]) -> bool:
    """The nested settings, pinned to what is recorded: an unknown one can change what is billed."""
    thinking = body.get("thinking", {})
    edits = body.get("context_management", {"edits": []})
    return (
        _keys_within(body.get("output_config", {}), OUTPUT_CONFIG_KEYS)
        and _keys_within(body.get("metadata", {}), METADATA_KEYS)
        and _keys_within(thinking, THINKING_KEYS)
        and (not thinking or thinking.get("type") in THINKING_TYPES)
        and _keys_within(edits, frozenset({"edits"}))
        and isinstance(edits.get("edits"), list)
        and all(edit == CONTEXT_EDIT for edit in edits["edits"])
    )


def _tools_allowed(body: dict[str, Any]) -> bool:
    """Client tools only: a server tool bills and acts outside the reservation."""
    tools = body.get("tools", [])
    return isinstance(tools, list) and all(
        isinstance(tool, dict) and tool.get("type", "custom") == "custom" for tool in tools
    )


class RunnerGate:
    def __init__(
        self,
        key: str,
        ledger: Ledger,
        out: Callable[[str], None],
        close: Callable[[str], None],
        max_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self._fragments = key_fragments(secret_part(key))
        self._ledger = ledger
        self._out = out
        self._close = close
        self._max_tokens = max_tokens
        self._refusals = 0

    def refuse(
        self,
        method: str,
        raw_path: str,
        query: str,
        raw: bytes,
        body: Any,
        headers: dict[str, str],
    ) -> tuple[str, Response] | None:
        if method != "POST" or raw_path != PRICED_PATH or query not in ALLOWED_QUERIES:
            message = "the runner forwards only POST /v1/messages"
            return "refused-path", error_response(403, "permission", False, message)
        if not isinstance(body, dict) or not _has_one_meaning(raw):
            message = "request body must be a JSON object with no duplicate keys"
            return "refused-body", error_response(400, "validation", False, message)
        if set(body) - ALLOWED_FIELDS:
            message = f"the runner forwards only the fields {sorted(ALLOWED_FIELDS)}"
            return "refused-field", error_response(400, "validation", False, message)
        if body.get("model") not in ALLOWED_MODELS:
            message = f"the runner accepts only {sorted(ALLOWED_MODELS)}"
            return "refused-model", error_response(400, "validation", False, message)
        max_tokens = body.get("max_tokens")
        if (
            not isinstance(max_tokens, int)
            or isinstance(max_tokens, bool)
            or not 0 < max_tokens <= self._max_tokens
        ):
            message = f"max_tokens must be an integer from 1 to {self._max_tokens}"
            return "refused-max-tokens", error_response(400, "validation", False, message)
        if not _body_content_allowed(body):
            message = "only text, thinking and tool blocks with their usual keys"
            return "refused-content", error_response(400, "validation", False, message)
        if not _settings_allowed(body):
            message = "the runner forwards only the recorded settings"
            return "refused-setting", error_response(400, "validation", False, message)
        if not _tools_allowed(body):
            message = "the runner forwards client tools only"
            return "refused-tool", error_response(400, "validation", False, message)
        betas = {b.strip() for b in headers.get("anthropic-beta", "").split(",") if b.strip()}
        if betas - ALLOWED_BETAS:
            message = "the runner does not forward that anthropic-beta value"
            return "refused-beta", error_response(400, "validation", False, message)
        if not all(headers[name].isascii() for name in FORWARD_HEADERS if name in headers):
            message = "forwarded headers must be ASCII"
            return "refused-header", error_response(400, "validation", False, message)
        return None

    def leaks(self, text: str) -> bool:
        return any(f in form for form in readable_forms(text) for f in self._fragments)

    def request_leaks(self, raw: bytes, headers: dict[str, str]) -> bool:
        forwarded = [headers[name] for name in FORWARD_HEADERS if name in headers]
        return self.leaks(raw.decode("utf-8", errors="replace")) or any(map(self.leaks, forwarded))

    def blocked(self) -> JSONResponse:
        return error_response(502, "permission", False, "the runner blocked this: see its terminal")

    def report(self, entry: dict[str, Any]) -> None:
        """One line per request for the user's terminal. Status and numbers only: nothing the
        caller chose is printed, so neither content nor terminal escapes get through."""
        stamp = datetime.now().strftime("%H:%M:%S")  # noqa: DTZ005 - a local clock for a person
        outcome = str(entry.get("outcome"))
        if outcome == "blocked-key":
            # A hit ends the window, so guesses cannot go on from it. Misses before it still told
            # the caller something, which is why the key must be rotated.
            self._out(f"{stamp} BLOCKED a key fragment: closing the window. ROTATE THE KEY.")
            self._close("a key fragment was blocked. ROTATE THE KEY")
        if outcome not in ("live", "blocked-key"):
            self._out(f"{stamp} REFUSED {outcome}")
        # A 400 from upstream counts: the gate passed it, and no real request is that malformed.
        if (outcome not in ("live", "blocked-key")) or entry.get("status") == 400:
            self._refusals += 1
            if self._refusals >= MAX_REFUSALS:
                self._close(f"{MAX_REFUSALS} refusals")
        if "cost_usd" not in entry:
            return
        usage = entry.get("usage") or {}
        totals = self._ledger.totals()
        cap = self._ledger.run_budget_usd or 0.0
        self._out(
            f"{stamp} {entry.get('model')} status={entry.get('status')} "
            f"first_byte={entry.get('first_byte_ms', '?')}ms "
            f"in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')} "
            f"${entry['cost_usd']:.6f} | run ${totals.run_spent_usd:.6f} of ${cap:.2f} | "
            f"ledger ${totals.spent_usd:.6f} of ${self._ledger.budget_usd:.2f}"
        )
        if totals.run_spent_usd >= cap:
            self._close("the run budget is spent")


def _is_local(request: Request) -> bool:
    """DNS rebinding would otherwise let a web page read the claim's reply."""
    return request.headers.get("host", "").rpartition(":")[0] == "127.0.0.1"


def _size_is_declared_within(request: Request, limit: int) -> bool:
    length = request.headers.get("content-length", "0")
    return "transfer-encoding" not in request.headers and length.isdigit() and int(length) <= limit


def _foreign_host() -> Response:
    return error_response(403, "permission", False, "the runner only answers on 127.0.0.1")


def create_runner_app(
    window: Window,
    key: str,
    ledger: Ledger,
    cassette_root: Path,
    *,
    upstream_url: str = DEFAULT_UPSTREAM,
    out: Callable[[str], None] = print,
) -> tuple[Starlette, Proxy]:
    """`upstream_url` is for tests: `serve_window`, the only real caller, does not pass it."""
    gate = RunnerGate(key, ledger, out, window.close)
    proxy = Proxy(
        ProxyConfig(
            mode="live",
            suite=window.suite,
            cassette_root=cassette_root,
            ledger=ledger,
            upstream_url=upstream_url,
            key_loader=lambda: key,
            credential=window.credential,
            gate=gate,
        )
    )
    noise = 0

    def out_capped(line: str) -> None:
        nonlocal noise
        noise += 1
        if noise <= MAX_NOISE_LINES:
            out(line)
        if noise == MAX_NOISE_LINES:
            out("further refused callers are not shown")

    async def claim(request: Request) -> Response:
        if not _is_local(request):
            return _foreign_host()
        if not _size_is_declared_within(request, MAX_CLAIM_BYTES):
            return error_response(413, "validation", False, "a claim is a small JSON object")
        # A browser page cannot send JSON here without a preflight this server never answers.
        if request.headers.get("content-type") != "application/json":
            return error_response(400, "validation", False, "a claim must be JSON")
        try:
            body = json.loads(await request.body())
        except (ValueError, RecursionError):
            return error_response(400, "validation", False, "a claim must be JSON")
        if not isinstance(body, dict) or body.get("suite") != window.suite:
            return error_response(403, "permission", False, "this window is for another suite")
        token = window.claim()
        if token is None:
            out_capped("REFUSED a claim: the window is closed or already claimed")
            message = "the window is closed or already claimed; open a new one"
            return error_response(409, "business", False, message)
        out("claimed: the run has started")
        return JSONResponse({"token": token})

    async def release(request: Request) -> Response:
        token = window.credential()
        if (
            not _is_local(request)
            or token is None
            or not same_secret(request.headers.get("x-api-key", ""), token)
        ):
            return error_response(401, "permission", False, "not the run that claimed this window")
        window.close("released")
        return JSONResponse({"released": True})

    async def forward(request: Request) -> Response:
        if not _is_local(request):
            return _foreign_host()
        # Credentials first: a caller without the token reads no body and moves no idle clock.
        headers = {k.lower(): v for k, v in request.headers.items()}
        if auth_verdict(headers, window.credential()) != "placeholder":
            out_capped("REFUSED refused-auth")
            return error_response(401, "permission", False, "not the run that claimed this window")
        if not _size_is_declared_within(request, MAX_BODY_BYTES):
            message = f"a request body must declare its size, at most {MAX_BODY_BYTES} bytes"
            return error_response(413, "validation", False, message)
        if not window.begin():
            return error_response(429, "transient", True, "too many requests in flight")
        try:
            return await proxy.handle(request)
        except (ValueError, TypeError, RecursionError):  # TypeError: a list where a name belongs
            gate.report({"outcome": "refused-unreadable"})
            return error_response(400, "validation", False, "the request could not be read")
        finally:
            window.end()

    methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]
    routes = [
        Route("/_runner/claim", claim, methods=["POST"]),
        Route("/_runner/release", release, methods=["POST"]),
        Route("/{path:path}", forward, methods=methods),
    ]
    return Starlette(routes=routes), proxy


def serve_window(
    suite: str, run_budget: float, minutes: float, idle_minutes: float, total_budget: float
) -> int:
    """Open one window and block until it closes. The upstream is DEFAULT_UPSTREAM, always."""
    checked_run_budget(run_budget)
    if minutes <= 0 or idle_minutes <= 0:
        raise RunnerError("the time limit and the idle limit must be above 0")
    root = localenv.worktree_root()
    preflight(root, localenv.runner_checkout())
    key = load_runner_key(localenv.env_file())
    port = localenv.runner_port()
    refuse_if_port_taken(port)
    ledger = Ledger(
        localenv.ledger_path(),
        budget_usd=total_budget,
        run_budget_usd=run_budget,
        run_id=f"runner-{new_id()[:12]}",
        suite=suite,
    )
    window = Window(suite=suite, minutes=minutes, idle_minutes=idle_minutes)
    app, _ = create_runner_app(window, key, ledger, root / "cassettes")
    server = ServerThread(app, port=port)
    try:
        server.start()
    except RuntimeError as exc:
        raise RunnerError(f"port {port} is already in use: is another runner open?") from exc
    print(
        f"window open: suite {suite}, ${run_budget:.2f}, {minutes:g} min, idle {idle_minutes:g} min"
        f", on {server.url}. Ctrl-C closes it.",
        flush=True,
    )
    try:
        while not window.closed.wait(1.0):
            window.tick()
    except KeyboardInterrupt:
        window.close("stopped from the terminal")
    deadline = time.monotonic() + IN_FLIGHT_GRACE_SECONDS
    while window.in_flight and time.monotonic() < deadline:
        time.sleep(0.2)
    server.stop()
    print(f"window closed: {window.reason}", flush=True)
    return 0


# -- the sandboxed side ----------------------------------------------------------------------


@dataclass(frozen=True)
class RunnerSession:
    url: str
    suite: str
    token: str = field(repr=False)


# One claim per process and suite: pytest builds a proxy per test. In memory only, never in the
# environment or a file, so a command that is not this run has nothing to read.
SESSIONS: dict[tuple[str, str], RunnerSession] = {}


def _release(session: RunnerSession) -> None:
    try:
        httpx.post(
            f"{session.url}/_runner/release",
            headers={"x-api-key": session.token},
            timeout=5,
            trust_env=False,
        )
    except httpx.HTTPError:
        pass  # the idle limit closes the window instead


def claim(suite: str, url: str) -> RunnerSession:
    if (url, suite) in SESSIONS:
        return SESSIONS[(url, suite)]
    how = (
        f"In your own terminal: make -C ~/.northwind/runner llm-runner SUITE={suite} "
        "RUN_BUDGET=<usd>"
    )
    try:
        reply = httpx.post(
            f"{url}/_runner/claim", json={"suite": suite}, timeout=5, trust_env=False
        )
    except httpx.HTTPError as exc:
        raise RunnerUnavailableError(f"no runner window is open at {url}. {how}") from exc
    if reply.status_code != 200:
        message = reply.json().get("message", reply.text)
        raise RunnerUnavailableError(f"the runner refused the claim: {message}. {how}")
    session = RunnerSession(url=url, suite=suite, token=reply.json()["token"])
    SESSIONS[(url, suite)] = session
    atexit.register(_release, session)
    return session


def proxy_config(
    mode: str,
    suite: str,
    *,
    cassette_root: Path,
    log_path: Path | None,
    match: str = "hash",
    url: str | None = None,
) -> ProxyConfig:
    """The config for every proxy outside the runner. No ledger and no key on this path: in record
    or live mode the upstream is the runner, and a run with no open window stops here."""
    if mode not in ("record", "live"):
        return ProxyConfig(mode=mode, suite=suite, cassette_root=cassette_root, match=match)
    session = claim(suite, url or localenv.runner_url())
    return ProxyConfig(
        mode=mode,
        suite=suite,
        cassette_root=cassette_root,
        log_path=log_path,
        match=match,
        upstream_url=session.url,
        key_loader=lambda: session.token,
        via_runner=True,
    )
