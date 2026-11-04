"""The record/replay proxy that sits behind ANTHROPIC_BASE_URL for every LLM call (FOUND-2).

Modes (LLM_MODE):
- replay (default, forced in CI): serve cassettes only; a miss is a structured error with a
  diff against the nearest cassette. Never loads the API key, never goes upstream.
- record: serve existing cassettes; otherwise admit through the budget guard, call upstream,
  write the cassette (after the scanner), settle the ledger.
- live: admit, call upstream, settle; no cassette.

Auth: callers must present exactly one credential and it must be the placeholder key, either
as `x-api-key` (the Agent SDK's CLI) or as `Authorization: Bearer <placeholder>` (`anthropic`
2.x sends API keys that way). Anything else, such as an OAuth token, a real key or two
credentials, is refused. This is a hard pass/fail that keeps runtime calls off the developer's
subscription (decision 0001).
The real key is loaded from .env by `dotenv_values` (never os.environ) on the first upstream
call and only ever set on the upstream request.

Test context: a base URL of `http://host:port/_t/<test-id>` (`encode_test_id`) tags requests
with a test ID
(used by sequence matching and stored on cassettes); the prefix is stripped before matching.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import anyio
import httpx2 as httpx
from dotenv import dotenv_values
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from northwind_common.llm import PLACEHOLDER_API_KEY
from northwind_llm_replay.cassette import Cassette, CassetteStore, DirtyCassetteError
from northwind_llm_replay.ledger import BudgetExceededError, Ledger, Reservation, now_iso
from northwind_llm_replay.pricing import UnpricedModelError, price_for, worst_case_usd
from northwind_llm_replay.scrub import match_key, request_hash, scrub

MODES = ("replay", "record", "live")
DEFAULT_UPSTREAM = "https://api.anthropic.com"
# Paths the proxy will send upstream. Anything else is refused until a task needs it.
PRICED_PATH = "/v1/messages"
FREE_PATHS = frozenset({"/v1/messages/count_tokens"})
# Inbound headers forwarded upstream (auth is never forwarded; the proxy sets its own).
FORWARD_HEADERS = ("anthropic-version", "anthropic-beta", "content-type", "accept", "user-agent")
# Inbound header values written to the request log. Names of all headers are logged.
LOGGED_HEADER_VALUES = ("anthropic-version", "anthropic-beta", "content-type", "user-agent")
# Failures that prove the request never left: the only ones released at zero cost. After the
# connection opens the request may have been billed, so anything else is charged the worst case.
# Enough to find a cassette by prefix, and too short to hold a 13-digit run the PAN check flags.
LOG_HASH_CHARS = 12
NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def load_api_key(env_file: Path) -> str:
    """Read ANTHROPIC_API_KEY from .env without touching os.environ."""
    value = dotenv_values(env_file).get("ANTHROPIC_API_KEY") if env_file.exists() else None
    if not value:
        raise RuntimeError(f"ANTHROPIC_API_KEY is not set in {env_file}")
    return value


@dataclass
class ProxyConfig:
    mode: str = "replay"  # replay | record | live
    suite: str = "default"
    cassette_root: Path = Path("cassettes")
    ledger: Ledger | None = None
    env_file: Path = Path(".env")
    log_path: Path | None = None
    upstream_url: str = DEFAULT_UPSTREAM
    match: str = "hash"  # hash | sequence
    key_loader: Callable[[], str] | None = None
    secret_for_scan: str | None = None  # the real key, if known, for fragment scanning

    def __post_init__(self) -> None:
        # handle() sends anything but replay upstream, so a typo must not get that far.
        if self.mode not in MODES:
            raise ValueError(f"LLM_MODE must be one of {MODES}, not {self.mode!r}")


@dataclass
class ProxyState:
    misses: list[dict[str, Any]] = field(default_factory=list)
    scan_failures: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    seq: dict[str, int] = field(default_factory=dict)


def error_response(
    status: int, category: str, retryable: bool, message: str, **extra: Any
) -> JSONResponse:
    """Anthropic-shaped error (so SDKs parse it) with the lab's structured fields (rule 10)."""
    anthropic_type = {
        "permission": "authentication_error" if status == 401 else "permission_error",
        "validation": "invalid_request_error",
        "business": "invalid_request_error",
        "transient": "api_error",
    }[category]
    return JSONResponse(
        {
            "type": "error",
            "error": {"type": anthropic_type, "message": message},
            "isError": True,
            "errorCategory": category,
            "isRetryable": retryable,
            "message": message,
            **extra,
        },
        status_code=status,
    )


def auth_verdict(headers: dict[str, str]) -> str:
    """ "placeholder" if the only credential is the placeholder key, else "REJECTED"."""
    creds = [v for k, v in headers.items() if k in ("x-api-key", "authorization")]
    if len(creds) != 1:
        return "REJECTED"
    ok = (
        headers.get("x-api-key") == PLACEHOLDER_API_KEY
        or headers.get("authorization") == f"Bearer {PLACEHOLDER_API_KEY}"
    )
    return "placeholder" if ok else "REJECTED"


def encode_test_id(test_id: str) -> str:
    """A URL path segment for a test ID (node IDs contain `/`, `::` and brackets)."""
    raw = base64.urlsafe_b64encode(test_id.encode("utf-8")).decode("ascii").rstrip("=")
    return f"b64.{raw}"


def _decode_test_id(segment: str) -> str:
    if segment.startswith("b64."):
        raw = segment[4:]
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    return unquote(segment)


def split_test_prefix(raw_path: str) -> tuple[str | None, str]:
    if raw_path.startswith("/_t/"):
        rest = raw_path[len("/_t/") :]
        segment, _, tail = rest.partition("/")
        return _decode_test_id(segment), "/" + tail
    return None, raw_path


def parse_sse_usage(sse_text: str) -> tuple[dict[str, Any] | None, bool]:
    """(final usage, complete). Usage = message_start usage overlaid with message_delta usage.
    Final usage exists only once a message_delta carrying usage was seen."""
    usage: dict[str, Any] = {}
    final = False
    complete = False
    for block in sse_text.split("\n\n"):
        for line in block.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            kind = data.get("type")
            if kind == "message_start":
                usage.update(data.get("message", {}).get("usage") or {})
            elif kind == "message_delta" and data.get("usage"):
                usage.update({k: v for k, v in data["usage"].items() if v is not None})
                final = True
            elif kind == "message_stop":
                complete = True
    return (usage if final else None), complete


class Proxy:
    def __init__(self, cfg: ProxyConfig) -> None:
        self.cfg = cfg
        self.state = ProxyState()
        self.store = CassetteStore(cfg.cassette_root, cfg.suite, secret=cfg.secret_for_scan)
        self._key: str | None = None
        self._log_lock = threading.Lock()
        self._client: httpx.AsyncClient | None = None

    # -- helpers ---------------------------------------------------------------------------
    def _api_key(self) -> str:
        if self._key is None:
            loader = self.cfg.key_loader or (lambda: load_api_key(self.cfg.env_file))
            self._key = loader()
            if self.store._secret is None:
                self.store._secret = self._key
        return self._key

    def _log(self, entry: dict[str, Any]) -> None:
        self.state.calls.append({k: v for k, v in entry.items() if k != "body"})
        if self.cfg.log_path is None:
            return
        self.cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_lock, self.cfg.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")

    def _client_get(self) -> httpx.AsyncClient:
        if self._client is None:
            # trust_env=False: the real key must not route through an HTTPS_PROXY from the env.
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(600.0, connect=10.0), trust_env=False
            )
        return self._client

    def _replay(self, c: Cassette) -> Response:
        return Response(
            content=c.body.encode("utf-8"), status_code=c.status, media_type=c.content_type
        )

    # -- request handling ------------------------------------------------------------------
    async def handle(self, request: Request) -> Response:
        test_id, path = split_test_prefix(request.url.path)
        full_path = path + (f"?{request.url.query}" if request.url.query else "")
        raw = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            body: Any = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = None
        key = match_key(request.method, full_path, headers, scrub(body))
        h = request_hash(key)
        entry: dict[str, Any] = {
            "ts": now_iso(),
            "mode": self.cfg.mode,
            "method": request.method,
            "path": full_path,
            "test_id": test_id,
            # Credential header names are left out; `auth_via` says which form was used.
            "header_names": sorted(k for k in headers if k not in ("x-api-key", "authorization")),
            "auth_via": "api-key-header"
            if "x-api-key" in headers
            else ("bearer" if "authorization" in headers else None),
            "headers": {k: headers[k] for k in LOGGED_HEADER_VALUES if k in headers},
            "auth": auth_verdict(headers),
            "hash": h[:LOG_HASH_CHARS],
            "model": body.get("model") if isinstance(body, dict) else None,
            "max_tokens": body.get("max_tokens") if isinstance(body, dict) else None,
            "stream": bool(body.get("stream")) if isinstance(body, dict) else False,
            "body": body,
        }

        if entry["auth"] != "placeholder":
            entry["outcome"] = "refused-auth"
            self._log(entry)
            return error_response(
                401,
                "permission",
                False,
                "the LLM proxy only accepts the placeholder key as the single credential "
                "(build clients with northwind_common.llm)",
            )

        seq_no = None
        if test_id is not None:
            seq_no = self.state.seq.get(test_id, 0)
            self.state.seq[test_id] = seq_no + 1

        if self.cfg.mode in ("replay", "record"):
            hit = self.store.get(h)
            if (
                hit is None
                and self.cfg.match == "sequence"
                and test_id is not None
                and seq_no is not None
            ):
                hit = self.store.by_sequence(test_id, seq_no)
                if hit is not None:
                    self.state.stale.append(f"{test_id}#{seq_no}")
                    entry["stale"] = True
            if hit is not None:
                entry["outcome"] = "hit"
                self._log(entry)
                return self._replay(hit)
            if self.cfg.mode == "replay":
                diff = self.store.miss_diff(key)
                entry["outcome"] = "miss"
                self._log(entry)
                self.state.misses.append(
                    {"hash": h, "path": full_path, "test_id": test_id, "diff": diff}
                )
                return error_response(
                    400,
                    "validation",
                    False,
                    f"cassette miss in suite {self.cfg.suite!r} (model input changed; re-record "
                    f"with `make record SUITE={self.cfg.suite}`)",
                    diff=diff,
                )

        # record (miss) or live: go upstream through the guard.
        if path not in FREE_PATHS and path != PRICED_PATH:
            entry["outcome"] = "refused-path"
            self._log(entry)
            return error_response(
                403, "permission", False, f"the LLM proxy does not forward {path}"
            )

        reservation: Reservation | None = None
        if path == PRICED_PATH:
            if not isinstance(body, dict):
                entry["outcome"] = "refused-body"
                self._log(entry)
                return error_response(
                    400, "validation", False, "request body must be a JSON object"
                )
            if body.get("inference_geo") not in (None, "global"):
                entry["outcome"] = "refused-geo"
                self._log(entry)
                return error_response(
                    400,
                    "validation",
                    False,
                    "inference_geo is not priced by the ledger; leave it unset",
                )
            model, max_tokens = body.get("model"), body.get("max_tokens")
            if not isinstance(model, str) or not isinstance(max_tokens, int):
                entry["outcome"] = "refused-body"
                self._log(entry)
                return error_response(
                    400, "validation", False, "model and integer max_tokens are required"
                )
            try:
                price_for(model)
                worst = worst_case_usd(model, len(raw), max_tokens)
            except UnpricedModelError as e:
                entry["outcome"] = "refused-unpriced"
                self._log(entry)
                return error_response(400, "validation", False, str(e))
            if self.cfg.ledger is None:
                raise RuntimeError("record/live mode needs a ledger")
            try:
                reservation = await anyio.to_thread.run_sync(
                    lambda: self.cfg.ledger.reserve(  # type: ignore[union-attr]
                        model=model, worst_case_usd=worst, request_hash=h
                    )
                )
            except BudgetExceededError as e:
                entry["outcome"] = "refused-budget"
                self._log(entry)
                return error_response(402, "business", False, str(e))
            entry["reserved_usd"] = worst

        out_headers = {k: headers[k] for k in FORWARD_HEADERS if k in headers}
        out_headers["accept-encoding"] = "identity"
        try:
            out_headers["x-api-key"] = self._api_key()
        except RuntimeError as e:
            if reservation is not None:
                await self._settle(reservation, {})  # nothing was sent; release at zero cost
            entry["outcome"] = "refused-nokey"
            self._log(entry)
            return error_response(401, "permission", False, str(e))

        client = self._client_get()
        upstream = client.build_request(
            request.method,
            self.cfg.upstream_url.rstrip("/") + full_path,
            headers=out_headers,
            content=raw,
        )
        try:
            resp = await client.send(upstream, stream=True)
        except httpx.HTTPError as e:
            return await self._upstream_failed(e, reservation, entry)

        content_type = resp.headers.get("content-type", "application/json")
        if "text/event-stream" in content_type:
            return StreamingResponse(
                self._stream(resp, reservation, entry, key, h, test_id, seq_no),
                status_code=resp.status_code,
                media_type="text/event-stream",
            )
        try:
            data = await resp.aread()
        except httpx.HTTPError as e:
            return await self._upstream_failed(e, reservation, entry)
        finally:
            await resp.aclose()
        text = data.decode("utf-8", errors="replace")
        usage: dict[str, Any] | None = None
        try:
            parsed = json.loads(text)
            usage = parsed.get("usage") if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            parsed = None
        if reservation is not None:
            # Any response without usage, error statuses included, is charged the worst case.
            entry["cost_usd"] = await self._settle(reservation, usage or None)
        entry["status"] = resp.status_code
        entry["outcome"] = "live"
        if self.cfg.mode == "record" and resp.status_code == 200:
            self._record(
                key, h, resp.status_code, content_type, "json", text, entry, usage, test_id, seq_no
            )
        self._log(entry)
        return Response(content=data, status_code=resp.status_code, media_type=content_type)

    async def _upstream_failed(
        self, e: httpx.HTTPError, reservation: Reservation | None, entry: dict[str, Any]
    ) -> Response:
        if reservation is not None:
            usage: dict[str, Any] | None = {} if isinstance(e, NOT_SENT_ERRORS) else None
            entry["cost_usd"] = await self._settle(reservation, usage)
        entry["outcome"] = "upstream-error"
        entry["error"] = type(e).__name__
        self._log(entry)
        return error_response(
            502, "transient", True, f"upstream request failed: {type(e).__name__}"
        )

    async def _settle(self, res: Reservation, usage: dict[str, Any] | None) -> float:
        ledger = self.cfg.ledger
        if ledger is None:
            raise RuntimeError("record/live mode needs a ledger")
        return await anyio.to_thread.run_sync(lambda: ledger.settle(res, usage))

    async def _stream(
        self,
        resp: httpx.Response,
        reservation: Reservation | None,
        entry: dict[str, Any],
        key: dict[str, Any],
        h: str,
        test_id: str | None,
        seq_no: int | None,
    ) -> AsyncIterator[bytes]:
        chunks: list[bytes] = []
        settled = False
        try:
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                yield chunk
            text = b"".join(chunks).decode("utf-8", errors="replace")
            usage, complete = parse_sse_usage(text)
            if reservation is not None:
                entry["cost_usd"] = await self._settle(reservation, usage)  # None -> worst case
                settled = True
            entry["status"] = resp.status_code
            entry["outcome"] = "live"
            entry["final_usage"] = usage is not None
            if (
                self.cfg.mode == "record"
                and resp.status_code == 200
                and complete
                and usage is not None
            ):
                self._record(
                    key,
                    h,
                    resp.status_code,
                    "text/event-stream",
                    "sse",
                    text,
                    entry,
                    usage,
                    test_id,
                    seq_no,
                )
            self._log(entry)
        finally:
            await resp.aclose()
            if reservation is not None and not settled:
                # The stream was cut (client went away, upstream error): charge the worst case.
                await asyncio.shield(self._settle(reservation, None))
                entry["outcome"] = "stream-cut"
                self._log(entry)

    def _record(
        self,
        key: dict[str, Any],
        h: str,
        status: int,
        content_type: str,
        kind: str,
        text: str,
        entry: dict[str, Any],
        usage: dict[str, Any] | None,
        test_id: str | None,
        seq_no: int | None,
    ) -> None:
        cassette = Cassette(
            hash=h,
            suite=self.cfg.suite,
            recorded_at=now_iso(),
            match=key,
            status=status,
            content_type=content_type,
            kind=kind,
            body=text,
            model=str(entry.get("model")),
            usage=usage,
            cost_usd=float(entry.get("cost_usd") or 0.0),
            test_id=test_id,
            seq=seq_no,
        )
        try:
            self.store.put(cassette)
            entry["outcome"] = "recorded"
        except DirtyCassetteError as e:
            self.state.scan_failures.append(str(e))
            entry["outcome"] = "recorded-refused-dirty"


def create_app(cfg: ProxyConfig) -> tuple[Starlette, Proxy]:
    proxy = Proxy(cfg)
    methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]
    app = Starlette(routes=[Route("/{path:path}", proxy.handle, methods=methods)])
    return app, proxy


class RunningProxy:
    """The proxy served on 127.0.0.1 in a background thread (tests, spike, record)."""

    def __init__(self, cfg: ProxyConfig, port: int = 0) -> None:
        from northwind_llm_replay.scripted import ServerThread

        self.app, self.proxy = create_app(cfg)
        self._server = ServerThread(self.app, port=port)

    def __enter__(self) -> RunningProxy:
        self._server.start()
        return self

    @property
    def url(self) -> str:
        return self._server.url

    def __exit__(self, *exc: object) -> None:
        self._server.stop()
