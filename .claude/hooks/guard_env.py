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
# After `cd .claude`, these bare names mean the same files as PROTECTED.
PROTECTED_NAME = re.compile(r"settings(\.local)?\.json|\bhooks\b")
SEGMENT_SPLIT = re.compile(r"[;&|\n]+")
# env, set or export with no arguments, at the start of a segment, in a subshell or in $(...).
# With arguments they are ordinary commands: `set -e`, `env FOO=1 cmd`, `/usr/bin/env python3`.
ENV_DUMP = re.compile(
    r"^\s*export\s+-p\b"
    r"|(^\s*\(*|\$\()\s*((/usr)?/bin/)?(env|set|export)\s*($|\)|\d*>)"
)
PRINTENV = re.compile(r"\bprintenv\b")
KEY_REF = re.compile(r"\$\{?ANTHROPIC_API_KEY")
SAFE_GIT = re.compile(r"^\s*git\s+(status|diff|log|show|add|commit|ls-files)\b")
# What an allowed git command writes to. A commit message is text, so it is removed first.
GIT_WRITE = re.compile(r"(?:>+|--output[=\s])\s*\S+")
GIT_MESSAGE = re.compile(r"""\s-a?m\s*('[^']*'|"[^"]*")""")
CD = re.compile(r"^[\s(]*(cd|pushd)\s")
CD_INTO_CLAUDE = re.compile(r"""\.claude/?["']?\s*$""", re.IGNORECASE)


def touches_env(text: str) -> bool:
    return any((m.group(1) or "").lower() not in ALLOWED_SUFFIXES for m in ENV_FILE.finditer(text))


def names_protected(text: str, in_claude: bool = False) -> bool:
    """Lowercased because macOS paths are not case-sensitive."""
    text = text.lower()
    return any(p in text for p in PROTECTED) or (in_claude and bool(PROTECTED_NAME.search(text)))


def block(reason: str) -> NoReturn:
    print(f"Blocked by guard_env.py: {reason}", file=sys.stderr)
    sys.exit(2)


def check_bash(cmd: str) -> None:
    if touches_env(cmd):
        block("shell commands may not reference .env files (hard rule 4).")
    if PRINTENV.search(cmd) or KEY_REF.search(cmd):
        block("shell commands may not dump the environment or reference ANTHROPIC_API_KEY.")
    in_claude = False
    for seg in SEGMENT_SPLIT.split(cmd):
        if ENV_DUMP.search(seg):
            block("shell commands may not dump the environment.")
        if SAFE_GIT.match(seg):
            checked = " ".join(GIT_WRITE.findall(GIT_MESSAGE.sub("", seg)))
        else:
            checked = seg
        if names_protected(checked, in_claude):
            block("Claude may not change its own settings or hooks; ask the user.")
        if CD.match(seg):
            in_claude = bool(CD_INTO_CLAUDE.search(seg))


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
        if names_protected(path):
            block("Claude may not change its own settings or hooks; ask the user.")
    else:  # Read, Grep, Glob
        keys = ("file_path", "path", "glob") + (("pattern",) if tool == "Glob" else ())
        if any(touches_env(str(inp.get(k, ""))) for k in keys):
            block("reading .env files is not allowed (hard rule 4).")
    sys.exit(0)


if __name__ == "__main__":
    main()
