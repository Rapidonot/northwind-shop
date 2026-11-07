"""NFR-02 / CC-19: a truncated or weakened .claude/settings.json fails CI.

The guard hook and the spend ask rules only work while this file registers them, and the file
was once found emptied in a worktree with nothing failing. `problems` is the check. The weakened
copies below prove it catches each kind of damage, so the check itself cannot go quiet.
"""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.req("NFR-02")

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / ".claude" / "settings.json"
PROPOSED = ROOT / "tools" / "settings.proposed.json"

# Compared whole: `<guard> || true` or an added `async` would keep the path but lose the block.
GUARD_HOOK = {
    "type": "command",
    "command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/guard_env.py"',
}
GUARDED_TOOLS = ("Bash", "Read", "Edit", "MultiEdit", "Write", "NotebookEdit", "Grep", "Glob")
ENV_DENY = (
    "Read(./.env)",
    "Read(./.env.*)",
    "Edit(./.env)",
    "Edit(./.env.*)",
    "Write(./.env)",
    "Write(./.env.*)",
)
SPEND_ASK = ("Bash(make record*)", "Bash(make eval-live*)", "Bash(make llm-proxy*)")
# In the proposal only until the user installs it: the session cannot write the installed file, so
# these fold into SPEND_ASK in the settings install commit (AUTO-2, item 5).
PROPOSED_ASK = (
    "Bash(make *enrich*)",
    "Bash(uv run make *enrich*)",
    "Bash(uv run *northwind_evals*enrich*)",
)


def guarded_tools(settings: dict[str, Any]) -> set[str]:
    """The tools whose calls reach the guard, from every PreToolUse entry that runs it."""
    covered: set[str] = set()
    for entry in settings.get("hooks", {}).get("PreToolUse", []):
        if GUARD_HOOK in entry.get("hooks", []):
            matcher = entry.get("matcher") or "*"
            pattern = ".*" if matcher == "*" else matcher
            covered.update(tool for tool in GUARDED_TOOLS if re.fullmatch(pattern, tool))
    return covered


def problems(raw: str, ask: tuple[str, ...] = SPEND_ASK) -> list[str]:
    try:
        settings = json.loads(raw)
    except ValueError as exc:
        return [f"not valid JSON: {exc}"]
    if not isinstance(settings, dict):
        return ["not a JSON object"]

    found = []
    missing_tools = set(GUARDED_TOOLS) - guarded_tools(settings)
    if missing_tools:
        found.append(f"guard does not run before: {sorted(missing_tools)}")
    if settings.get("disableAllHooks"):
        found.append("disableAllHooks turns the guard off")

    permissions = settings.get("permissions", {})
    if permissions.get("defaultMode") == "bypassPermissions":
        found.append("bypassPermissions skips the ask rules")
    for name, rules in (("deny", ENV_DENY), ("ask", ask)):
        missing_rules = set(rules) - set(permissions.get(name, []))
        if missing_rules:
            found.append(f"missing {name} rules: {sorted(missing_rules)}")
    return found


def test_settings_file_keeps_the_guard_and_the_rules() -> None:
    assert problems(SETTINGS.read_text()) == []


@pytest.mark.req("CC-27")
def test_proposed_settings_keep_the_guard_and_the_rules() -> None:
    """Installing the sandbox proposal must not lose anything this file protects."""
    assert problems(PROPOSED.read_text(), SPEND_ASK + PROPOSED_ASK) == []


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize("rule", PROPOSED_ASK)
def test_a_proposal_without_an_enrich_rule_is_caught(rule: str) -> None:
    settings = json.loads(PROPOSED.read_text())
    settings["permissions"]["ask"].remove(rule)
    assert problems(json.dumps(settings), SPEND_ASK + PROPOSED_ASK)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("", id="emptied"),
        pytest.param("{}", id="empty-object"),
        pytest.param("[]", id="not-an-object"),
    ],
)
def test_empty_settings_are_caught(raw: str) -> None:
    assert problems(raw)


def test_truncated_settings_are_caught() -> None:
    raw = SETTINGS.read_text()
    assert problems(raw[: len(raw) // 2])


def without_rule(section: str, rule: str) -> Callable[[dict[str, Any]], None]:
    return lambda settings: settings["permissions"][section].remove(rule)


def without_guard(settings: dict[str, Any]) -> None:
    del settings["hooks"]["PreToolUse"]


def with_other_command(settings: dict[str, Any]) -> None:
    settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "true"


def with_guard_failure_ignored(settings: dict[str, Any]) -> None:
    settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] += " || true"


def with_async_guard(settings: dict[str, Any]) -> None:
    settings["hooks"]["PreToolUse"][0]["hooks"][0]["async"] = True


def with_bash_only_matcher(settings: dict[str, Any]) -> None:
    settings["hooks"]["PreToolUse"][0]["matcher"] = "Bash"


def with_hooks_disabled(settings: dict[str, Any]) -> None:
    settings["disableAllHooks"] = True


def with_bypass_mode(settings: dict[str, Any]) -> None:
    settings["permissions"]["defaultMode"] = "bypassPermissions"


@pytest.mark.parametrize(
    "weaken",
    [
        *(pytest.param(without_rule("deny", rule), id=f"deny-{rule}") for rule in ENV_DENY),
        *(pytest.param(without_rule("ask", rule), id=f"ask-{rule}") for rule in SPEND_ASK),
        pytest.param(without_guard, id="guard-removed"),
        pytest.param(with_other_command, id="guard-replaced"),
        pytest.param(with_guard_failure_ignored, id="guard-failure-ignored"),
        pytest.param(with_async_guard, id="guard-async"),
        pytest.param(with_bash_only_matcher, id="matcher-narrowed"),
        pytest.param(with_hooks_disabled, id="hooks-disabled"),
        pytest.param(with_bypass_mode, id="bypass-mode"),
    ],
)
def test_weakened_settings_are_caught(weaken: Callable[[dict[str, Any]], None]) -> None:
    settings = json.loads(SETTINGS.read_text())
    weaken(settings)
    assert problems(json.dumps(settings))
