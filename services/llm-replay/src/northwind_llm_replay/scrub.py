"""Canonicalise, scrub and hash a request, so identical calls match their cassette (FOUND-2).

Scrubbing is deliberately narrow. Only the JSON paths in SCRUB_RULES are touched, and that list
holds exactly the paths the spike diff showed changing between identical runs
(services/llm-replay/SPIKE.md). There are no free-text sweeps over message content: a date or a
path the *model* is meant to see still changes the hash: then the model input really changed.

Path syntax: dot-separated keys, `[*]` for every element of a list, `[n]` for one element.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Request headers that are part of the match (they change model behaviour) and the only ones
# a cassette stores. Never extend this to auth headers.
MATCHED_HEADERS = ("anthropic-version", "anthropic-beta")


@dataclass(frozen=True)
class ScrubRule:
    """Replace the value at `path`. With `pattern`, only the regex matches inside a string
    value are replaced; without it, the whole value is."""

    path: str
    reason: str
    pattern: str | None = None

    @property
    def placeholder(self) -> str:
        return f"<scrubbed:{self.path}>"


# Filled in from the spike's diff of identical runs. Each entry names the evidence.
SCRUB_RULES: tuple[ScrubRule, ...] = (
    ScrubRule(
        path="metadata.user_id",
        reason=(
            "SPIKE.md: the only body field that differed across identical runs (r1-r4): a JSON "
            "string holding a device_id (new per CLAUDE_CONFIG_DIR) and a per-session UUID"
        ),
    ),
)

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\*|\d+)\]")


def _parse(path: str) -> list[str | int | None]:
    parts: list[str | int | None] = []
    for key, idx in _TOKEN.findall(path):
        if key:
            parts.append(key)
        elif idx == "*":
            parts.append(None)
        else:
            parts.append(int(idx))
    return parts


def _apply(node: Any, parts: list[str | int | None], rule: ScrubRule) -> Any:
    if not parts:
        if rule.pattern is None:
            return rule.placeholder
        if isinstance(node, str):
            return re.sub(rule.pattern, rule.placeholder, node)
        return node
    head, rest = parts[0], parts[1:]
    if isinstance(head, str):
        if isinstance(node, dict) and head in node:
            node[head] = _apply(node[head], rest, rule)
        return node
    if isinstance(node, list):
        if head is None:
            return [_apply(item, rest, rule) for item in node]
        if 0 <= head < len(node):
            node[head] = _apply(node[head], rest, rule)
    return node


def scrub(body: Any, rules: tuple[ScrubRule, ...] = SCRUB_RULES) -> Any:
    out = copy.deepcopy(body)
    for rule in rules:
        out = _apply(out, _parse(rule.path), rule)
    return out


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def matched_headers(headers: Mapping[str, str]) -> dict[str, str]:
    lower = {k.lower(): v for k, v in headers.items()}
    return {h: lower[h] for h in MATCHED_HEADERS if h in lower}


def match_key(
    method: str, path: str, headers: Mapping[str, str], scrubbed_body: Any
) -> dict[str, Any]:
    """Everything the hash covers, as one JSON-able object (also used for miss diffs)."""
    return {
        "method": method.upper(),
        "path": path,
        "headers": matched_headers(headers),
        "body": scrubbed_body,
    }


def request_hash(key: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(key).encode("utf-8")).hexdigest()
