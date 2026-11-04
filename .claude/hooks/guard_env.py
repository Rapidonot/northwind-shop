#!/usr/bin/env python3
"""PreToolUse guard (hard rule 4, NFR-02; CC-19).

Blocks tool calls that read or write .env files, dump the environment, or change
Claude's own settings and hooks. Exit 2 blocks the call; stderr goes back to Claude.
A speed bump, not a sandbox.
"""

import json
import re
import sys

ENV_FILE = re.compile(r"\.env(\.[\w.-]+)?(?!\w)")
ALLOWED_SUFFIXES = {".example"}
PROTECTED = (".claude/settings.json", ".claude/settings.local.json", ".claude/hooks/")
ENV_DUMP = re.compile(
    r"(^|[;&|]\s*)(printenv|env|export -p|set)\s*($|[;&|])|\$\{?ANTHROPIC_API_KEY"
)


def touches_env(text: str) -> bool:
    return any(m.group(1) not in ALLOWED_SUFFIXES for m in ENV_FILE.finditer(text))


def block(reason: str) -> None:
    print(f"Blocked by guard_env.py: {reason}", file=sys.stderr)
    sys.exit(2)


data = json.load(sys.stdin)
tool = data.get("tool_name", "")
inp = data.get("tool_input") or {}

if tool == "Bash":
    cmd = inp.get("command", "")
    if touches_env(cmd):
        block("shell commands may not reference .env files (hard rule 4).")
    if ENV_DUMP.search(cmd):
        block("shell commands may not dump the environment or reference ANTHROPIC_API_KEY.")
    if not cmd.lstrip().startswith("git ") and any(p in cmd for p in PROTECTED):
        block("Claude may not change its own settings or hooks; ask the user.")
elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
    path = inp.get("file_path") or inp.get("notebook_path") or ""
    if touches_env(path):
        block("editing .env files is not allowed (hard rule 4).")
    if any(p in path for p in PROTECTED):
        block("Claude may not change its own settings or hooks; ask the user.")
else:  # Read, Grep, Glob
    keys = ("file_path", "path", "glob") + (("pattern",) if tool == "Glob" else ())
    if any(touches_env(str(inp.get(k, ""))) for k in keys):
        block("reading .env files is not allowed (hard rule 4).")

sys.exit(0)
