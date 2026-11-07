"""AUTO-2: the proposed `req-start` and `req-done` skills, read as data.

The session cannot write the installed skills, so the user installs these.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("AUTO-2")

TOOLS = Path(__file__).resolve().parents[2] / "tools"
START = (TOOLS / "req-start.proposed.SKILL.md").read_text()
DONE = (TOOLS / "req-done.proposed.SKILL.md").read_text()


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def step(text: str, number: int) -> str:
    match = re.search(rf"^{number}\. (.*?)(?=^\d+\. |\Z)", text, re.M | re.S)
    assert match, f"no step {number}"
    return flat(match.group(1))


@pytest.mark.parametrize("text", [START, DONE], ids=["req-start", "req-done"])
def test_only_the_user_can_run_them(text: str) -> None:
    assert "disable-model-invocation: true" in text


def test_req_start_waits_for_an_approved_plan() -> None:
    plan = step(START, 5)
    assert "Do not start implementing until the plan is approved" in plan


def test_req_start_lets_the_advisor_approve_only_a_routine_row() -> None:
    plan = step(START, 5)
    assert "by the user" in plan
    assert "routine row" in plan
    assert "`advisor-reviewer`" in plan


def test_req_start_keeps_the_dependency_stop() -> None:
    assert "stop and ask the user whether to go on" in step(START, 3)


def test_req_done_keeps_its_own_ci_run() -> None:
    assert "`make ci`" in step(DONE, 1)
    assert "If it fails, show the output and stop" in step(DONE, 1)


def test_req_done_does_not_run_the_gate_twice() -> None:
    gate = step(DONE, 3)
    assert "do not run `make eval-gate` again" in gate
    assert "treat a regression as a failure" in gate


def test_req_done_still_needs_a_passing_gate_to_say_done() -> None:
    assert "the eval gate did not regress" in step(DONE, 4)
