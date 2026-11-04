"""Spike-only egress logger (FOUND-2 spike).

An HTTP(S) proxy that never connects anywhere. It is set as HTTPS_PROXY/HTTP_PROXY for the CLI,
so any traffic that does not go to ANTHROPIC_BASE_URL (telemetry, update checks, error
reporting, OAuth) shows up here with its target host and is refused.
"""

from __future__ import annotations

import socket
import threading
from datetime import UTC, datetime


class EgressLogger:
    def __init__(self) -> None:
        self.hits: list[dict[str, str]] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._sock.getsockname()
        return f"http://{host}:{port}"

    def __enter__(self) -> EgressLogger:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._sock.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                try:
                    first = conn.recv(4096).split(b"\r\n", 1)[0].decode("latin-1")
                except OSError:
                    first = "<no request line>"
                parts = first.split(" ")
                self.hits.append(
                    {
                        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                        "method": parts[0] if parts else "",
                        "target": parts[1] if len(parts) > 1 else "",
                    }
                )
                try:
                    conn.sendall(
                        b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                except OSError:
                    pass
