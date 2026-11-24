"""Tool results in `tool_use` order (row #16, decision 0001's amendment).

The Claude CLI runs read-only tools in parallel and appends their results as they finish, so one
model turn can produce request bodies that differ only in the order of their `tool_result` blocks,
and a replay then misses on timing (docs/reviews/2026-11-23-16-tool-order-probe.txt). The proxy
puts each user message's results back in the order of the preceding assistant message's `tool_use`
blocks before it scrubs, hashes, logs, scans, prices or forwards anything, so the model's input
does not depend on which tool finished first.
"""

from __future__ import annotations

import json
from typing import Any


def _tool_use_rank(content: list[Any]) -> dict[str, int]:
    """Each `tool_use` id's first position, built once per assistant message (security review L1:
    linear in the body, not users x tool uses)."""
    rank: dict[str, int] = {}
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("id"), str):
            rank.setdefault(b["id"], len(rank))
    return rank


def _ordered(content: list[Any], rank: dict[str, int]) -> list[Any] | None:
    """`content` with the results that answer `rank`'s tool uses in that order, in the slots results
    already hold; every other block, and a result for no listed `tool_use`, keeps its place."""
    slots = [
        i
        for i, b in enumerate(content)
        if isinstance(b, dict)
        and b.get("type") == "tool_result"
        and isinstance(b.get("tool_use_id"), str)
        and b["tool_use_id"] in rank
    ]
    if len(slots) < 2:
        return None
    blocks = sorted((content[i] for i in slots), key=lambda b: rank[b["tool_use_id"]])  # stable
    if all(content[i] is block for i, block in zip(slots, blocks, strict=True)):
        return None
    out = list(content)
    for i, block in zip(slots, blocks, strict=True):
        out[i] = _in_slot(block, content[i])
    return out


# Keys that belong to a block's position, not to the block: the CLI marks the message's last block
# as a cache breakpoint, so the marker stays in its slot when the results move.
_POSITIONAL = ("cache_control",)


def _in_slot(block: dict[str, Any], held: dict[str, Any]) -> dict[str, Any]:
    """`block` moved into the slot `held` had: with that slot's positional keys, not its own."""
    moved = {k: v for k, v in block.items() if k not in _POSITIONAL}
    moved.update({k: held[k] for k in _POSITIONAL if k in held})
    return moved


def ordered_tool_results(body: Any) -> Any | None:
    """`body` with every user message's tool results in `tool_use` order, or None if it already
    is (or is not a messages body). The input is never modified."""
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return None
    messages: list[Any] = []
    rank: dict[str, int] = {}
    changed = False
    for message in body["messages"]:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and message.get("role") == "assistant":
            rank = _tool_use_rank(content)
        elif isinstance(content, list) and message.get("role") == "user" and rank:
            ordered = _ordered(content, rank)
            if ordered is not None:
                message = {**message, "content": ordered}
                changed = True
        messages.append(message)
    return {**body, "messages": messages} if changed else None


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def reordered_request(raw: bytes, body: Any) -> tuple[bytes, Any] | None:
    """The bytes and body to use instead of `raw`/`body`, or None to use them unchanged.

    None also when a reorder is due but the body has more than one reading (not strict UTF-8, or a
    duplicate key) or does not serialize back as strict JSON (a non-finite number, a lone
    surrogate): the original bytes then go on, so the runner gate refuses or passes them exactly as
    before (security review L2, L3)."""
    ordered = ordered_tool_results(body)
    if ordered is None:
        return None
    try:
        json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
        text = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return text.encode("utf-8"), ordered
    except (ValueError, RecursionError):  # UnicodeDecodeError/EncodeError are ValueErrors
        return None
