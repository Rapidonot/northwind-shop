"""Secret / PAN scanner for cassettes, the ledger and the proxy log (FOUND-2; hard rules 1, 4).

Fails on:
- API-key shapes: `sk-ant-…`, other `sk-…` keys, Bearer tokens, an `x-api-key` header with a value;
- any 16+ character piece of the real key (when the caller supplies it, e.g. loaded from .env),
  so a key in a format the regexes don't know is still caught;
- Luhn-valid 13–19 digit runs with a non-digit (or start/end of text) on both sides, plus
  4-4-4-4 groups split by one space or dash. There is no allowlist: sandbox test PANs fail too,
  because card numbers never belong in model traffic. This check is the same for all content. A
  random ID can hold a digit run that passes Luhn, so two things are left out, and nothing else:
  the exact legacy ledger values below, in the ledger file only, and a cassette's `hash` when it is
  provably the SHA-256 of that cassette's own request. New ledger IDs cannot hold such a run;
- a cassette whose `recorded_at` is not ISO-8601;
- an internal-only canary from the answer key (hard rule 7; pulled forward from #5 by RAG-12):
  shopper traffic must never carry internal text, so a recording holding one is refused.

Findings never contain the matched secret, only its kind and position.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from northwind_common.cards import luhn_ok
from northwind_llm_replay.scrub import request_hash

KEY_FRAGMENT_LEN = 16
ANSWER_KEY = Path("data/fixtures/_answer_key.json")

_KEY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic-api-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")),
    ("generic-sk-key", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}")),
    ("bearer-token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/\-]{16,}")),
    # The header *with a value* (a header dump, curl line or JSON object), not the bare name.
    ("x-api-key-header", re.compile(r"""(?i)x-api-key["']?\s*[:=]\s*["']?[^\s"',}]+""")),
)
_PAN_RUN = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
# The only generated values that trip the PAN check, written before ledger IDs became digit-free
# (shared ledger lines 103, 104 and 371). Exact values: this list never grows.
LEGACY_LEDGER_VALUES = {
    "id": "8a6cfff64959463199414e83f6dc4f65",
    "request_hash": "9ae008fbeda6cdf6a5e448691696572946f33256beca740eec2bbb879d27a62b",
}
_PAN_GROUPED = re.compile(r"(?<![\d])\d{4}([ -])\d{4}\1\d{4}\1\d{4}(?![\d])")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}"


def key_fragments(key: str) -> set[str]:
    if len(key) < KEY_FRAGMENT_LEN:
        return {key} if key else set()
    return {key[i : i + KEY_FRAGMENT_LEN] for i in range(len(key) - KEY_FRAGMENT_LEN + 1)}


def load_canaries(repo_root: Path, required: bool = False) -> frozenset[str]:
    """`internal_only[].canary` from the answer key. A missing key file is empty, unless `required`:
    the repo's own scans must not report clean with the canary check silently off."""
    key = repo_root / ANSWER_KEY
    if not key.exists():
        if required:
            raise FileNotFoundError(f"{key} is missing, so the canary check cannot run")
        return frozenset()
    entries = json.loads(key.read_text(encoding="utf-8")).get("internal_only", [])
    return frozenset(e["canary"] for e in entries)


def scan_text(
    text: str,
    path: str = "<text>",
    secret: str | None = None,
    canaries: frozenset[str] = frozenset(),
) -> list[Finding]:
    findings: list[Finding] = []
    fragments = key_fragments(secret) if secret else set()
    for lineno, line in enumerate(text.splitlines() or [text], start=1):
        for kind, pattern in _KEY_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(path, lineno, kind))
        if fragments and any(f in line for f in fragments):
            findings.append(Finding(path, lineno, "api-key-fragment"))
        if any(c in line for c in canaries):
            findings.append(Finding(path, lineno, "internal-canary"))
        for m in _PAN_RUN.finditer(line):
            if luhn_ok(m.group(0)):
                findings.append(Finding(path, lineno, "luhn-valid-pan"))
        for m in _PAN_GROUPED.finditer(line):
            if luhn_ok(re.sub(r"\D", "", m.group(0))):
                findings.append(Finding(path, lineno, "luhn-valid-pan"))
    return findings


_DELTA_TEXT_KEYS = {
    "text_delta": "text",
    "thinking_delta": "thinking",
    "input_json_delta": "partial_json",
}


def _record(document: str) -> dict[str, Any] | None:
    try:
        record = json.loads(document)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def reassembled_blocks(document: str) -> list[str]:
    """The streamed text of each content block, in index order. `document` is a raw SSE body or a
    cassette holding one. A plain function, so anything holding a stream can reuse it."""
    record = _record(document)
    body = record["body"] if record and isinstance(record.get("body"), str) else document
    blocks: dict[int, list[str]] = {}
    for line in body.splitlines():
        event = _record(line[len("data:") :]) if line.startswith("data:") else None
        if event is None or event.get("type") != "content_block_delta":
            continue
        delta = event.get("delta")
        if not isinstance(delta, dict):
            continue
        key = _DELTA_TEXT_KEYS.get(delta.get("type", ""))
        if key is not None:
            blocks.setdefault(event.get("index", 0), []).append(str(delta.get(key, "")))
    return ["".join(parts) for _, parts in sorted(blocks.items())]


def scan_stream(
    document: str,
    path: str,
    secret: str | None = None,
    canaries: frozenset[str] = frozenset(),
) -> list[Finding]:
    """`scan_text` over each reassembled block, because a secret split across deltas is never on
    one raw line. Pairs with `scan_text` on the raw document (FOUND-2 review 6)."""
    return [
        replace(finding, kind=f"reassembled-{finding.kind}")
        for block in reassembled_blocks(document)
        for finding in scan_text(block, path, secret, canaries)
    ]


def without_ledger_ids(line: str) -> str:
    """One ledger record with any legacy value blanked and every other byte kept."""
    record = _record(line) or {}
    for key, value in LEGACY_LEDGER_VALUES.items():
        if record.get(key) == value:
            line = line.replace(f'"{key}": "{value}"', f'"{key}": ""', 1)
    return line


def without_request_hash(cassette: str) -> str:
    """A cassette with its `hash` blanked, only if that hash is the SHA-256 of its own `match`: a
    digest cannot be chosen, so it cannot carry a card number."""
    record = _record(cassette) or {}
    value = record.get("hash")
    if isinstance(value, str) and "match" in record and value == request_hash(record["match"]):
        return cassette.replace(f'"hash": "{value}"', '"hash": ""', 1)
    return cassette


def _check_recorded_at(path: Path, text: str) -> list[Finding]:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return [Finding(str(path), 1, "cassette-not-json")]
    value = doc.get("recorded_at") if isinstance(doc, dict) else None
    if not isinstance(value, str):
        return [Finding(str(path), 1, "recorded_at-missing")]
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return [Finding(str(path), 1, "recorded_at-not-iso8601")]
    return []


def _files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file():
            yield root
        elif root.is_dir():
            yield from sorted(p for p in root.rglob("*") if p.is_file())


def is_cassette(path: Path) -> bool:
    return path.suffix == ".json" and "cassettes" in path.parts


def scan_paths(
    roots: Iterable[Path],
    secret: str | None = None,
    canaries: frozenset[str] = frozenset(),
    ledger: Path | None = None,
) -> list[Finding]:
    """`ledger` names the one file whose legacy values are left out; there is no default."""
    findings: list[Finding] = []
    for path in _files(roots):
        if path.suffix == ".lock":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if ledger is not None and path.resolve() == ledger.resolve():
            scanned = "\n".join(without_ledger_ids(line) for line in text.splitlines())
        elif is_cassette(path):
            scanned = without_request_hash(text)
        else:
            scanned = text
        findings.extend(scan_text(scanned, str(path), secret, canaries))
        if is_cassette(path):
            findings.extend(_check_recorded_at(path, text))
            findings.extend(scan_stream(text, str(path), secret, canaries))
    return findings
