"""Cassette store: one plain-JSON file per recorded call under cassettes/<suite>/ (FOUND-2).

A cassette holds the *scrubbed* match key (method, path, matched headers, body), never the raw
request, so local paths or IDs that scrubbing removed are not committed. The response is stored
as received: raw SSE text for streams, the JSON text otherwise, plus content-type. Every write
goes through the secret/PAN scanner first and is refused if it finds anything.

Cassettes are recorded, never hand-edited: a miss means the model input changed, so re-record.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from northwind_llm_replay.scan import (
    load_canaries,
    scan_stream,
    scan_text,
    without_request_hash,
)
from northwind_llm_replay.scrub import canonical

FORMAT = 1


class DirtyCassetteError(Exception):
    """The scanner found a secret or PAN pattern; the cassette was not written."""


@dataclass
class Cassette:
    hash: str
    suite: str
    recorded_at: str
    match: dict[str, Any]
    status: int
    content_type: str
    kind: str  # "sse" | "json"
    body: str
    model: str
    usage: dict[str, Any] | None
    cost_usd: float
    test_id: str | None = None
    seq: int | None = None
    format: int = FORMAT
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Cassette:
        return cls(**json.loads(text))


def pretty(obj: Any) -> list[str]:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False).splitlines()


class CassetteStore:
    def __init__(
        self,
        root: Path,
        suite: str,
        secret: str | None = None,
        canaries: frozenset[str] | None = None,
    ) -> None:
        self.root = root
        self.suite = suite
        self.dir = root / suite
        self._secret = secret
        self._canaries = load_canaries(root.parent) if canaries is None else canaries

    def path_for(self, request_hash: str) -> Path:
        return self.dir / f"{request_hash}.json"

    def get(self, request_hash: str) -> Cassette | None:
        p = self.path_for(request_hash)
        return Cassette.from_json(p.read_text(encoding="utf-8")) if p.exists() else None

    def all(self) -> list[Cassette]:
        if not self.dir.exists():
            return []
        return [
            Cassette.from_json(p.read_text(encoding="utf-8"))
            for p in sorted(self.dir.glob("*.json"))
        ]

    def by_sequence(self, test_id: str, seq: int) -> Cassette | None:
        for c in self.all():
            if c.test_id == test_id and c.seq == seq:
                return c
        return None

    def put(self, cassette: Cassette) -> Path:
        text = cassette.to_json()
        where = str(self.path_for(cassette.hash))
        findings = scan_text(without_request_hash(text), where, self._secret, self._canaries)
        findings += scan_stream(text, where, self._secret, self._canaries)
        if findings:
            kinds = sorted({f.kind for f in findings})
            raise DirtyCassetteError(f"cassette {cassette.hash[:12]} not written: {kinds}")
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path_for(cassette.hash)
        p.write_text(text, encoding="utf-8")
        return p

    def miss_diff(self, match: dict[str, Any], max_lines: int = 200) -> str:
        """Unified diff of `match` against the most similar cassette with the same method + path."""
        candidates = [
            c
            for c in self.all()
            if c.match.get("method") == match.get("method")
            and c.match.get("path") == match.get("path")
        ]
        if not candidates:
            return f"(no cassette in {self.dir} for {match.get('method')} {match.get('path')})"
        want = canonical(match)
        nearest = max(
            candidates,
            key=lambda c: difflib.SequenceMatcher(
                None, canonical(c.match), want, autojunk=False
            ).ratio(),
        )
        lines = list(
            difflib.unified_diff(
                pretty(nearest.match),
                pretty(match),
                fromfile=f"cassette {nearest.hash[:12]}",
                tofile="request",
                lineterm="",
            )
        )
        if len(lines) > max_lines:
            lines = [*lines[:max_lines], f"... ({len(lines) - max_lines} more diff lines)"]
        return "\n".join(lines)
