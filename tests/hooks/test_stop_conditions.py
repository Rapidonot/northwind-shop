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
    "tools/finish.sh",
    "tools/refused-paths.sh",
    "RUNNER_SYNC_PATHS",
    "evals/baselines/**",
    "evals/src/**",
    "evals/fixtures/**",
    "cassettes/**",
    "data/enrichment/**",
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
    "commit",
    "pr body",
    "`make ci`",
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


def chain_paragraph() -> str:
    return flat(next(p for p in SECTION.split("\n\n") if p.lstrip().startswith("implement")))


@pytest.mark.req("AUTO-2")
def test_the_chain_does_not_run_the_gate_a_second_time() -> None:
    assert "make eval-gate" not in chain_paragraph()
    # `make ci` must still hold the gate, or dropping it above would drop the gate.
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text()
    ci = re.search(r"^ci: (.*?)##", makefile, re.M)
    assert ci and "eval-gate" in ci.group(1).split()


@pytest.mark.req("AUTO-2")
def test_make_ci_runs_once_at_the_head_the_advisor_reads() -> None:
    text = chain_paragraph()
    assert text.count("`make ci`") == 1
    assert text.index("commit") < text.index("`make ci`") < text.index("`advisor-reviewer`")


@pytest.mark.req("AUTO-2")
def test_a_change_after_make_ci_means_another_run() -> None:
    assert "any change after `make ci`" in FLAT
    assert "a new commit and another `make ci`" in FLAT


@pytest.mark.req("AUTO-2")
def test_make_check_is_for_during_a_row_and_is_never_evidence() -> None:
    assert "`make check`" in FLAT
    assert "never evidence" in FLAT


ROUTINE = re.search(r"\*\*Routine rows\.\*\*.*?(?=\n\n\S*The control surface|\Z)", SECTION, re.S)


def routine() -> str:
    assert ROUTINE, "no routine rows text"
    return flat(ROUTINE.group(0))


@pytest.mark.req("AUTO-2")
def test_stop_one_is_kept_with_only_the_routine_exception() -> None:
    assert STOPS[0] == "1. plan approval, except on a routine row (below)."


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize(
    "needle",
    [
        "the user names its scope in the start prompt and says it is routine",
        "no hard-rule 1 or 9 path",
        "no control surface path",
        "files added under `cassettes/<suite>/` by `make record suite=<suite>`",
        "a window the user opened for that suite",
        "names the suite, the model input that changed and the expected spend",
    ],
)
def test_the_routine_definition(needle: str) -> None:
    assert needle in routine()


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize(
    "needle",
    [
        "a modified, deleted or hand-written cassette",
        "any file added under `cassettes/eval-answers/`",
        "`make eval-live`",
        "`make enrich`",
    ],
)
def test_what_makes_a_row_not_routine(needle: str) -> None:
    text = routine()
    assert needle in text
    assert text.index(needle) < text.index("make the row not routine")


@pytest.mark.req("AUTO-2")
def test_the_cassette_rule_is_stated_as_a_rule_not_a_proof() -> None:
    assert "a rule the session keeps" in routine()
    assert "the gate report lists every added cassette" in routine()


@pytest.mark.req("AUTO-2")
def test_stop_one_holds_until_the_advisor_can_check_a_routine_row() -> None:
    assert (
        "until the `advisor-reviewer` proposal is installed, stop 1 applies to every row"
        in routine()
    )


@pytest.mark.req("AUTO-2")
def test_the_routine_rule_names_its_hard_rule_paths() -> None:
    for path in ("`services/backend/**`", "cards.py", "chat.py"):
        assert path in routine()


@pytest.mark.req("AUTO-2")
def test_an_unplanned_cassette_miss_is_a_scope_question() -> None:
    assert "an unplanned cassette miss is a scope question (stop 7)" in routine()
    assert "never an automatic re-record" in routine()


@pytest.mark.req("AUTO-2")
def test_only_the_added_cassette_exception_narrows_stop_eight() -> None:
    stop = next(item for item in STOPS if "in a row not about them" in item)
    assert "routine row's added cassettes" in stop


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize(
    "needle",
    [
        "`pass`: carry on",
        "`findings`, medium or low only: triage, then carry on",
        "`findings` with a high or critical, or `unconfirmed`: stop",
    ],
)
def test_what_each_advisor_answer_does(needle: str) -> None:
    assert needle in routine()


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize(
    "needle",
    [
        "critical or high is stop 10",
        "unsafe at any severity",
        "stop 6 if the fix changes the design",
        "medium: fix it if it is inside the row's files, otherwise log it with an owner",
        "low: log it in `progress.md`",
    ],
)
def test_the_triage_rule_is_fixed(needle: str) -> None:
    assert needle in routine().lower()


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
    assert any(line.startswith("make finish ") for line in lines)
    assert any(line.startswith("make check ") for line in lines)
    assert any(line.startswith("make review-diff") for line in lines)
