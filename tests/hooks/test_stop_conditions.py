"""AUTO-1: the "Running a row" section of CLAUDE.md, read as data."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("AUTO-1")

CLAUDE = (Path(__file__).resolve().parents[2] / "CLAUDE.md").read_text()


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def running_a_row() -> str:
    match = re.search(r"^## Running a row.*?(?=^## |\Z)", CLAUDE, re.M | re.S)
    assert match, "no 'Running a row' section"
    return match.group(0)


SECTION = running_a_row()
FLAT = flat(SECTION)


def stop_items() -> list[str]:
    after = SECTION.split("Stop only for:", 1)[1]
    items: list[str] = []
    for line in after.splitlines():
        if re.match(r"\d+\. ", line):
            items.append(line)
        elif items and line.startswith((" ", "\t")) and line.strip():
            items[-1] += " " + line.strip()
        elif items and not line.strip():
            break
    return [flat(item) for item in items]


STOPS = stop_items()

# (name, patterns that must all appear in one numbered stop)
REQUIRED_STOPS = [
    ("plan approval", [r"plan approval"]),
    ("spending window", [r"spending window"]),
    (
        "terminal-only steps",
        [r"\bpush\b", r"\bpr\b", r"\bmerge\b", r"settings install", r"runner-sync", r"docker"],
    ),
    ("git error strings", [r"\bgit\b", r"five error strings"]),
    (
        "weakening a test or loosening a check",
        [
            r"weakening a test after a failure",
            r"loosening any check, gate, cap, scanner, baseline",
            r"known_failures\.json",
        ],
    ),
    ("unsafe security finding", [r"unsafe security finding", r"changes the design"]),
    ("scope question", [r"scope question"]),
    ("control surface in a row not about it", [r"in a row not about them", r"control surface"]),
    ("two failed attempts", [r"two failed attempts to fix the same failure"]),
    ("open high or critical finding", [r"high or critical finding", r"after the re-review"]),
]


@pytest.mark.parametrize(("name", "patterns"), REQUIRED_STOPS, ids=[n for n, _ in REQUIRED_STOPS])
def test_stop_condition_present(name: str, patterns: list[str]) -> None:
    assert any(all(re.search(p, item) for p in patterns) for item in STOPS), name


def test_there_are_exactly_ten_stops() -> None:
    assert len(STOPS) == len(REQUIRED_STOPS) == 10


CONTROL_SURFACE = [
    "CLAUDE.md",
    ".claude/**",
    ".github/workflows/**",
    "Makefile",
    "tools/*.proposed*",
    "tools/ship.sh",
    "tools/refused-paths.sh",
    "RUNNER_SYNC_PATHS",
    "evals/baselines/**",
    "evals/src/**",
    "evals/fixtures/**",
    "cassettes/**",
    "**/conftest.py",
    ".gitignore",
    "pyproject.toml",
    "package.json",
]


def control_surface_paragraph() -> str:
    paragraph = next(p for p in SECTION.split("\n\n") if p.startswith("The control surface"))
    return re.sub(r"\s+", " ", paragraph)


@pytest.mark.parametrize("entry", CONTROL_SURFACE)
def test_the_control_surface_paragraph_names_the_entry(entry: str) -> None:
    assert entry in control_surface_paragraph()


def test_stopping_for_a_decision_finishes_independent_work_first() -> None:
    assert "when stopping for a decision, first finish all work that does not depend on it" in FLAT
    assert "stopped-variant note" in FLAT
    assert "what is waiting on the answer" in FLAT


GIT_STRINGS = [
    "Operation not permitted",
    "could not lock config file",
    "unable to unlink",
    "unable to create file",
    "cannot create directory",
]


@pytest.mark.parametrize("text", GIT_STRINGS)
def test_git_error_string_is_in_claude_md(text: str) -> None:
    assert text in CLAUDE


def test_the_git_stop_cites_the_strings_in_how_we_work() -> None:
    assert '"how we work"' in FLAT
    how = CLAUDE.split("## How we work", 1)[1].split("## Running a row", 1)[0]
    assert all(text in how for text in GIT_STRINGS)


CHAIN = [
    "implement",
    "`test-writer`",
    "`security-reviewer`",
    "fix",
    "one scoped re-review",
    "`make ci`",
    "`make eval-gate`",
    "commit",
    "pr body",
    "`advisor-reviewer`",
    "gate report",
]


def test_chain_steps_appear_in_order() -> None:
    paragraph = next(p for p in SECTION.split("\n\n") if p.lstrip().startswith("implement"))
    text = flat(paragraph)
    position = 0
    for step in CHAIN:
        found = text.find(step, position)
        assert found >= 0, f"{step!r} missing or out of order"
        position = found + len(step)


def test_never_push() -> None:
    assert "never push" in FLAT


def test_a_revised_plan_goes_to_the_advisor_before_the_user() -> None:
    assert "any revised plan goes to `advisor-reviewer` before it is shown to the user" in FLAT


def test_ask_first_rules_survive() -> None:
    assert "ask-first rules" in FLAT
    assert "human-reviewed eval label" in FLAT
    assert "budget cap" in FLAT
    # and the original rules themselves are still in the file
    whole = flat(CLAUDE)
    assert "never change a human-reviewed item's labels without asking" in whole
    assert "the proxy's budget cap is never raised without asking" in whole


def test_review_diff_is_the_named_diff_command() -> None:
    assert "one fixed command: `make review-diff`" in FLAT
    assert "`base <sha>` and `head <sha>` as its first two lines" in FLAT


def test_commands_list_has_ship_and_review_diff() -> None:
    block = CLAUDE.split("## Commands", 1)[1].split("```")[1]
    lines = [line for line in block.splitlines() if line.startswith("make ")]
    assert any(line.startswith("make ship PR=<n> SHA=<reviewed>") for line in lines)
    assert any(line.startswith("make review-diff") for line in lines)
