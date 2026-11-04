"""NFR-02 / CC-19: pins what the PreToolUse guard blocks and allows.

Runs the real hook as a subprocess with Claude Code's JSON on stdin, the way the harness does,
and asserts the exit code (2 blocks, 0 allows) and stderr. Every command here is only passed
to the hook as data; none of them is ever executed. GUARD_HOOK_PATH points the suite at another
copy of the hook, such as a proposed fix; the default is the installed one.

xfail(strict=True) marks a gap that still gets through, so fixing the hook flips the test.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.req("NFR-02")

INSTALLED_HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "guard_env.py"
HOOK = Path(os.environ.get("GUARD_HOOK_PATH", INSTALLED_HOOK))

ENV_MSG = "may not reference .env files"
DUMP_MSG = "may not dump the environment"
SELF_MSG = "may not change its own settings or hooks"
EDIT_ENV_MSG = "editing .env files is not allowed"
READ_ENV_MSG = "reading .env files is not allowed"
CLOSED_MSG = "failing closed"


def run_raw(stdin: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603  # our own hook, fixed argv
        [sys.executable, str(HOOK)], input=stdin, capture_output=True, timeout=30, check=False
    )


def run_hook(tool: str, tool_input: dict[str, Any]) -> subprocess.CompletedProcess[bytes]:
    return run_raw(json.dumps({"tool_name": tool, "tool_input": tool_input}).encode())


def assert_blocked(result: subprocess.CompletedProcess[bytes], message: str) -> None:
    stderr = result.stderr.decode()
    assert result.returncode == 2, f"not blocked: exit {result.returncode}, {stderr!r}"
    assert "Blocked by guard_env.py" in stderr
    assert message in stderr


def assert_allowed(result: subprocess.CompletedProcess[bytes]) -> None:
    assert result.returncode == 0, f"blocked: {result.stderr.decode()!r}"
    assert result.stderr == b""


def gap(command: str, reason: str) -> Any:
    return pytest.param(command, marks=pytest.mark.xfail(strict=True, reason=reason))


@pytest.mark.parametrize(
    "command",
    [
        "cat .env",
        "cat .env.local",
        "cat config/.env.production",
        "cat .ENV",
        "cat .Env.Local",
        "git show HEAD:.env",
        "echo x > .env",
        "cat .env.example .env",
        "cat .env.example.bak",
    ],
)
def test_bash_env_file_is_blocked(command: str) -> None:
    assert_blocked(run_hook("Bash", {"command": command}), ENV_MSG)


@pytest.mark.parametrize(
    "command",
    [
        "printenv",
        "printenv ANTHROPIC_API_KEY",
        "ls\nprintenv",
        "/usr/bin/printenv",
        "env",
        "  env",
        "env | sort",
        "ls; env",
        "export",
        "export -p",
        "set",
        "echo $ANTHROPIC_API_KEY",
        "echo ${ANTHROPIC_API_KEY}",
        "env > dump.txt",
        "env>dump.txt",
        "set > dump.txt",
        "export > dump.txt",
        "echo $(env)",
        "echo $(set)",
        "echo $(env | sort)",
        "(env)",
        "/usr/bin/env",
        "/usr/bin/env > dump.txt",
    ],
)
def test_bash_environment_dump_is_blocked(command: str) -> None:
    assert_blocked(run_hook("Bash", {"command": command}), DUMP_MSG)


@pytest.mark.parametrize(
    "command",
    [
        "rm .claude/settings.json",
        "rm .claude/settings.local.json",
        "rm .claude/hooks/guard_env.py",
        "cd .claude/hooks && rm guard_env.py",
        "git status && rm .claude/settings.json",
        "git log; rm -rf .claude/hooks",
        "git checkout -- .claude/settings.json",
        "sed -i '' s/a/b/ .claude/settings.json",
        "git diff > .claude/settings.json",
        "git diff >> .claude/settings.json",
        'git diff > ".claude/settings.json"',
        "git diff > .CLAUDE/settings.json",
        "git show HEAD~1:.claude/hooks/guard_env.py > .claude/hooks/guard_env.py",
        "git diff --output=.claude/settings.json",
        "git diff --output .claude/settings.json",
        "git commit -m 'x' > .claude/settings.json",
        "cd .claude && rm settings.json",
        "cd .claude/ && rm settings.local.json",
        "cd /repo/.claude; rm -rf hooks",
        "cd .claude && cd hooks",
        "(cd .claude && rm settings.json)",
        "pushd .claude && rm settings.json",
        "cd .claude && git diff > settings.json",
        "rm .CLAUDE/settings.json",
        "rm .Claude/Hooks/guard_env.py",
    ],
)
def test_bash_protected_path_is_blocked(command: str) -> None:
    assert_blocked(run_hook("Bash", {"command": command}), SELF_MSG)


SUBSTITUTION = "R1: a substitution inside an allowed git segment is not inspected"
GIT_MESSAGE_QUOTING = "R2: GIT_MESSAGE ignores quoting, so a real redirect is stripped as text"
PATH_SPELLING = "R3: protected paths are matched as literal strings"
CD_TRACKING = "R4: cd tracking needs cd at the start of a segment and a path ending in .claude"


@pytest.mark.parametrize(
    "command",
    [
        gap("rm -rf .claude", "removes the parent, so no protected path appears in the text"),
        gap(
            "git -C .claude checkout -- settings.json",
            "the bare name is only protected after cd",
        ),
        gap("git status $(rm .claude/settings.json)", SUBSTITUTION),
        gap("git log `cp /tmp/weak.json .claude/settings.json`", SUBSTITUTION),
        gap('git commit -m "$(rm .claude/hooks/guard_env.py)"', SUBSTITUTION),
        gap("git diff > >(tee .claude/settings.json)", SUBSTITUTION),
        gap("""git diff " -m '" > .claude/settings.json "'" """, GIT_MESSAGE_QUOTING),
        gap("""git log --grep=" -m 'x" > .claude/settings.json # '""", GIT_MESSAGE_QUOTING),
        gap("git diff > .claude/./settings.json", PATH_SPELLING),
        gap("git diff --output=.claude//settings.json", PATH_SPELLING),
        gap("rm .claude//hooks/guard_env.py", PATH_SPELLING),
        gap("cd .claude/agents && rm ../settings.json", CD_TRACKING),
        gap("cd .claude/agents && git diff > ../settings.json", CD_TRACKING),
        gap("cd .claude/. && rm settings.json", CD_TRACKING),
        gap("{ cd .claude; rm settings.json; }", CD_TRACKING),
        gap("if cd .claude; then rm settings.json; fi", CD_TRACKING),
    ],
)
def test_bash_protected_path_gaps(command: str) -> None:
    assert_blocked(run_hook("Bash", {"command": command}), SELF_MSG)


@pytest.mark.xfail(strict=True, reason=PATH_SPELLING)
def test_write_to_protected_path_in_other_spelling_gap() -> None:
    result = run_hook("Write", {"file_path": "/repo/.claude/./settings.json"})
    assert_blocked(result, SELF_MSG)


@pytest.mark.parametrize(
    "command",
    [
        gap(
            "python3 -c 'import os; print(os.environ[\"ANTHROPIC_API_KEY\"])'",
            "KEY_REF only matches shell expansion",
        ),
        gap("env -0", "env with a flag reads as an ordinary command"),
        gap("echo `env`", "backticks are not parsed; $(env) is blocked"),
    ],
)
def test_bash_environment_dump_gaps(command: str) -> None:
    assert_blocked(run_hook("Bash", {"command": command}), DUMP_MSG)


@pytest.mark.parametrize(
    "command",
    [
        gap("cat .en*", "shell globbing hides the name; the hook is a speed bump"),
        gap("cat .e''nv", "shell quoting hides the name; the hook is a speed bump"),
    ],
)
def test_bash_obfuscated_env_file_gaps(command: str) -> None:
    assert_blocked(run_hook("Bash", {"command": command}), ENV_MSG)


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
@pytest.mark.parametrize("path", [".env", "/repo/.env.local", "/repo/.ENV"])
def test_edit_tools_on_env_file_are_blocked(tool: str, path: str) -> None:
    assert_blocked(run_hook(tool, {"file_path": path}), EDIT_ENV_MSG)


def test_notebook_edit_on_env_file_is_blocked() -> None:
    assert_blocked(run_hook("NotebookEdit", {"notebook_path": "/repo/.env"}), EDIT_ENV_MSG)


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
@pytest.mark.parametrize(
    "path",
    [
        "/repo/.claude/settings.json",
        "/repo/.claude/settings.local.json",
        "/repo/.claude/hooks/guard_env.py",
        "/repo/.CLAUDE/settings.json",
    ],
)
def test_edit_tools_on_protected_path_are_blocked(tool: str, path: str) -> None:
    assert_blocked(run_hook(tool, {"file_path": path}), SELF_MSG)


def test_notebook_edit_on_protected_path_is_blocked() -> None:
    result = run_hook("NotebookEdit", {"notebook_path": "/repo/.claude/hooks/x.ipynb"})
    assert_blocked(result, SELF_MSG)


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "/repo/.env"}),
        ("Read", {"file_path": "/repo/.env.local"}),
        ("Read", {"file_path": "/repo/.ENV"}),
        ("Grep", {"pattern": "KEY", "path": "/repo/.env"}),
        ("Grep", {"pattern": "KEY", "glob": ".env*"}),
        ("Glob", {"pattern": "**/.env*"}),
        ("Glob", {"pattern": "*", "path": "/repo/.env.local"}),
    ],
)
def test_read_tools_on_env_file_are_blocked(tool: str, tool_input: dict[str, str]) -> None:
    assert_blocked(run_hook(tool, tool_input), READ_ENV_MSG)


@pytest.mark.parametrize(
    "stdin",
    [
        b"",
        b"not json",
        b"[]",
        b"null",
        b"\xff\xfe",
        b'{"tool_name": "Bash", "tool_input": "cat x"}',
    ],
)
def test_unreadable_input_fails_closed(stdin: bytes) -> None:
    assert_blocked(run_raw(stdin), CLOSED_MSG)


@pytest.mark.parametrize(
    "command",
    [
        "cat .env.example",
        "cat .ENV.EXAMPLE",
        "cat env.example",
        "cat .envrc",
        ".venv/bin/python -V",
        "python3 -c 'import os; print(sorted(os.environ))'",
        "git status .claude/settings.json",
        "git diff .claude/hooks/guard_env.py",
        "git log -- .claude/settings.json",
        "git add .claude/hooks/guard_env.py",
        "git commit -m 'touch .claude/hooks'",
        "git ls-files .claude/hooks",
        "ls -la",
        "make test",
        "uv run pytest tests/hooks",
        "set -e; make test",
        "set -euo pipefail",
        "env FOO=1 make test",
        "env FOO=1 cmd",
        "/usr/bin/env python3",
        "export FOO=1",
        "echo '(env)'",
        "git diff .claude/hooks/guard_env.py > /tmp/hook.diff",
        "git commit -m 'block git diff > .claude/settings.json'",
        "git commit -am 'block --output=.claude/settings.json'",
        "cd .claude && ls",
        "cd .claude && git add settings.json",
        "cd .claude && cd .. && ls tests/hooks",
    ],
)
def test_bash_ordinary_commands_are_allowed(command: str) -> None:
    assert_allowed(run_hook("Bash", {"command": command}))


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "/repo/.env.example"}),
        ("Read", {"file_path": "/repo/.envrc"}),
        ("Read", {"file_path": "/repo/env.example"}),
        ("Read", {"file_path": "/repo/.venv/pyvenv.cfg"}),
        ("Read", {"file_path": "/repo/.claude/settings.json"}),
        ("Read", {"file_path": "/repo/.claude/hooks/guard_env.py"}),
        ("Grep", {"pattern": ".env"}),
        ("Glob", {"pattern": "**/*.py"}),
        ("Edit", {"file_path": "/repo/.envrc"}),
        ("Edit", {"file_path": "/repo/env.example"}),
        ("Write", {"file_path": "/repo/.env.example"}),
        ("Write", {"file_path": "/repo/.claude/agents/test-writer.md"}),
        ("Write", {"file_path": "/repo/.claude/skills/req-start/SKILL.md"}),
        ("Write", {"file_path": "/repo/tests/hooks/test_guard_env.py"}),
    ],
)
def test_file_tools_on_ordinary_paths_are_allowed(tool: str, tool_input: dict[str, str]) -> None:
    assert_allowed(run_hook(tool, tool_input))
