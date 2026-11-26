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

The walk never follows a symlink: a symlink is a finding and is not read. The key-fragment scan runs
outside the sandbox over a worktree the sandbox can write, so a link there must not reach other
files (decision 0005). A process racing the walk could still swap a parent directory; that discloses
only a finding's kind and line.
"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from northwind_common.cards import luhn_ok
from northwind_llm_replay.scrub import request_hash

KEY_FRAGMENT_LEN = 16
SYMLINK_REFUSED = "symlink-refused"
ANSWER_KEY = Path("data/fixtures/_answer_key.json")

_KEY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic-api-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")),
    ("generic-sk-key", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}")),
    ("bearer-token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/\-]{16,}")),
    # The header *with a value* (a header dump, curl line or JSON object), not the bare name.
    # Row 18a (H6): or JSON-escaped inside a cassette string (`x-api-key\": \"…`), an empty value
    # left out as before. The first alternative is the old pattern, unchanged.
    (
        "x-api-key-header",
        re.compile(
            r"""(?i)x-api-key["']?\s*[:=]\s*["']?[^\s"',}]+"""
            r"""|(?i:x-api-key)\\{1,2}["']\s*[:=]\s*\\{1,2}["'](?!\\)[^\s"',}\\]+"""
        ),
    ),
)
_PAN_RUN = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
# The only generated values that trip the PAN check, written before ledger IDs became digit-free
# (shared ledger lines 103, 104 and 371). Exact values: this list never grows.
LEGACY_LEDGER_VALUES = {
    "id": "8a6cfff64959463199414e83f6dc4f65",
    "request_hash": "9ae008fbeda6cdf6a5e448691696572946f33256beca740eec2bbb879d27a62b",
}
_PAN_GROUPED = re.compile(r"(?<![\d])\d{4}([ -])\d{4}\1\d{4}\1\d{4}(?![\d])")
# Row 18a (H6): 4-4-4-4, Amex 4-6-5 and Diners 4-6-4, each gap one space, hyphen, dot, no-break or
# thin space, mixed or not. Read beside `_PAN_GROUPED`, which is unchanged; a span both find counts
# once.
_PAN_SEP = "[ \\-.\u00a0\u2007\u2009\u202f]"
_PAN_GROUPED_WIDE = re.compile(
    rf"(?<!\d)(?:\d{{4}}{_PAN_SEP}\d{{4}}{_PAN_SEP}\d{{4}}{_PAN_SEP}\d{{4}}"
    rf"|\d{{4}}{_PAN_SEP}\d{{6}}{_PAN_SEP}\d{{4,5}})(?!\d)"
)


def printable(text: str) -> str:
    """A file name for a terminal. The sandbox picks these names, and escape sequences in one could
    hide a failing result on the user's screen."""
    return "".join(ch if ch.isprintable() else f"\\x{ord(ch):02x}" for ch in text)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str

    def __str__(self) -> str:
        return f"{printable(self.path)}:{self.line}: {self.kind}"


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
            # No line number: a session can write one guess per line, and read this back.
            findings.append(Finding(path, 0, "api-key-fragment"))
        if any(c in line for c in canaries):
            findings.append(Finding(path, lineno, "internal-canary"))
        for m in _PAN_RUN.finditer(line):
            if luhn_ok(m.group(0)):
                findings.append(Finding(path, lineno, "luhn-valid-pan"))
        grouped = set()
        for m in _PAN_GROUPED.finditer(line):
            grouped.add(m.span())
            if luhn_ok(re.sub(r"\D", "", m.group(0))):
                findings.append(Finding(path, lineno, "luhn-valid-pan"))
        for m in _PAN_GROUPED_WIDE.finditer(line):
            if m.span() not in grouped and luhn_ok(re.sub(r"\D", "", m.group(0))):
                findings.append(Finding(path, lineno, "luhn-valid-pan"))
    return findings


_DELTA_TEXT_KEYS = {
    "text_delta": "text",
    "thinking_delta": "thinking",
    "input_json_delta": "partial_json",
    "signature_delta": "signature",  # row 18a: every delta type is read, or it is a finding
}
UNKNOWN_DELTA = "unknown-delta-type"
# Not splitlines(): that also breaks on U+2028 and friends, legal inside a JSON string, and the
# broken halves would parse as nothing, leaving a hole in the text. CR-only streams are split too
# (row 18a, H6).
_SSE_LINE = re.compile(r"\r\n|\r|\n")


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
    return _reassembled(body)[0]


def _reassembled(body: str) -> tuple[list[str], bool]:
    """The blocks, and whether a delta was of a type not in `_DELTA_TEXT_KEYS` (or no dict)."""
    blocks: dict[int, list[str]] = {}
    unknown = False
    for line in _SSE_LINE.split(body):
        event = _record(line[len("data:") :]) if line.startswith("data:") else None
        if event is None or event.get("type") != "content_block_delta":
            continue
        delta = event.get("delta")
        key = _DELTA_TEXT_KEYS.get(delta.get("type", "")) if isinstance(delta, dict) else None
        if key is None or not isinstance(delta, dict):
            unknown = True
            continue
        index = event.get("index", 0)
        blocks.setdefault(index if isinstance(index, int) else -1, []).append(
            str(delta.get(key, ""))
        )
    return ["".join(parts) for _, parts in sorted(blocks.items())], unknown


def json_blocks(document: str) -> list[str]:
    """The content blocks of a `kind: "json"` cassette's body (row 18a, H6): text, thinking and a
    tool call's input, in order. Empty for anything else."""
    record = _record(document)
    if not record or record.get("kind") != "json" or not isinstance(record.get("body"), str):
        return []
    body = _record(record["body"])
    content = body.get("content") if body else None
    out: list[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        for key in ("text", "thinking"):
            if isinstance(block.get(key), str):
                out.append(block[key])
        if "input" in block:
            out.append(json.dumps(block["input"], ensure_ascii=False))
    return out


def response_blocks(document: str) -> list[str]:
    """What the model wrote in one cassette, block by block, whether it streamed or not."""
    return reassembled_blocks(document) + json_blocks(document)


def _strings(value: Any, depth: int = 0) -> Iterator[str]:
    """Every string in a JSON value, decoded; a string that is itself a JSON document is opened
    too, three levels deep at most (a cassette's `body`)."""
    if isinstance(value, str):
        yield value
        if depth < 3 and value[:1] in "{[":
            inner = _record(value) if value[:1] == "{" else None
            if inner is None and value[:1] == "[":
                try:
                    inner = {"": json.loads(value)}
                except json.JSONDecodeError:
                    inner = None
            if inner is not None:
                yield from _strings(inner, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item, depth)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, depth)


def scan_stream(
    document: str,
    path: str,
    secret: str | None = None,
    canaries: frozenset[str] = frozenset(),
) -> list[Finding]:
    """`scan_text` over each reassembled block, because a secret split across deltas is never on
    one raw line. Pairs with `scan_text` on the raw document (FOUND-2 review 6).

    A key fragment keeps its kind: `cmd_scan` hides where those are, so a guess per file cannot be
    located, and says to rotate the key (decision 0005)."""
    return [
        finding
        if finding.kind == "api-key-fragment"
        else replace(finding, kind=f"reassembled-{finding.kind}")
        for block in reassembled_blocks(document)
        for finding in scan_text(block, path, secret, canaries)
    ]


def _new_kinds(found: list[Finding], known: set[str], prefix: str) -> list[Finding]:
    """`found` without the kinds the plain scans reported already, under `prefix`: a widening adds
    findings, it never repeats one."""
    return [
        f if f.kind == "api-key-fragment" else replace(f, kind=f"{prefix}{f.kind}")
        for f in found
        if f.kind not in known
    ]


def scan_cassette(
    text: str,
    path: str,
    secret: str | None = None,
    canaries: frozenset[str] = frozenset(),
    carry: str = "",
) -> list[Finding]:
    """Everything a cassette is refused for, read the same way by `CassetteStore.put` and
    `scan_paths`: the raw text (`hash` left out when it is the request's own), each reassembled
    block, and since row 18a (H6, review 6): an unknown delta type; every JSON string decoded, so
    an escape cannot hide a secret; a `json` body's blocks; the blocks joined, for a secret split
    between two blocks; and with `carry`, the end of what the same test's earlier calls wrote
    (`carried`), for one split between two requests or more."""
    findings = scan_text(without_request_hash(text), path, secret, canaries)
    findings += scan_stream(text, path, secret, canaries)
    record = _record(text)
    if record is None:
        return findings
    known = {f.kind.removeprefix("reassembled-") for f in findings}
    body = record.get("body")
    if isinstance(body, str) and _reassembled(body)[1]:
        findings.append(Finding(path, 0, UNKNOWN_DELTA))
    blocked = without_request_hash(text) != text
    decoded: list[Finding] = []
    for string in _strings({k: v for k, v in record.items() if not (blocked and k == "hash")}):
        decoded += scan_text(string, path, secret, canaries)
    findings += _new_kinds(decoded, known, "decoded-")
    known |= {f.kind.removeprefix("decoded-") for f in findings}
    blocks = response_blocks(text)
    single = [f for block in json_blocks(text) for f in scan_text(block, path, secret, canaries)]
    findings += _new_kinds(single, known, "reassembled-")
    known |= {f.kind.removeprefix("reassembled-") for f in findings}
    joined = scan_text("".join(blocks), path, secret, canaries) if len(blocks) > 1 else []
    findings += _new_kinds(joined, known, "rebuilt-")
    known |= {f.kind.removeprefix("rebuilt-") for f in findings}
    if carry:
        # What the carry holds alone was reported for the earlier calls: only what the join adds.
        ahead = {f.kind for f in scan_text(carry, path, secret, canaries)}
        across = scan_text(carry + "".join(blocks)[:CARRY], path, secret, canaries)
        findings += _new_kinds(across, known | ahead, "rebuilt-across-requests-")
    return findings


# A secret split between two requests lies within this many characters of the cut on each side:
# every key shape, card number, canary and key fragment is shorter.
CARRY = 256


def carried(carry: str, text: str) -> str:
    """The carry after one more cassette of the same test: the end of everything it wrote."""
    return (carry + "".join(response_blocks(text)))[-CARRY:]


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


def _walk(path: Path) -> Iterator[Path | Finding]:
    """Regular files under `path`, and a finding for each symlink. Nothing is followed."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        yield Finding(str(path), 0, SYMLINK_REFUSED)
    elif stat.S_ISREG(mode):
        yield path
    elif stat.S_ISDIR(mode):
        for child in sorted(path.iterdir()):
            yield from _walk(child)


def read_no_follow(path: Path) -> str | None:
    """The file's text, or None if it stopped being a regular file after the walk saw it."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None
        raise
    with os.fdopen(fd, encoding="utf-8", errors="replace") as handle:
        return handle.read() if stat.S_ISREG(os.fstat(fd).st_mode) else None


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
    cassettes: list[tuple[Path, str]] = []
    for path in (item for root in roots for item in _walk(root)):
        if isinstance(path, Finding):
            findings.append(path)
            continue
        if path.suffix == ".lock":
            continue
        text = read_no_follow(path)
        if text is None:
            findings.append(Finding(str(path), 0, SYMLINK_REFUSED))
            continue
        if is_cassette(path):
            findings.extend(_check_recorded_at(path, text))
            cassettes.append((path, text))
            continue
        if ledger is not None and path.resolve() == ledger.resolve():
            scanned = "\n".join(without_ledger_ids(line) for line in text.splitlines())
        else:
            scanned = text
        findings.extend(scan_text(scanned, str(path), secret, canaries))
    findings.extend(_scan_cassettes(cassettes, secret, canaries))
    return findings


def _scan_cassettes(
    cassettes: list[tuple[Path, str]], secret: str | None, canaries: frozenset[str]
) -> list[Finding]:
    """Each cassette with the carry of its test's earlier cassettes, from the same folder, in `seq`
    order (row 18a, review 6: a secret split between two requests)."""
    turns: dict[tuple[Path, str], list[tuple[int, str, str]]] = {}
    for path, text in cassettes:
        record = _record(text) or {}
        test, seq = record.get("test_id"), record.get("seq")
        if isinstance(test, str) and isinstance(seq, int):
            turns.setdefault((path.parent, test), []).append((seq, str(path), text))
    carries: dict[str, str] = {}
    for items in turns.values():
        carry = ""
        for _seq, where, text in sorted(items):
            carries[where] = carry
            carry = carried(carry, text)
    findings: list[Finding] = []
    for path, text in cassettes:
        where = str(path)
        findings.extend(scan_cassette(text, where, secret, canaries, carries.get(where, "")))
    return findings
