"""The record/replay proxy that sits behind ANTHROPIC_BASE_URL for every LLM call (FOUND-2).

Modes (LLM_MODE):
- replay (default, forced in CI): serve cassettes only; a miss is a structured error with a
  diff against the nearest cassette. Never loads the API key, never goes upstream.
- record: serve existing cassettes; otherwise admit through the budget guard, call upstream,
  write the cassette (after the scanner), settle the ledger.
- live: admit, call upstream, settle; no cassette.

Auth: callers must present exactly one credential and it must be the expected one, either as
`x-api-key` (the Agent SDK's CLI) or as `Authorization: Bearer <credential>` (`anthropic` 2.x
sends API keys that way). Anything else, such as an OAuth token, a real key or two credentials,
is refused. This is a hard pass/fail that keeps runtime calls off the developer's subscription
(decision 0001). The expected credential is the placeholder, except on the runner, where it is
the token of the run that claimed the window.

Two roles for spending (decision 0005). The runner (`northwind_llm_replay.runner`) is this proxy
in live mode with a `gate`: it alone holds the key, the ledger and the caps, and it runs from a
clean checkout the sandbox cannot write. Everything else is `via_runner`: its upstream is the
runner, it presents the claimed token, and it has no ledger and no key.

Test context: a base URL of `http://host:port/_t/<test-id>` (`encode_test_id`) tags requests
with a test ID
(used by sequence matching and stored on cassettes); the prefix is stripped before matching.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from hmac import compare_digest
from pathlib import Path
from typing import Any, Protocol
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
from northwind_llm_replay.ledger import (
    HASH_PREFIX_CHARS,
    BudgetExceededError,
    Ledger,
    Reservation,
    now_iso,
)
from northwind_llm_replay.pricing import (
    UnpricedModelError,
    cost_usd,
    price_for,
    worst_case_usd,
)
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
LOG_HASH_CHARS = HASH_PREFIX_CHARS  # enough to find a cassette by prefix
# HARDEN-1: betas the CLI adds after a classifier refusal. The runner refuses them (they are not on
# its ALLOWED_BETAS) and counts each toward its refusal cap, and replay can never match them. The
# sandbox proxy refuses them itself, in every mode, so they never leave and never cost a refusal.
FALLBACK_BETA_PREFIXES = ("fallback-credit-", "server-side-fallback-")
# Logged in place of a request body the proxy could not parse: the raw bytes are never logged.
UNPARSED_BODY = "<unlogged:unparsed-body>"
NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def load_api_key(env_file: Path) -> str:
    """Read ANTHROPIC_API_KEY from .env without touching os.environ."""
    try:
        value = dotenv_values(env_file).get("ANTHROPIC_API_KEY") if env_file.exists() else None
    except OSError as exc:  # Claude's sandbox denies the key file (CC-27)
        raise RuntimeError(f"cannot read {env_file}") from exc
    if not value:
        raise RuntimeError(f"ANTHROPIC_API_KEY is not set in {env_file}")
    return value


def placeholder_credential() -> str | None:
    return PLACEHOLDER_API_KEY


class Gate(Protocol):
    """What the runner adds to a live proxy. A proxy with a gate buffers every response, so
    nothing reaches the caller before `leaks` has seen all of it."""

    def refuse(
        self,
        method: str,
        raw_path: str,
        query: str,
        raw: bytes,
        body: Any,
        headers: dict[str, str],
    ) -> tuple[str, Response] | None:
        """(outcome, response) to refuse a request, or None to let it through."""

    def leaks(self, text: str) -> bool: ...

    def request_leaks(self, raw: bytes, headers: dict[str, str]) -> bool: ...

    def blocked(self) -> Response: ...

    def report(self, entry: dict[str, Any]) -> None: ...


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
    credential: Callable[[], str | None] = placeholder_credential  # None refuses everyone
    via_runner: bool = False  # upstream is the runner, which owns the guard and the ledger
    gate: Gate | None = None  # set on the runner only

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
    fallback_attempts: list[dict[str, Any]] = field(default_factory=list)
    # HARDEN-1: responses served with stop_reason "refusal", for tests and evals to report.
    refusals: list[dict[str, Any]] = field(default_factory=list)


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


def same_secret(presented: str, expected: str) -> bool:
    """Constant-time, and safe for the non-ASCII text a header can decode to."""
    return compare_digest(presented.encode("utf-8", "replace"), expected.encode("utf-8", "replace"))


def auth_verdict(headers: dict[str, str], expected: str | None = PLACEHOLDER_API_KEY) -> str:
    """ "placeholder" if the only credential is the expected one, else "REJECTED"."""
    creds = [v for k, v in headers.items() if k in ("x-api-key", "authorization")]
    if expected is None or len(creds) != 1:
        return "REJECTED"
    ok = same_secret(headers.get("x-api-key", ""), expected) or same_secret(
        headers.get("authorization", ""), f"Bearer {expected}"
    )
    return "placeholder" if ok else "REJECTED"


def stop_reason(text: str, is_sse: bool) -> str | None:
    """The response's stop_reason, from the JSON body or the stream's message_delta (HARDEN-1)."""
    if not is_sse:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        reason = parsed.get("stop_reason") if isinstance(parsed, dict) else None
        return reason if isinstance(reason, str) else None
    found: str | None = None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("type") == "message_delta":
            delta = data.get("delta")  # never trust the shape: counting must not raise (L1)
            reason = delta.get("stop_reason") if isinstance(delta, dict) else None
            if isinstance(reason, str):
                found = reason
    return found


def fallback_betas(headers: dict[str, str]) -> list[str]:
    """The fallback betas in a request's `anthropic-beta`, in order (HARDEN-1)."""
    betas = (b.strip() for b in headers.get("anthropic-beta", "").split(","))
    return [b for b in betas if b.lower().startswith(FALLBACK_BETA_PREFIXES)]


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
            if self.store._secret is None and not self.cfg.via_runner:  # a token is not the key
                self.store._secret = self._key
        return self._key

    def _log(self, entry: dict[str, Any]) -> None:
        self.state.calls.append({k: v for k, v in entry.items() if k != "body"})
        if self.cfg.gate is not None:
            self.cfg.gate.report(entry)
        if self.cfg.log_path is None:
            return
        self.cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_lock, self.cfg.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")

    def _note_refusal(self, entry: dict[str, Any], text: str, is_sse: bool) -> None:
        """Count a refusal the proxy served. Observation only: nothing it returns changes."""
        if stop_reason(text, is_sse) == "refusal":
            entry["refusal"] = True
            self.state.refusals.append(
                {"hash": entry.get("hash"), "test_id": entry.get("test_id"), "mode": self.cfg.mode}
            )

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
        # One scrub for the match key and the log, so the log can never hold what a cassette drops
        # (HARDEN-1, M2). If it raises, the request fails here, before anything is logged.
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
            "auth": auth_verdict(headers, self.cfg.credential()),
            "hash": h[:LOG_HASH_CHARS],
            "model": body.get("model") if isinstance(body, dict) else None,
            "max_tokens": body.get("max_tokens") if isinstance(body, dict) else None,
            "stream": bool(body.get("stream")) if isinstance(body, dict) else False,
            # Scrubbed like the cassette; a body that did not parse is never logged raw.
            "body": key["body"] if body is not None else (UNPARSED_BODY if raw else None),
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

        # HARDEN-1: never forward a fallback beta, and say so as its own failure, not as a miss to
        # re-record. Sandbox side only: the runner keeps its own `refused-beta` (decision 0005).
        fallback = fallback_betas(headers) if self.cfg.gate is None else []
        if fallback:
            entry["outcome"] = "refused-fallback"
            self.state.fallback_attempts.append(
                {"hash": h, "path": full_path, "test_id": test_id, "betas": fallback}
            )
            self._log(entry)
            return error_response(
                400,
                "validation",
                False,
                "the LLM proxy never forwards a fallback beta (HARDEN-1): the CLI must not retry "
                "a refusal with one (northwind_common.llm.child_env)",
            )

        if self.cfg.gate is not None:
            refusal = self.cfg.gate.refuse(
                request.method, request.url.path, request.url.query, raw, body, headers
            )
            if refusal is not None:
                entry["outcome"], refused = refusal
                self._log(entry)
                return refused

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
                self._note_refusal(entry, hit.body, hit.kind == "sse")
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
        if path == PRICED_PATH and not self.cfg.via_runner:
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

        # Last of all, once every free refusal has passed: a miss here costs a real upstream call,
        # so this cannot be used as a free test of guesses at the key (review 2026-11-04, 1).
        try:
            leaked = self.cfg.gate is not None and self.cfg.gate.request_leaks(raw, headers)
        except (ValueError, RecursionError):
            if reservation is not None:
                await self._settle(reservation, {})  # nothing was sent; release at zero cost
            raise
        if leaked and self.cfg.gate is not None:
            if reservation is not None:
                await self._settle(reservation, {})  # nothing was sent; release at zero cost
            entry["outcome"] = "blocked-key"
            self._log(entry)
            return self.cfg.gate.blocked()

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
            sent_at = time.monotonic()
            resp = await client.send(upstream, stream=True)
        except httpx.HTTPError as e:
            return await self._upstream_failed(e, reservation, entry)

        content_type = resp.headers.get("content-type", "application/json")
        is_sse = "text/event-stream" in content_type
        if is_sse and self.cfg.gate is None:
            return StreamingResponse(
                self._stream(resp, reservation, entry, key, h, test_id, seq_no),
                status_code=resp.status_code,
                media_type="text/event-stream",
            )
        try:
            pieces: list[bytes] = []
            async for piece in resp.aiter_bytes():
                if not pieces:  # what a caller of a buffering runner cannot measure itself
                    entry["first_byte_ms"] = round((time.monotonic() - sent_at) * 1000)
                pieces.append(piece)
            data = b"".join(pieces)
        except httpx.HTTPError as e:
            return await self._upstream_failed(e, reservation, entry)
        finally:
            await resp.aclose()
        text = data.decode("utf-8", errors="replace")
        usage: dict[str, Any] | None = None
        if is_sse:
            usage, _ = parse_sse_usage(text)
        else:
            try:
                parsed = json.loads(text)
                usage = parsed.get("usage") if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                parsed = None
        if reservation is not None:
            # Any response without usage, error statuses included, is charged the worst case.
            entry["cost_usd"] = await self._settle(
                reservation, usage or None, entry.get("first_byte_ms")
            )
        entry["status"] = resp.status_code
        entry["outcome"] = "live"
        entry["usage"] = usage
        self._note_refusal(entry, text, is_sse)
        if self.cfg.gate is not None and self.cfg.gate.leaks(text):
            entry["outcome"] = "blocked-key"
            self._log(entry)
            return self.cfg.gate.blocked()
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
            entry["cost_usd"] = await self._settle(reservation, usage, entry.get("first_byte_ms"))
        entry["outcome"] = "upstream-error"
        entry["error"] = type(e).__name__
        self._log(entry)
        return error_response(
            502, "transient", True, f"upstream request failed: {type(e).__name__}"
        )

    async def _settle(
        self, res: Reservation, usage: dict[str, Any] | None, first_byte_ms: int | None = None
    ) -> float:
        ledger = self.cfg.ledger
        if ledger is None:
            raise RuntimeError("record/live mode needs a ledger")
        return await anyio.to_thread.run_sync(lambda: ledger.settle(res, usage, first_byte_ms))

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
            self._note_refusal(entry, text, True)
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
        # Through the runner there is no local settlement, and usage prices to the same figure.
        cost = (
            cost_usd(str(entry.get("model")), usage)
            if self.cfg.via_runner and usage
            else float(entry.get("cost_usd") or 0.0)
        )
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
            cost_usd=cost,
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
