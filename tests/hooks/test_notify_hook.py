"""AUTO-2, item 3: the proposed notification hooks, run for real against a fake `osascript`.

Command hooks run outside the sandbox with the user's full access, and `Stop` input carries model
text. So the command must sit in the settings file, which the session cannot write, and must take
nothing from its input: model text in an AppleScript string is code execution outside the sandbox.

The sandbox blocks the real `osascript`, so this proves that the hook fires and with what, not that
macOS shows it. That is the user's check after install.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.req("AUTO-2")

PROPOSED = Path(__file__).resolve().parents[2] / "tools" / "settings.proposed.json"
HOSTILE = '"; do shell script "touch /tmp/owned"; "'
EVENTS = ("Stop", "StopFailure", "Notification")
PROMPTS = {"permission_prompt", "idle_prompt", "elicitation_dialog"}
SOUND = "sound name"


def settings() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(PROPOSED.read_text())
    return data


def entries(event: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = settings()["hooks"].get(event, [])
    return found


def command(event: str) -> str:
    (entry,) = entries(event)
    (hook,) = entry["hooks"]
    assert hook["type"] == "command"
    text: str = hook["command"]
    return text


def fire(tmp_path: Path, event: str, rc: int = 0) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the proposed command as the harness would: through a shell, with its JSON on stdin."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "osascript.log"
    fake = bin_dir / "osascript"
    fake.write_text(f'#!/bin/sh\ncat > "{log}.stdin"\nprintf "%s\\n" "$@" >> "{log}"\nexit {rc}\n')
    fake.chmod(0o755)
    payload = {
        "hook_event_name": event,
        "session_id": HOSTILE,
        "cwd": HOSTILE,
        "last_assistant_message": HOSTILE,
        "message": HOSTILE,
        "title": HOSTILE,
        "notification_type": "permission_prompt",
    }
    done = subprocess.run(  # noqa: S603
        ["/bin/sh", "-c", command(event)],
        env={"PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin"},
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    return done, log.read_text() if log.exists() else ""


@pytest.mark.parametrize("event", EVENTS)
def test_each_event_fires_a_notification_with_sound(tmp_path: Path, event: str) -> None:
    done, args = fire(tmp_path, event)
    assert done.returncode == 0, done.stderr
    assert args.startswith("-e\ndisplay notification ")
    assert 'with title "Northwind"' in args
    assert SOUND in args


@pytest.mark.parametrize("event", EVENTS)
def test_nothing_from_the_input_reaches_osascript(tmp_path: Path, event: str) -> None:
    _, args = fire(tmp_path, event)
    assert args
    assert "do shell script" not in args
    assert "owned" not in args


@pytest.mark.parametrize("event", EVENTS)
def test_a_failed_notification_cannot_block_the_session(tmp_path: Path, event: str) -> None:
    """Exit 2 from a Stop hook would keep the session running, so nothing here may pass one on."""
    done, _ = fire(tmp_path, event, rc=2)
    assert done.returncode == 0


@pytest.mark.parametrize("event", EVENTS)
@pytest.mark.parametrize("token", ["$", "`", "tools/", "<", " cat", "jq", ".sh", ".py"])
def test_the_command_is_fixed_text_and_runs_no_file(event: str, token: str) -> None:
    assert token not in command(event)


def test_the_notification_entry_covers_every_prompt_that_waits() -> None:
    (entry,) = entries("Notification")
    assert set(entry["matcher"].split("|")) == PROMPTS


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
def test_a_stop_is_not_filtered(event: str) -> None:
    (entry,) = entries(event)
    assert "matcher" not in entry


@pytest.mark.parametrize("event", ["SubagentStop", "PreCompact", "SessionEnd"])
def test_a_subagent_or_a_housekeeping_event_makes_no_noise(event: str) -> None:
    assert entries(event) == []


def test_the_guard_is_still_the_only_pre_tool_hook() -> None:
    (entry,) = entries("PreToolUse")
    (hook,) = entry["hooks"]
    assert hook["command"].endswith('guard_env.py"')
