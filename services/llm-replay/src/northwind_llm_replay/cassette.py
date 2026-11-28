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
    carried,
    load_canaries,
    scan_cassette,
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


# Row #18 (the user's stop-8 yes, 2026-11-28): a miss's diff is bounded, because it used to cost
# 18-24 s of CPU (`SequenceMatcher(autojunk=False).ratio()` against every cassette of a suite) and
# held every other request the proxy was serving. The nearest cassette is chosen by lengths, then
# by a multiset count, on a short list; only that one is diffed, by lines, and only below this size
# (the two canonical forms together; the largest real pair is about 52,000 characters).
MAX_DIFF_CHARS = 128_000
# The line diff's cost follows its lines, and `pretty()` puts one scalar per line, so the lines are
# capped too (security L1); the largest recorded match is 708 lines.
MAX_DIFF_LINES = 20_000
NEAREST_SHORTLIST = 8


def _nearest(candidates: list[Cassette], want: str) -> Cassette:
    """The most similar candidate, at a cost linear in the text: `real_quick_ratio` (lengths only)
    over all of them, then `quick_ratio` (a character multiset count) over the closest few. Never
    `ratio()`, whose full match is what took 18-24 s."""
    matcher = difflib.SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(want)

    def by_length(c: Cassette) -> float:
        matcher.set_seq1(canonical(c.match))
        return matcher.real_quick_ratio()

    shortlist = sorted(candidates, key=by_length, reverse=True)[:NEAREST_SHORTLIST]

    def by_content(c: Cassette) -> tuple[float, float]:
        matcher.set_seq1(canonical(c.match))
        return matcher.quick_ratio(), matcher.real_quick_ratio()

    return max(shortlist, key=by_content)


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
        self._carry: dict[str, str] = {}  # row 18a: per test, the end of what it wrote

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
        # Row 18a (review 6): what this test's earlier calls wrote in this store's life, for a
        # secret split between two requests. Calls recorded before it are read by `scan_paths`.
        key = cassette.test_id or ""
        carry = self._carry.get(key, "")
        findings = scan_cassette(text, where, self._secret, self._canaries, carry)
        if findings:
            kinds = sorted({f.kind for f in findings})
            raise DirtyCassetteError(f"cassette {cassette.hash[:12]} not written: {kinds}")
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path_for(cassette.hash)
        p.write_text(text, encoding="utf-8")
        self._carry[key] = carried(carry, text)
        return p

    def miss_diff(
        self, match: dict[str, Any], test_id: str | None = None, max_lines: int = 200
    ) -> str:
        """Unified diff of `match` against the most similar cassette with the same method + path:
        of `test_id`'s cassettes if it has any, so a miss is reported in seconds (row #16), else of
        the whole suite."""
        candidates = [
            c
            for c in self.all()
            if c.match.get("method") == match.get("method")
            and c.match.get("path") == match.get("path")
        ]
        same_test = [c for c in candidates if test_id is not None and c.test_id == test_id]
        candidates = same_test or candidates
        if not candidates:
            return f"(no cassette in {self.dir} for {match.get('method')} {match.get('path')})"
        want = canonical(match)
        nearest = _nearest(candidates, want)
        seen = len(canonical(nearest.match)) + len(want)
        if seen > MAX_DIFF_CHARS:
            return "\n".join(
                [
                    f"--- cassette {nearest.hash[:12]}",
                    "+++ request",
                    f"(diff skipped (too large): {seen} characters, cap {MAX_DIFF_CHARS})",
                ]
            )
        before, after = pretty(nearest.match), pretty(match)
        if len(before) + len(after) > MAX_DIFF_LINES:
            return "\n".join(
                [
                    f"--- cassette {nearest.hash[:12]}",
                    "+++ request",
                    f"(diff skipped (too large): {len(before) + len(after)} lines, "
                    f"cap {MAX_DIFF_LINES})",
                ]
            )
        lines = list(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"cassette {nearest.hash[:12]}",
                tofile="request",
                lineterm="",
            )
        )
        if len(lines) > max_lines:
            lines = [*lines[:max_lines], f"... ({len(lines) - max_lines} more diff lines)"]
        return "\n".join(lines)
