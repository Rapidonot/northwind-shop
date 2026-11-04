#!/usr/bin/env python3
"""PreToolUse guard (hard rule 4, NFR-02; CC-19).

Blocks tool calls that read or write .env files, dump the environment, or change
Claude's own settings and hooks. Exit 2 blocks the call; stderr goes back to Claude.
Fails closed on unreadable input. A speed bump, not a sandbox.
"""

import json
import re
import sys
from typing import NoReturn

ENV_FILE = re.compile(r"\.env(\.[\w.-]+)?(?!\w)", re.IGNORECASE)
ALLOWED_SUFFIXES = {".example"}
PROTECTED = (".claude/settings.json", ".claude/settings.local.json", ".claude/hooks")
SEGMENT_SPLIT = re.compile(r"[;&|\n]+")
ENV_DUMP = re.compile(r"^\s*(export\s+-p\b|(env|set|export)\s*$)")
PRINTENV = re.compile(r"\bprintenv\b")
KEY_REF = re.compile(r"\$\{?ANTHROPIC_API_KEY")
SAFE_GIT = re.compile(r"^\s*git\s+(status|diff|log|show|add|commit|ls-files)\b")


def touches_env(text: str) -> bool:
    return any((m.group(1) or "").lower() not in ALLOWED_SUFFIXES for m in ENV_FILE.finditer(text))


def block(reason: str) -> NoReturn:
    print(f"Blocked by guard_env.py: {reason}", file=sys.stderr)
    sys.exit(2)


def check_bash(cmd: str) -> None:
    if touches_env(cmd):
        block("shell commands may not reference .env files (hard rule 4).")
    if PRINTENV.search(cmd) or KEY_REF.search(cmd):
        block("shell commands may not dump the environment or reference ANTHROPIC_API_KEY.")
    for seg in SEGMENT_SPLIT.split(cmd):
        if ENV_DUMP.match(seg):
            block("shell commands may not dump the environment.")
        if any(p in seg for p in PROTECTED) and not SAFE_GIT.match(seg):
            block("Claude may not change its own settings or hooks; ask the user.")


def main() -> None:
    try:
        data = json.load(sys.stdin)
        tool = str(data.get("tool_name", ""))
        inp = data.get("tool_input") or {}
    except (ValueError, AttributeError):
        block("unreadable hook input; failing closed.")
    if not isinstance(inp, dict):
        block("unexpected tool_input; failing closed.")

    if tool == "Bash":
        check_bash(str(inp.get("command", "")))
    elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        if touches_env(path):
            block("editing .env files is not allowed (hard rule 4).")
        if any(p in path for p in PROTECTED):
            block("Claude may not change its own settings or hooks; ask the user.")
    else:  # Read, Grep, Glob
        keys = ("file_path", "path", "glob") + (("pattern",) if tool == "Glob" else ())
        if any(touches_env(str(inp.get(k, ""))) for k in keys):
            block("reading .env files is not allowed (hard rule 4).")
    sys.exit(0)


if __name__ == "__main__":
    main()
