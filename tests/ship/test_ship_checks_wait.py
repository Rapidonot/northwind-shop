"""AUTO-1: `ship` waits for the required checks to appear before it watches them.

DEV-1 removed the branch push trigger, so a PR's checks exist only after the PR does. `gh pr checks
--watch` returns at once when none exists, and the bucket read then refused on a `pending` check
(PR #48). Plan addendum 8, update 3: poll `gh pr checks <pr> --json name,workflow` until every job
in REQUIRED_JOBS is present, every SHIP_CHECKS_POLL s (default 15), up to SHIP_CHECKS_WAIT s
(default 600, capped at 600), then refuse naming the missing jobs. Both settings must be digits.

The rig, the fake `gh` and the assertions are the ones in test_ship.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.req("AUTO-1"), pytest.mark.ship]

_spec = importlib.util.spec_from_file_location("ship_rig", Path(__file__).with_name("test_ship.py"))
assert _spec is not None and _spec.loader is not None
ship_rig = sys.modules.get("ship_rig") or importlib.util.module_from_spec(_spec)
if "ship_rig" not in sys.modules:
    sys.modules["ship_rig"] = ship_rig
    _spec.loader.exec_module(ship_rig)

rig = ship_rig.rig  # the fixture
Rig = ship_rig.Rig
PR: str = ship_rig.PR
assert_refused = ship_rig.assert_refused
assert_merged = ship_rig.assert_merged
checks = ship_rig.checks

# A sleep that records its argument and returns at once. After the second call the checks appear
# (the first two polls saw none), so a test can see the poll interval without waiting for it.
FAKE_SLEEP = """#!/bin/sh
echo "$1" >> "$GH_SCENARIO/sleep.log"
n=$(wc -l < "$GH_SCENARIO/sleep.log")
if [ "$n" -ge 2 ]; then echo 0 > "$GH_SCENARIO/checks_empty_calls"; fi
"""


def poll_calls(rig: Rig) -> tuple[list[int], list[int]]:
    """Indexes of the `pr checks <pr> --json` calls before and after the --watch call."""
    calls = rig.gh_calls()
    watch = [i for i, c in enumerate(calls) if c.startswith(f"pr checks {PR}") and "--watch" in c]
    assert len(watch) <= 1, calls
    polls = [
        i
        for i, c in enumerate(calls)
        if c.startswith(f"pr checks {PR}") and "--json" in c and "--watch" not in c
    ]
    cut = watch[0] if watch else len(calls)
    return [i for i in polls if i < cut], [i for i in polls if i > cut]


def watch_calls(rig: Rig) -> list[str]:
    return [c for c in rig.gh_calls() if c.startswith("pr checks") and "--watch" in c]


def settings(rig: Rig, wait: str, poll: str = "1") -> None:
    rig.sh_env["SHIP_CHECKS_WAIT"] = wait
    rig.sh_env["SHIP_CHECKS_POLL"] = poll


# -- the bug ----------------------------------------------------------------------------------


def test_checks_that_appear_after_the_pr_opens_do_not_refuse_as_pending(rig: Rig) -> None:
    """The bug itself: nothing on the first call, then every check passes."""
    settings(rig, "30")
    rig.checks_empty_calls = 1
    assert_merged(rig, rig.ship())


# -- the wait ---------------------------------------------------------------------------------


def test_polls_until_the_checks_appear_then_watches_then_merges(rig: Rig) -> None:
    settings(rig, "30")
    rig.checks_empty_calls = 2
    done = rig.ship()
    assert_merged(rig, done)
    before, _ = poll_calls(rig)
    assert len(before) >= 3, rig.gh_calls()  # two empty polls, then the one that sees them
    assert len(watch_calls(rig)) == 1, rig.gh_calls()
    calls = rig.gh_calls()
    watch_at = next(i for i, c in enumerate(calls) if "--watch" in c)
    assert all(i < watch_at for i in before)


def test_the_poll_asks_for_name_and_workflow(rig: Rig) -> None:
    settings(rig, "30")
    rig.checks_empty_calls = 1
    assert_merged(rig, rig.ship())
    before, _ = poll_calls(rig)
    first = rig.gh_calls()[before[0]]
    assert "--json name,workflow" in first, first
    assert "--jq" in first, first


def test_a_required_job_that_never_appears_is_refused_at_the_deadline(rig: Rig) -> None:
    settings(rig, "2")
    rig.checks = [c for c in checks() if c["name"] != "eval-gate"]
    done = rig.ship()
    assert_refused(rig, done, "CI/eval-gate")
    assert "not started" in (done.stdout + done.stderr).lower()
    assert watch_calls(rig) == [], rig.gh_calls()
    before, _ = poll_calls(rig)
    assert len(before) >= 2, rig.gh_calls()


def test_every_missing_job_is_named(rig: Rig) -> None:
    settings(rig, "1")
    rig.checks = [c for c in checks() if c["name"] not in ("e2e", "baseline-label")]
    done = rig.ship()
    assert_refused(rig, done, "CI/e2e")
    assert "Guard/baseline-label" in done.stdout + done.stderr


def test_no_checks_at_all_is_refused_and_says_ci_has_not_started(rig: Rig) -> None:
    settings(rig, "1")
    rig.checks_empty_calls = 1000
    done = rig.ship()
    assert_refused(rig, done, "CI/python")
    assert "not started" in (done.stdout + done.stderr).lower()
    assert watch_calls(rig) == []


def test_checks_present_on_the_first_poll_poll_once_and_do_not_sleep(rig: Rig) -> None:
    settings(rig, "30", "5")
    sleep = rig.root / "bin" / "sleep"
    sleep.write_text(FAKE_SLEEP)
    sleep.chmod(0o755)
    assert_merged(rig, rig.ship())
    before, _ = poll_calls(rig)
    assert len(before) == 1, rig.gh_calls()
    assert not (rig.scn / "sleep.log").exists()
    assert len(watch_calls(rig)) == 1


def test_after_the_wait_the_watch_and_the_bucket_read_are_unchanged(rig: Rig) -> None:
    settings(rig, "30")
    rig.checks_empty_calls = 1
    rig.checks = checks(python="fail")
    assert_refused(rig, rig.ship(), "python")
    before, after = poll_calls(rig)
    assert before and after, rig.gh_calls()  # the poll, then the bucket read after --watch
    assert any("--watch" in c and "--interval 30" in c for c in rig.gh_calls())


# -- the settings -----------------------------------------------------------------------------

BAD = ["1m", "-1", "", "10;x", "1.5", " 5", "0x10", "$(true)"]


@pytest.mark.parametrize("value", BAD)
def test_a_wait_that_is_not_digits_is_refused_before_any_checks_call(rig: Rig, value: str) -> None:
    # "" is chosen as not digits: a set-but-empty value refuses (it is not silently the default).
    settings(rig, value)
    done = rig.ship()
    assert_refused(rig, done, "SHIP_CHECKS_WAIT")
    assert [c for c in rig.gh_calls() if c.startswith(f"pr checks {PR}")] == []


@pytest.mark.parametrize("value", BAD)
def test_a_poll_that_is_not_digits_is_refused_before_any_checks_call(rig: Rig, value: str) -> None:
    settings(rig, "30", value)
    done = rig.ship()
    assert_refused(rig, done, "SHIP_CHECKS_POLL")
    assert [c for c in rig.gh_calls() if c.startswith(f"pr checks {PR}")] == []


def test_a_wait_above_the_cap_is_treated_as_600(rig: Rig) -> None:
    """Observed through the sleep: both settings absurd, so the first sleep is the capped value.

    A real 600 s deadline cannot run inside the 180 s timeout. `sleep` is faked: it logs its
    argument and, on its second call, makes the checks appear, so the run finishes at once.
    """
    settings(rig, "99999", "99999")
    sleep = rig.root / "bin" / "sleep"
    sleep.write_text(FAKE_SLEEP)
    sleep.chmod(0o755)
    rig.checks_empty_calls = 1000
    assert_merged(rig, rig.ship())
    slept = (rig.scn / "sleep.log").read_text().split()
    assert slept, "never slept between polls"
    assert all(s.isdigit() and 1 <= int(s) <= 600 for s in slept), slept


def test_a_poll_above_the_wait_is_cut_to_the_wait(rig: Rig) -> None:
    settings(rig, "7", "500")
    sleep = rig.root / "bin" / "sleep"
    sleep.write_text(FAKE_SLEEP)
    sleep.chmod(0o755)
    rig.checks_empty_calls = 1000
    assert_merged(rig, rig.ship())
    slept = (rig.scn / "sleep.log").read_text().split()
    assert slept and all(s.isdigit() and 1 <= int(s) <= 7 for s in slept), slept


def test_a_zero_poll_is_raised_to_one_second(rig: Rig) -> None:
    settings(rig, "30", "0")
    sleep = rig.root / "bin" / "sleep"
    sleep.write_text(FAKE_SLEEP)
    sleep.chmod(0o755)
    rig.checks_empty_calls = 1000
    assert_merged(rig, rig.ship())
    slept = (rig.scn / "sleep.log").read_text().split()
    assert slept and all(s.isdigit() and int(s) >= 1 for s in slept), slept
