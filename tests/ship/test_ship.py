"""AUTO-1: `make ship PR=<n> SHA=<reviewed>` and the runner-sync refusals, driven end to end.

A real bare origin and a real clone stand in for GitHub and the runner clone; `gh` is a fake
script driven by scenario files (it pipes canned JSON through the real `jq`, so the script's own
jq expressions run) and `uv` is a no-op. Nothing touches the network or a real `gh`.

The fake `gh` serves the "after" view JSON and the "after" label events once
`gh pr checks --watch` has run, which is how a change in the middle of the wait is simulated
whatever the number of calls before it.

Answers are typed after the prompt appears (ship discards anything typed earlier): `interact`
reads the output and answers each prompt as it shows.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import select
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.req("AUTO-1")

_spec = importlib.util.spec_from_file_location("ship_shared", Path(__file__).with_name("shared.py"))
assert _spec is not None and _spec.loader is not None
shared = importlib.util.module_from_spec(_spec)
sys.modules["ship_shared"] = shared
_spec.loader.exec_module(shared)

REPO: Path = shared.REPO
REFUSED_PATHS: list[str] = shared.REFUSED_PATHS
NOT_REFUSED_PATHS: list[str] = shared.NOT_REFUSED_PATHS
PR = "7"
JOBS = ("python", "eval-gate", "e2e", "portal")
GUARD_JOBS = ("baseline-label", "eval-integrity-main", "eval-gate-main")
ALL_JOBS = (*JOBS, *GUARD_JOBS)
ME = "me"
T1, T2, T3 = "2026-11-05T10:00:00Z", "2026-11-05T11:00:00Z", "2026-11-05T12:00:00Z"

GH_HELP_CHECKS = "Usage: gh pr checks\n  --watch  wait\n  --json fields  output JSON\n  bucket\n"
GH_HELP_MERGE = "Usage: gh pr merge\n  --merge\n  --match-head-commit sha\n"

FAKE_GH = r"""#!/bin/bash
S="$GH_SCENARIO"
printf '%s\n' "$*" >> "$S/argv.log"
args=("$@")
jq_expr=""
json=""
for ((i = 0; i < $#; i++)); do
  case "${args[i]}" in
    --jq) jq_expr="${args[i+1]}" ;;
    --json) json="${args[i+1]}" ;;
  esac
done
serve() {  # canned JSON through the real jq, like gh --jq
  if [[ -n "$jq_expr" ]]; then jq -r "$jq_expr" < "$1"; else cat "$1"; fi
}
if [[ "$1" == "--version" ]]; then
  cat "$S/version"
  exit 0
fi
case " $* " in
  *" --help "*)
    cat "$S/help_$1_$2"
    exit 0 ;;
esac
case "$1 $2" in
  "pr view")
    [[ -e "$S/view_fail" ]] && { echo "gh: view failed" >&2; exit 1; }
    if [[ "$json" == "state" ]]; then serve "$S/final.json"
    elif [[ -e "$S/watched" && -e "$S/view_after.json" ]]; then serve "$S/view_after.json"
    else serve "$S/view.json"; fi
    exit 0 ;;
  "pr checks")
    case " $* " in
      *" --watch "*)
        touch "$S/watched"
        echo "FAKE-WATCH-OUTPUT"
        exit "$(cat "$S/watch_rc")" ;;
    esac
    [[ -e "$S/checks_fail" ]] && { echo "gh: checks failed" >&2; exit 1; }
    serve "$S/checks.json"
    exit 0 ;;
  "pr merge")
    echo "FAKE-MERGE-OUTPUT"
    [[ -e "$S/merge_esc" ]] && printf 'merge says \033[31mred\033[0m\n'
    echo "origin/main at merge: $(git rev-parse origin/main)" >> "$S/merge_state.log"
    rc="$(cat "$S/merge_rc")"
    if [[ "$rc" == 0 ]]; then
      head="$(cat "$S/head")"
      main="$(git -C "$GH_ORIGIN" rev-parse refs/heads/main)"
      tree="$(git -C "$GH_ORIGIN" rev-parse "$head^{tree}")"
      new="$(git -C "$GH_ORIGIN" commit-tree "$tree" -p "$main" -p "$head" -m merge)"
      git -C "$GH_ORIGIN" update-ref refs/heads/main "$new"
    fi
    exit "$rc" ;;
esac
if [[ "$1" == "api" ]]; then
  if [[ "$2" == "user" ]]; then
    [[ -e "$S/user_fail" ]] && { echo "gh: api user failed" >&2; exit 1; }
    serve "$S/user.json"
    exit 0
  fi
  case "$*" in
    *"/events"*)
      [[ -e "$S/events_fail" ]] && { echo "gh: events failed" >&2; exit 1; }
      f="$S/events.json"
      [[ -e "$S/watched" && -e "$S/events_after.json" ]] && f="$S/events_after.json"
      # --paginate with --jq applies the expression to each page on its own
      pages="$(jq length < "$f")"
      for ((p = 0; p < pages; p++)); do
        jq ".[$p]" < "$f" > "$S/page.json"
        serve "$S/page.json"
      done
      exit 0 ;;
  esac
fi
echo "fake gh: unexpected call: $*" >&2
exit 99
"""

PROMPT = re.compile(rb"\[y/N\]|Type the PR number")
# bash `read -p` prints its prompt only on a terminal, so runner-sync's question is never in the
# output; a process that has gone quiet after we typed (or in runner-sync itself) is waiting on it.
IDLE_SECONDS = 1.5


class Ran(subprocess.CompletedProcess[str]):
    """A finished run, and what was typed into it."""

    typed: list[str]


def interact(
    argv: Sequence[str],
    cwd: Path,
    sh_env: Mapping[str, str],
    answers: Sequence[str] | None = None,
    pr: str = PR,
    typeahead: str | None = None,
    idle_prompt: bool = False,
    timeout: float = 120,
) -> Ran:
    """Run `argv`, answering each prompt once it has been printed.

    `idle_prompt` also answers a quiet process (runner-sync's invisible `read -p` question),
    from the start; otherwise only once something has been typed.

    `answers=None` types the PR number at "Type the PR number" and `y` at "[y/N]". With a list,
    answer N goes to prompt N; stdin is closed after the last answer, or at the first prompt
    when the list is empty (EOF). `typeahead` writes at once, then closes stdin. A run that exits
    without a prompt is fine; one that hangs fails.
    """
    proc = subprocess.Popen(  # noqa: S603
        list(argv),
        cwd=cwd,
        env=dict(sh_env),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdin is not None and proc.stdout is not None
    stdin = proc.stdin
    stdin_open = True

    def close_stdin() -> None:
        nonlocal stdin_open
        if stdin_open:
            stdin_open = False
            try:
                stdin.close()
            except BrokenPipeError:
                pass

    def type_text(text: str) -> None:
        try:
            stdin.write(text.encode())
            stdin.flush()
        except BrokenPipeError:
            pass

    if typeahead is not None:
        type_text(typeahead)
        close_stdin()

    typed: list[str] = []

    def answer(prompt: bytes) -> None:
        if not stdin_open:
            return
        if answers is None:
            text: str | None = pr if b"PR number" in prompt else "y"
        elif len(typed) < len(answers):
            text = answers[len(typed)]
        else:
            text = None
        if text is None:
            close_stdin()
            return
        typed.append(text)
        type_text(text + "\n")
        if answers is not None and len(typed) == len(answers):
            close_stdin()

    out = b""
    scanned = 0
    last_output = time.monotonic()
    deadline = last_output + timeout
    while True:
        if time.monotonic() > deadline:
            proc.kill()
            raise AssertionError(f"hung after {timeout}s:\n{out.decode(errors='replace')}")
        ready, _, _ = select.select([proc.stdout], [], [], 0.25)
        if not ready:
            if proc.poll() is not None:
                break
            quiet = time.monotonic() - last_output > IDLE_SECONDS
            if quiet and (idle_prompt or typed) and stdin_open:
                answer(b"[y/N]")
                last_output = time.monotonic()
            continue
        chunk = os.read(proc.stdout.fileno(), 65536)
        if not chunk:
            break
        last_output = time.monotonic()
        out += chunk
        for match in PROMPT.finditer(out, scanned):
            scanned = match.end()
            answer(match.group())
    close_stdin()
    code = proc.wait(timeout=30)
    ran = Ran(list(argv), code, out.decode(errors="replace"), "")
    ran.typed = typed
    return ran


def run(
    cwd: Path, *argv: str, sh_env: Mapping[str, str], stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        list(argv),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=dict(sh_env),
        input=stdin,
        timeout=180,
    )


def passing_checks() -> list[dict[str, str]]:
    return [
        {"name": j, "bucket": "pass", "workflow": "Guard" if j in GUARD_JOBS else "CI"}
        for j in ALL_JOBS
    ]


def ev(
    label: str,
    login: str = ME,
    kind: str = "User",
    at: str = T1,
    event: str = "labeled",
) -> dict[str, Any]:
    return {
        "event": event,
        "created_at": at,
        "label": {"name": label},
        "actor": {"login": login, "type": kind},
    }


def bot(label: str, at: str = T1) -> dict[str, Any]:
    return ev(label, "github-actions[bot]", "Bot", at)


@dataclass
class Rig:
    root: Path
    origin: Path
    clone: Path
    sh_env: dict[str, str]
    base: str
    head: str = ""
    view: dict[str, Any] = field(default_factory=dict)
    view_after: dict[str, Any] | None = None
    checks: list[dict[str, str]] = field(default_factory=list)
    watch_rc: int = 0
    version: str = "gh version 2.103.3 (2026-10-26)\n"
    help_checks: str = GH_HELP_CHECKS
    help_merge: str = GH_HELP_MERGE
    final_state: str = "MERGED"
    merge_rc: int = 0
    user_login: str = ME
    events: list[list[dict[str, Any]]] | None = None
    events_after: list[list[dict[str, Any]]] | None = None
    flags: set[str] = field(default_factory=set)

    @property
    def scn(self) -> Path:
        return self.root / "scn"

    def git(self, *args: str, cwd: Path | None = None, stdin: str = "") -> str:
        return str(shared.git(cwd or self.origin, self.sh_env, *args, stdin=stdin))

    def commit(self, parent: str = "main", **kw: Any) -> str:
        return str(shared.make_commit(self.origin, self.sh_env, parent, **kw))

    def pr(
        self,
        changes: Mapping[str, str | None] | None = None,
        renames: Mapping[str, str] | None = None,
        bulk: int = 0,
        number: str = PR,
        parent: str = "main",
    ) -> str:
        """Create refs/pull/<n>/head in origin from `parent` plus `changes` (None deletes)."""
        if changes is None and renames is None and not bulk:
            changes = {"docs/x.md": "docs changed\n"}
        self.head = self.commit(parent, changes=changes, renames=renames, bulk=bulk)
        self.git("update-ref", f"refs/pull/{number}/head", self.head)
        return self.head

    def advance_main(
        self, changes: Mapping[str, str | None], renames: Mapping[str, str] | None = None
    ) -> str:
        """Move origin's main one commit ahead of the clone (what runner-sync fetches)."""
        new = self.commit("main", changes=changes, renames=renames)
        self.git("update-ref", "refs/heads/main", new)
        return new

    def labels(self, *names: str) -> None:
        self.view["labels"] = [{"name": n} for n in names]

    def write_scenario(self) -> None:
        self.scn.mkdir(exist_ok=True)
        for old in (
            "view_fail",
            "checks_fail",
            "user_fail",
            "events_fail",
            "merge_esc",
            "watched",
            "argv.log",
            "merge_state.log",
            "uv.log",
        ):
            (self.scn / old).unlink(missing_ok=True)
        view = {
            "state": "OPEN",
            "baseRefName": "main",
            "isDraft": False,
            "isCrossRepository": False,
            "headRefOid": self.head,
            "labels": [],
            **self.view,
        }
        (self.scn / "view.json").write_text(json.dumps(view))
        after = self.scn / "view_after.json"
        after.unlink(missing_ok=True)
        if self.view_after is not None:
            after.write_text(json.dumps({**view, **self.view_after}))
        events = self.events
        if events is None:  # every label on the PR was added by the user
            events = [[ev(label["name"]) for label in view["labels"]]]
        (self.scn / "events.json").write_text(json.dumps(events))
        after_events = self.scn / "events_after.json"
        after_events.unlink(missing_ok=True)
        if self.events_after is not None:
            after_events.write_text(json.dumps(self.events_after))
        (self.scn / "user.json").write_text(json.dumps({"login": self.user_login}))
        checks = self.checks or passing_checks()
        (self.scn / "checks.json").write_text(json.dumps(checks))
        (self.scn / "final.json").write_text(json.dumps({"state": self.final_state}))
        (self.scn / "watch_rc").write_text(str(self.watch_rc))
        (self.scn / "merge_rc").write_text(str(self.merge_rc))
        (self.scn / "head").write_text(self.head)
        (self.scn / "version").write_text(self.version)
        (self.scn / "help_pr_checks").write_text(self.help_checks)
        (self.scn / "help_pr_merge").write_text(self.help_merge)
        for flag in ("view_fail", "checks_fail", "user_fail", "events_fail", "merge_esc"):
            if flag in self.flags:
                (self.scn / flag).write_text("")

    def ship(
        self,
        answers: Sequence[str] | None = None,
        pr: str = PR,
        sha: str | None = None,
        runner_dir: Path | None = None,
        typeahead: str | None = None,
    ) -> Ran:
        self.write_scenario()
        return interact(
            [
                "make",
                "-s",
                "ship",
                f"PR={pr}",
                f"SHA={self.head if sha is None else sha}",
                f"RUNNER_DIR={runner_dir or self.clone}",
            ],
            self.clone,
            self.sh_env,
            answers=answers,
            typeahead=typeahead,
        )

    def sync(self, answers: Sequence[str] | None = None) -> Ran:
        self.scn.mkdir(exist_ok=True)
        (self.scn / "uv.log").unlink(missing_ok=True)
        return interact(
            ["make", "-s", "runner-sync", f"RUNNER_DIR={self.clone}"],
            self.clone,
            self.sh_env,
            answers=answers,
            idle_prompt=True,
        )

    # What the fakes saw.
    def gh_calls(self) -> list[str]:
        log = self.scn / "argv.log"
        return log.read_text().splitlines() if log.exists() else []

    def merge_calls(self) -> list[str]:
        return [c for c in self.gh_calls() if c.startswith("pr merge") and "--help" not in c]

    def api_calls(self) -> list[str]:
        return [c for c in self.gh_calls() if c.startswith("api")]

    def uv_calls(self) -> list[str]:
        log = self.scn / "uv.log"
        return log.read_text().splitlines() if log.exists() else []

    def origin_main_in_clone(self) -> str:
        return self.git("rev-parse", "origin/main", cwd=self.clone)

    def clone_head(self) -> str:
        return self.git("rev-parse", "HEAD", cwd=self.clone)


BASE_FILES = {
    "README": "one\n",
    "Makefile": (REPO / "Makefile").read_text(),
    "tools/ship.sh": (REPO / "tools" / "ship.sh").read_text(),
    "tools/refused-paths.sh": (REPO / "tools" / "refused-paths.sh").read_text(),
    ".gitignore": "*.log\nignored-dir/\n",
    ".github/workflows/ci.yml": "name: CI\n",
    ".claude/settings.json": "{}\n",
    "CLAUDE.md": "rules\n",
    "PROGRESS.md": "progress\n",
    "COVERAGE.md": "coverage\n",
    "tools/gate-report.proposed.SKILL.md": "skill\n",
    "evals/baselines/a.json": "{}\n",
    "evals/fixtures/known_failures.json": "[]\n",
    "services/common/x.py": "x = 1\n",
    "docs/x.md": "docs\n",
}


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "scn").mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "uv").write_text('#!/bin/sh\necho "$*" >> "$GH_SCENARIO/uv.log"\n')
    for shim in bin_dir.iterdir():
        shim.chmod(0o755)
    sh_env = {
        **shared.tool_env(tmp_path, bin_dir),
        "GH_SCENARIO": str(tmp_path / "scn"),
        "GH_ORIGIN": str(tmp_path / "origin.git"),
    }
    seed = tmp_path / "seed"
    seed.mkdir()
    for name, text in BASE_FILES.items():
        target = seed / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    for args in (("init", "-q", "-b", "main"), ("add", "."), ("commit", "-q", "-m", "base")):
        run(seed, "git", *args, sh_env=sh_env).check_returncode()
    origin = tmp_path / "origin.git"
    cloned = run(tmp_path, "git", "clone", "-q", "--bare", str(seed), str(origin), sh_env=sh_env)
    cloned.check_returncode()
    clone = tmp_path / "clone"
    run(tmp_path, "git", "clone", "-q", str(origin), str(clone), sh_env=sh_env).check_returncode()
    base = run(clone, "git", "rev-parse", "HEAD", sh_env=sh_env).stdout.strip()
    made = Rig(tmp_path, origin, clone, sh_env, base)
    made.pr()
    return made


# -- assertions -------------------------------------------------------------------------------


def assert_refused(
    rig: Rig, done: subprocess.CompletedProcess[str], reason: str | None = None
) -> None:
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert rig.merge_calls() == [], out
    assert rig.uv_calls() == [], out
    assert rig.origin_main_in_clone() == rig.base
    if reason is not None:
        assert reason.lower() in out.lower(), f"expected {reason!r} in:\n{out}"


def assert_merged(rig: Rig, done: subprocess.CompletedProcess[str]) -> None:
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    assert rig.merge_calls() == [f"pr merge {PR} --merge --match-head-commit {rig.head}"], out
    assert rig.uv_calls(), out


def assert_sync_refused(rig: Rig, done: Ran, reason: str | None = None) -> None:
    """runner-sync refused before asking and before the merge."""
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert done.typed == [], f"asked before refusing:\n{out}"
    assert rig.clone_head() == rig.base, out
    assert rig.origin_main_in_clone() == rig.base, out
    assert rig.uv_calls() == [], out
    if reason is not None:
        assert reason.lower() in out.lower(), f"expected {reason!r} in:\n{out}"


# -- the plain path ---------------------------------------------------------------------------


def test_clean_pr_merges_with_the_exact_argv_then_runner_syncs(rig: Rig) -> None:
    done = rig.ship()
    assert_merged(rig, done)
    assert not any("--admin" in call for call in rig.gh_calls())
    assert rig.origin_main_in_clone() != rig.base  # runner-sync moved it, after the merge


def test_origin_main_in_the_clone_is_unmoved_when_gh_merges(rig: Rig) -> None:
    # Main must have moved on the server, or a fetch that touches origin/main changes nothing here.
    tree = rig.git("rev-parse", "main^{tree}")
    rig.git("update-ref", "refs/heads/main", rig.git("commit-tree", tree, "-p", "main", "-m", "on"))
    rig.ship()
    seen = (rig.scn / "merge_state.log").read_text()
    assert seen.strip() == f"origin/main at merge: {rig.base}"


def test_a_pr_that_changes_no_files_is_refused(rig: Rig) -> None:
    rig.pr(changes={})
    assert_refused(rig, rig.ship(), reason="changes no files")


def test_more_than_one_merge_base_is_refused(rig: Rig) -> None:
    tree = rig.git("rev-parse", "main^{tree}")
    left = rig.git("commit-tree", tree, "-p", "main", "-m", "left")
    right = rig.git("commit-tree", tree, "-p", "main", "-m", "right")
    new_main = rig.git("commit-tree", tree, "-p", left, "-p", right, "-m", "one way")
    rig.head = rig.git("commit-tree", tree, "-p", right, "-p", left, "-m", "the other way")
    rig.git("update-ref", "refs/heads/main", new_main)
    rig.git("update-ref", f"refs/pull/{PR}/head", rig.head)
    assert_refused(rig, rig.ship(), reason="no single merge base")


def test_final_state_is_read_after_the_merge(rig: Rig) -> None:
    rig.ship()
    calls = rig.gh_calls()
    merge = next(i for i, c in enumerate(calls) if c.startswith("pr merge") and "--help" not in c)
    assert any(c.startswith("pr view") and "--json state" in c for c in calls[merge + 1 :])


# -- typed confirmation -----------------------------------------------------------------------


def test_no_surface_change_asks_y_n_and_y_merges(rig: Rig) -> None:
    done = rig.ship(answers=["y", "y"])
    assert_merged(rig, done)
    assert "[y/N]" in done.stdout
    assert "Type the PR number" not in done.stdout


def test_surface_change_asks_for_the_pr_number_and_y_refuses(rig: Rig) -> None:
    rig.pr({"CLAUDE.md": "changed\n"})
    done = rig.ship(answers=["y"])
    assert "Type the PR number" in done.stdout
    assert_refused(rig, done)


def test_surface_change_merges_when_the_pr_number_is_typed(rig: Rig) -> None:
    rig.pr({"CLAUDE.md": "changed\n"})
    done = rig.ship(answers=[PR, "y"])
    assert "Type the PR number" in done.stdout
    assert_merged(rig, done)


@pytest.mark.parametrize("typed", ["8", "70", "07", "", "n", "#7"])
def test_a_wrong_number_refuses(rig: Rig, typed: str) -> None:
    rig.pr({"CLAUDE.md": "changed\n"})
    assert_refused(rig, rig.ship(answers=[typed]))


@pytest.mark.parametrize("answer", ["n", "", "no", "yes please"])
def test_anything_but_y_does_not_merge(rig: Rig, answer: str) -> None:
    assert_refused(rig, rig.ship(answers=[answer]))


def test_eof_at_the_prompt_does_not_merge(rig: Rig) -> None:
    assert_refused(rig, rig.ship(answers=[]))


@pytest.mark.parametrize("surface", [False, True])
def test_typeahead_is_discarded(rig: Rig, surface: bool) -> None:
    if surface:
        rig.pr({"CLAUDE.md": "changed\n"})
    typed = f"{PR}\ny\n" if surface else "y\ny\n"
    assert_refused(rig, rig.ship(typeahead=typed))


def test_runner_sync_asks_separately(rig: Rig) -> None:
    done = rig.ship(answers=["y", "n"])
    assert rig.merge_calls()
    assert done.returncode != 0
    assert rig.uv_calls() == []


# -- baseline-change label (protected paths) ---------------------------------------------------

BASELINE_EDIT: dict[str, str | None] = {"evals/baselines/a.json": '{"v": 2}\n'}
PROTECTED_CHANGES: dict[str, dict[str, str | None]] = {
    "item set": {"evals/fixtures/starter_set.jsonl": "{}\n"},
    "baseline": BASELINE_EDIT,
    "new baseline": {"evals/baselines/new.json": "{}\n"},
    "known failures": {"evals/fixtures/known_failures.json": '["x"]\n'},
    "delete": {"evals/baselines/a.json": None},
}


@pytest.mark.parametrize("name", PROTECTED_CHANGES)
def test_protected_change_without_label_is_refused(rig: Rig, name: str) -> None:
    rig.pr(PROTECTED_CHANGES[name])
    assert_refused(rig, rig.ship(), "baseline-change")


@pytest.mark.parametrize("name", PROTECTED_CHANGES)
def test_protected_change_with_label_merges(rig: Rig, name: str) -> None:
    rig.pr(PROTECTED_CHANGES[name])
    rig.labels("baseline-change")
    assert_merged(rig, rig.ship())


def test_rename_out_of_baselines_is_refused(rig: Rig) -> None:
    rig.pr(renames={"evals/baselines/a.json": "docs/moved.json"})
    assert_refused(rig, rig.ship(), "baseline-change")


def test_rename_into_baselines_is_refused(rig: Rig) -> None:
    rig.pr(renames={"docs/x.md": "evals/baselines/x.md"})
    assert_refused(rig, rig.ship(), "baseline-change")


def test_rename_out_with_label_merges(rig: Rig) -> None:
    rig.pr(renames={"evals/baselines/a.json": "docs/moved.json"})
    rig.labels("baseline-change")
    assert_merged(rig, rig.ship())


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("evals/baselines/caf\u00e9.json", "must not reach the runner clone"),
        ("evals/baselines/with space.json", "baseline-change"),
        ('evals/baselines/q"uote.json', "baseline-change"),
    ],
    ids=["non-ascii", "space", "double-quote"],
)
def test_quoted_path_is_refused(rig: Rig, path: str, reason: str) -> None:
    rig.pr({path: "{}\n"})
    assert_refused(rig, rig.ship(), reason)


@pytest.mark.parametrize("path", ["Evals/Baselines/x", "EVALS/baselines/x", "evals/Baselines/x"])
def test_case_variant_is_refused(rig: Rig, path: str) -> None:
    rig.pr({path: "{}\n"})
    assert_refused(rig, rig.ship())


def test_large_diff_with_the_protected_path_last_is_refused(rig: Rig) -> None:
    rig.pr({"evals/baselines/zzz-last.json": "{}\n"}, bulk=4000)
    assert_refused(rig, rig.ship(), "baseline-change")


def test_label_removed_mid_wait_is_refused(rig: Rig) -> None:
    rig.pr(BASELINE_EDIT)
    rig.labels("baseline-change")
    rig.view_after = {"labels": []}
    assert_refused(rig, rig.ship(), "baseline-change")


@pytest.mark.parametrize(
    "label", ["baseline-change-x", "Baseline-Change", "x,baseline-change", "baseline-changes"]
)
def test_lookalike_labels_are_refused(rig: Rig, label: str) -> None:
    rig.pr(BASELINE_EDIT)
    rig.labels(label)
    assert_refused(rig, rig.ship(), "baseline-change")


# -- ci-change label (A1) ---------------------------------------------------------------------

WORKFLOW_CHANGES: dict[str, dict[str, str | None]] = {
    "ci.yml": {".github/workflows/ci.yml": "name: CI\non: push\n"},
    "case variant": {".GitHub/Workflows/new.yml": "name: x\n"},
    "case variant of the file": {".github/Workflows/NEW.YML": "name: x\n"},
    "new workflow": {".github/workflows/new.yml": "name: new\n"},
}


@pytest.mark.parametrize("name", WORKFLOW_CHANGES)
def test_workflow_change_without_label_is_refused(rig: Rig, name: str) -> None:
    rig.pr(WORKFLOW_CHANGES[name])
    assert_refused(rig, rig.ship(), "ci-change")


@pytest.mark.parametrize("name", WORKFLOW_CHANGES)
def test_workflow_change_with_baseline_label_is_refused(rig: Rig, name: str) -> None:
    rig.pr(WORKFLOW_CHANGES[name])
    rig.labels("baseline-change")
    assert_refused(rig, rig.ship(), "ci-change")


@pytest.mark.parametrize("name", ["ci.yml", "new workflow"])
def test_workflow_change_with_ci_label_merges(rig: Rig, name: str) -> None:
    rig.pr(WORKFLOW_CHANGES[name])
    rig.labels("ci-change")
    assert_merged(rig, rig.ship())


def test_baseline_change_with_ci_label_is_refused(rig: Rig) -> None:
    rig.pr(BASELINE_EDIT)
    rig.labels("ci-change")
    assert_refused(rig, rig.ship(), "baseline-change")


def test_both_changes_need_both_labels(rig: Rig) -> None:
    rig.pr({**BASELINE_EDIT, ".github/workflows/ci.yml": "name: x\n"})
    rig.labels("ci-change")
    assert_refused(rig, rig.ship())
    rig.labels("ci-change", "baseline-change")
    assert_merged(rig, rig.ship())


@pytest.mark.parametrize("label", ["ci-change-x", "CI-Change", "x,ci-change"])
def test_lookalike_ci_labels_are_refused(rig: Rig, label: str) -> None:
    rig.pr(WORKFLOW_CHANGES["ci.yml"])
    rig.labels(label)
    assert_refused(rig, rig.ship(), "ci-change")


# -- finding 1: who added the label -----------------------------------------------------------

LABEL_CASES = [
    pytest.param("baseline-change", BASELINE_EDIT, id="baseline-change"),
    pytest.param("ci-change", WORKFLOW_CHANGES["ci.yml"], id="ci-change"),
]


def refusing_events(label: str) -> dict[str, list[list[dict[str, Any]]]]:
    return {
        "bot": [[bot(label)]],
        "other-user": [[ev(label, "mallory")]],
        "user-then-bot": [[ev(label, at=T1), bot(label, T2)]],
        "bot-listed-first-but-latest": [[bot(label, T2), ev(label, at=T1)]],
        "user-with-type-bot": [[ev(label, ME, "Bot")]],
        "no-event": [[]],
        "only-unlabeled": [[ev(label, event="unlabeled")]],
        "two-pages-bot-last": [[ev(label, at=T1)], [bot(label, T2)]],
        "only-an-unrelated-label": [[ev("foo")]],
    }


def passing_events(label: str) -> dict[str, list[list[dict[str, Any]]]]:
    return {
        "user": [[ev(label)]],
        "bot-then-user": [[bot(label, T1), ev(label, at=T2)]],
        "out-of-order": [[ev(label, at=T2), bot(label, T1)]],
        "two-pages-user-last": [[bot(label, T1)], [ev(label, at=T2)]],
        "bot-added-an-unrelated-label": [[bot("foo", T3), ev(label, at=T1)]],
    }


EVENT_NAMES_REFUSED = list(refusing_events("x"))
EVENT_NAMES_PASSING = list(passing_events("x"))


@pytest.mark.parametrize(("label", "change"), LABEL_CASES)
@pytest.mark.parametrize("name", EVENT_NAMES_REFUSED)
def test_label_not_added_by_the_user_is_refused(
    rig: Rig, label: str, change: dict[str, str | None], name: str
) -> None:
    rig.pr(change)
    rig.labels(label)
    rig.events = refusing_events(label)[name]
    assert_refused(rig, rig.ship(), label)


@pytest.mark.parametrize(("label", "change"), LABEL_CASES)
@pytest.mark.parametrize("name", EVENT_NAMES_PASSING)
def test_label_whose_latest_add_is_the_user_merges(
    rig: Rig, label: str, change: dict[str, str | None], name: str
) -> None:
    rig.pr(change)
    rig.labels(label)
    rig.events = passing_events(label)[name]
    assert_merged(rig, rig.ship())


@pytest.mark.parametrize(("label", "change"), LABEL_CASES)
@pytest.mark.parametrize("flag", ["user_fail", "events_fail"])
def test_a_failing_actor_lookup_is_refused(
    rig: Rig, label: str, change: dict[str, str | None], flag: str
) -> None:
    rig.pr(change)
    rig.labels(label)
    rig.flags.add(flag)
    assert_refused(rig, rig.ship())


def test_an_unusual_login_is_refused_even_when_the_actor_matches_it(rig: Rig) -> None:
    rig.pr({"evals/baselines/a.json": "{1}\n"})
    rig.labels("baseline-change")
    rig.user_login = "not a login"
    rig.events = [[ev("baseline-change", login="not a login")]]
    assert_refused(rig, rig.ship(), reason="cannot read your GitHub login")


@pytest.mark.parametrize(("label", "change"), LABEL_CASES)
def test_an_empty_login_never_matches_an_empty_actor(
    rig: Rig, label: str, change: dict[str, str | None]
) -> None:
    rig.pr(change)
    rig.labels(label)
    rig.user_login = ""
    rig.events = [[ev(label, login="")]]
    assert_refused(rig, rig.ship())


@pytest.mark.parametrize(("label", "change"), LABEL_CASES)
def test_label_re_added_by_a_bot_during_the_wait_is_refused(
    rig: Rig, label: str, change: dict[str, str | None]
) -> None:
    rig.pr(change)
    rig.labels(label)
    rig.events = [[ev(label, at=T1)]]
    rig.events_after = [[ev(label, at=T1), bot(label, T2)]]
    assert_refused(rig, rig.ship(), label)


def test_an_unrelated_label_added_by_a_bot_does_not_matter_without_a_protected_path(
    rig: Rig,
) -> None:
    rig.labels("foo")
    rig.events = [[bot("foo")]]
    done = rig.ship()
    assert_merged(rig, done)
    assert rig.api_calls() == []


def test_the_actor_lookup_reads_the_pr_issue_events(rig: Rig) -> None:
    rig.pr(BASELINE_EDIT)
    rig.labels("baseline-change")
    assert_merged(rig, rig.ship())
    calls = rig.api_calls()
    assert any(c.startswith("api user") for c in calls)
    assert any(f"issues/{PR}/events" in c for c in calls)


# -- A1: the control surface block ------------------------------------------------------------

SURFACE: dict[str, str] = {
    ".github/workflows/ci.yml": "name: CI\non: push\n",
    ".claude/settings.json": '{"a": 1}\n',
    "CLAUDE.md": "changed\n",
    "tools/gate-report.proposed.SKILL.md": "changed\n",
    "Makefile": BASE_FILES["Makefile"] + "\n# changed\n",
    "tools/ship.sh": BASE_FILES["tools/ship.sh"] + "\n# changed\n",
    "tools/refused-paths.sh": BASE_FILES["tools/refused-paths.sh"] + "\n# changed\n",
    "services/common/x.py": "x = 2\n",
    # finding 8: the baselines and the known failures
    "evals/baselines/a.json": '{"v": 2}\n',
    "evals/fixtures/known_failures.json": '["x"]\n',
    # finding 3: what CI executes
    "evals/src/northwind_evals/cli.py": "x = 1\n",
    "evals/fixtures/x.json": "{}\n",
    "cassettes/a/b.json": "{}\n",
    "conftest.py": "x = 1\n",
    "services/x/conftest.py": "x = 1\n",
    ".gitignore": BASE_FILES[".gitignore"] + "unused-pattern-dir/\n",
    "pyproject.toml": "[project]\n",
    "package.json": "{}\n",
    "apps/portal/package.json": "{}\n",
}
NOT_SURFACE = {"tests/test_x.py": "x = 1\n", "docs/x.md": "changed\n"}


def surface_block(out: str) -> str:
    lines = out.splitlines()
    start = next((i for i, line in enumerate(lines) if "control surface" in line.lower()), None)
    assert start is not None, f"no control surface block in:\n{out}"
    return "\n".join(lines[start:])


@pytest.mark.parametrize("path", SURFACE)
def test_control_surface_lists_the_path(rig: Rig, path: str) -> None:
    rig.pr({path: SURFACE[path]})
    rig.labels("ci-change", "baseline-change")
    done = rig.ship(answers=["n"])
    assert path in surface_block(done.stdout + done.stderr)
    assert_refused(rig, done)


@pytest.mark.parametrize("path", NOT_SURFACE)
def test_control_surface_does_not_list(rig: Rig, path: str) -> None:
    rig.pr({**SURFACE, path: NOT_SURFACE[path]})
    rig.labels("ci-change", "baseline-change")
    block = surface_block(rig.ship(answers=["n"]).stdout)
    assert path not in block
    assert "services/common/x.py" in block


def test_control_surface_says_none_when_nothing_matches(rig: Rig) -> None:
    rig.pr(NOT_SURFACE)
    done = rig.ship(answers=["n"])
    block = surface_block(done.stdout + done.stderr)
    assert "(none)" in block
    assert "tests/test_x.py" not in block


def test_control_surface_comes_after_the_checks_and_before_the_merge(rig: Rig) -> None:
    rig.pr({"CLAUDE.md": "changed\n"})
    done = rig.ship()
    out = done.stdout + done.stderr
    assert "FAKE-WATCH-OUTPUT" in out, out
    assert "FAKE-MERGE-OUTPUT" in out, out
    title = out.lower().index("control surface")
    assert out.index("FAKE-WATCH-OUTPUT") < title < out.index("FAKE-MERGE-OUTPUT")


def test_escape_character_in_a_path_prints_as_caret_bracket(rig: Rig) -> None:
    rig.pr({"services/common/x\x1b[31m.py": "x = 2\n"})
    done = rig.ship(answers=["n"])
    out = done.stdout + done.stderr
    assert "\x1b" not in out
    assert "^[" in out


def test_a_newline_in_a_path_cannot_forge_a_none_line(rig: Rig) -> None:
    rig.pr({"tools/a.proposed\n(none)": "x\n"})
    done = rig.ship(answers=["n"])
    out = done.stdout + done.stderr
    assert not any(line.strip() == "(none)" for line in out.splitlines()), out
    assert any("tools/a.proposed" in line for line in out.splitlines()), out


def test_the_full_diff_of_sync_paths_is_shown_escaped_before_the_block(rig: Rig) -> None:
    rig.pr({"services/common/x.py": "x = '\x1b[31m'\nSENTINEL_DIFF_LINE = 1\n"})
    done = rig.ship(answers=["n"])
    out = done.stdout + done.stderr
    assert "\x1b" not in out
    assert "^[" in out
    assert out.index("SENTINEL_DIFF_LINE") < out.lower().index("control surface")


@pytest.mark.parametrize(
    ("path", "label"),
    [
        ("evals/baselines/a.json", "baseline-change"),
        ("evals/fixtures/known_failures.json", "baseline-change"),
        (".github/workflows/ci.yml", "ci-change"),
    ],
)
def test_the_full_diff_of_label_gated_paths_is_shown(rig: Rig, path: str, label: str) -> None:
    # A label says the user accepted a change, not this change.
    rig.pr({path: "SENTINEL_DIFF_LINE\n"})
    rig.labels(label)
    rig.events = [[ev(label)]]
    out = rig.ship(answers=["n"]).stdout
    assert out.index("SENTINEL_DIFF_LINE") < out.lower().index("control surface")


@pytest.mark.parametrize("entry", [" .gitignore ", " ':(glob)**/.gitignore' "])
def test_the_gitignore_that_drives_a_refusal_is_a_sync_path(entry: str) -> None:
    line = next(
        text
        for text in (REPO / "Makefile").read_text().splitlines()
        if text.startswith("RUNNER_SYNC_PATHS :=")
    )
    assert entry in line


def test_sync_paths_match_case_insensitively_in_ship(rig: Rig) -> None:
    rig.pr({"Services/common/new.py": "SENTINEL_DIFF_LINE = 1\n"})
    done = rig.ship(answers=["n"])
    out = done.stdout + done.stderr
    assert "Services/common/new.py" in surface_block(out)
    assert out.index("SENTINEL_DIFF_LINE") < out.lower().index("control surface")


# -- finding 12: merge output ------------------------------------------------------------------


def test_merge_output_with_an_escape_prints_as_caret_bracket(rig: Rig) -> None:
    rig.flags.add("merge_esc")
    done = rig.ship()
    assert_merged(rig, done)
    assert "\x1b" not in done.stdout
    assert "^[" in done.stdout


def test_a_failed_merge_with_an_escape_still_refuses(rig: Rig) -> None:
    rig.flags.add("merge_esc")
    rig.merge_rc = 1
    done = rig.ship()
    assert done.returncode != 0
    assert rig.uv_calls() == []
    assert "\x1b" not in done.stdout


# -- A2: checks fail closed -------------------------------------------------------------------


def checks(**overrides: str) -> list[dict[str, str]]:
    return [{**c, "bucket": overrides.get(c["name"], "pass")} for c in passing_checks()]


@pytest.mark.parametrize("bucket", ["fail", "skipping", "pending", "cancel"])
@pytest.mark.parametrize("job", ALL_JOBS)
def test_a_job_that_did_not_pass_is_refused(rig: Rig, job: str, bucket: str) -> None:
    rig.checks = checks(**{job: bucket})
    assert_refused(rig, rig.ship(), job)


@pytest.mark.parametrize("job", ALL_JOBS)
def test_a_missing_job_is_refused(rig: Rig, job: str) -> None:
    rig.checks = [c for c in checks() if c["name"] != job]
    assert_refused(rig, rig.ship(), job)


def test_no_required_jobs_at_all_is_refused(rig: Rig) -> None:
    rig.checks = [{"name": "lint", "bucket": "pass", "workflow": "CI"}]
    assert_refused(rig, rig.ship())


@pytest.mark.parametrize("job", ALL_JOBS)
def test_same_job_name_from_another_workflow_does_not_count(rig: Rig, job: str) -> None:
    rig.checks = [{**c, "workflow": "Nightly"} if c["name"] == job else c for c in checks()]
    assert_refused(rig, rig.ship(), job)


def test_two_entries_for_one_job_with_one_failing_is_refused(rig: Rig) -> None:
    rig.checks = [*checks(), {"name": "python", "bucket": "fail", "workflow": "CI"}]
    assert_refused(rig, rig.ship(), "python")


@pytest.mark.parametrize("bucket", ["fail", "skipping", "pending", "cancel"])
@pytest.mark.parametrize("workflow", ["CI", "Nightly"])
def test_any_other_check_that_did_not_pass_is_refused(rig: Rig, workflow: str, bucket: str) -> None:
    rig.checks = [*checks(), {"name": "lint-extra", "bucket": bucket, "workflow": workflow}]
    assert_refused(rig, rig.ship(), "lint-extra")


# -- row 5b: Guard, and the one failure a label may accept --------------------------------------

GATE_EDIT = {"evals/src/northwind_evals/cli.py": "changed\n"}


def main_gate_failed() -> list[dict[str, str]]:
    return checks(**{"eval-gate-main": "fail"})


@pytest.mark.req("RAG-12")
def test_required_jobs_are_the_jobs_in_the_workflows() -> None:
    """A renamed job would otherwise read as missing on every PR, or stop being required."""
    ship = (REPO / "tools" / "ship.sh").read_text(encoding="utf-8")
    required = re.search(r'^REQUIRED_JOBS="(.+)"$', ship, re.MULTILINE)
    assert required is not None
    want = {f"CI/{j}" for j in JOBS} | {f"Guard/{j}" for j in GUARD_JOBS}
    assert set(required.group(1).split()) == want
    for name, jobs in (("ci.yml", JOBS), ("guard.yml", GUARD_JOBS)):
        workflow = (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert all(re.search(rf"^  {j}:$", workflow, re.MULTILINE) for j in jobs)


@pytest.mark.req("RAG-12")
def test_a_gate_change_where_main_gate_passes_needs_no_label(rig: Rig) -> None:
    rig.pr(GATE_EDIT)
    assert_merged(rig, rig.ship())


@pytest.mark.req("RAG-12")
def test_main_gate_failing_on_a_gate_change_merges_with_your_label_and_says_so(rig: Rig) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    rig.checks = main_gate_failed()
    done = rig.ship()
    assert_merged(rig, done)
    assert "main's gate did not pass on this PR" in done.stdout
    assert "evals/src/northwind_evals/cli.py" in done.stdout


@pytest.mark.req("RAG-12")
def test_main_gate_failing_without_the_label_is_refused(rig: Rig) -> None:
    rig.pr(GATE_EDIT)
    rig.checks = main_gate_failed()
    assert_refused(rig, rig.ship(), "eval-gate-main")


@pytest.mark.req("RAG-12")
def test_the_label_waives_nothing_when_evals_src_did_not_change(rig: Rig) -> None:
    rig.pr()
    rig.labels("gate-change")
    rig.checks = main_gate_failed()
    assert_refused(rig, rig.ship(), "eval-gate-main")


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("bucket", ["pending", "cancel", "skipping"])
def test_only_a_finished_failure_is_accepted(rig: Rig, bucket: str) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    rig.checks = checks(**{"eval-gate-main": bucket})
    assert_refused(rig, rig.ship(), "eval-gate-main")


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("job", ["eval-integrity-main", "baseline-label", "eval-gate", "python"])
def test_the_label_accepts_no_other_failure(rig: Rig, job: str) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    rig.checks = checks(**{"eval-gate-main": "fail", job: "fail"})
    assert_refused(rig, rig.ship(), job)


@pytest.mark.req("RAG-12")
def test_the_same_name_from_another_workflow_is_not_accepted(rig: Rig) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    extra = {"name": "eval-gate-main", "bucket": "fail", "workflow": "Nightly"}
    rig.checks = [*checks(), extra]
    assert_refused(rig, rig.ship(), "eval-gate-main")


@pytest.mark.req("RAG-12")
def test_a_gate_change_label_added_by_a_bot_is_refused(rig: Rig) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    rig.events = [[bot("gate-change")]]
    rig.checks = main_gate_failed()
    assert_refused(rig, rig.ship(), "gate-change")


@pytest.mark.req("RAG-12")
def test_the_gate_change_label_going_during_the_wait_is_refused(rig: Rig) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    rig.view_after = {"labels": []}
    rig.checks = main_gate_failed()
    assert_refused(rig, rig.ship(), "gate-change")


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("label", ["gate-change-x", "Gate-Change", "baseline-change"])
def test_a_lookalike_label_accepts_nothing(rig: Rig, label: str) -> None:
    rig.pr(GATE_EDIT)
    rig.labels(label)
    rig.checks = main_gate_failed()
    assert_refused(rig, rig.ship(), "eval-gate-main")


@pytest.mark.req("RAG-12")
def test_a_case_variant_of_evals_src_counts_as_a_gate_change(rig: Rig) -> None:
    rig.pr({"Evals/Src/x.py": "changed\n"})
    rig.labels("gate-change")
    rig.checks = main_gate_failed()
    assert_merged(rig, rig.ship())


def test_an_extra_check_that_passed_is_fine(rig: Rig) -> None:
    rig.checks = [*checks(), {"name": "docs", "bucket": "pass", "workflow": "Nightly"}]
    assert_merged(rig, rig.ship())


def test_a_failed_job_is_refused_even_when_watch_exits_zero(rig: Rig) -> None:
    rig.checks = checks(python="fail")
    rig.watch_rc = 0
    assert_refused(rig, rig.ship())


def test_all_passing_proceeds_even_when_watch_exits_one(rig: Rig) -> None:
    rig.watch_rc = 1
    assert_merged(rig, rig.ship())


def test_checks_json_error_is_refused(rig: Rig) -> None:
    rig.flags.add("checks_fail")
    assert_refused(rig, rig.ship())


@pytest.mark.parametrize("version", ["2.103.2", "2.102.9", "2.20.0", "2.9.0", "1.99.0", "0.9.0"])
def test_old_gh_is_refused(rig: Rig, version: str) -> None:
    rig.version = f"gh version {version} (2026-01-01)\n"
    assert_refused(rig, rig.ship(), "2.103.3")


@pytest.mark.parametrize("version", ["2.103.3", "2.104.0", "2.200.1", "3.0.0", "10.0.0"])
def test_new_enough_gh_passes(rig: Rig, version: str) -> None:
    rig.version = f"gh version {version} (2026-12-01)\n"
    assert_merged(rig, rig.ship())


def test_unreadable_gh_version_is_refused(rig: Rig) -> None:
    rig.version = "not a version\n"
    assert_refused(rig, rig.ship())


@pytest.mark.parametrize("missing", ["--json", "bucket"])
def test_checks_help_lacking_a_feature_is_refused(rig: Rig, missing: str) -> None:
    rig.help_checks = GH_HELP_CHECKS.replace(missing, "xxxx")
    assert_refused(rig, rig.ship(), "gh pr checks")


def test_merge_help_lacking_match_head_commit_is_refused(rig: Rig) -> None:
    rig.help_merge = GH_HELP_MERGE.replace("--match-head-commit", "--xxxx")
    assert_refused(rig, rig.ship(), "--match-head-commit")


# -- fails closed on everything else ------------------------------------------------------------


def test_gh_pr_view_error_is_refused(rig: Rig) -> None:
    rig.flags.add("view_fail")
    assert_refused(rig, rig.ship())


def test_git_diff_error_is_refused(rig: Rig) -> None:
    dirs = rig.sh_env["PATH"].split(os.pathsep)
    real = next(str(Path(d) / "git") for d in dirs[1:] if (Path(d) / "git").exists())
    shim = Path(dirs[0]) / "git"
    shim.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do case "$a" in\n'
        '  diff|diff-*) echo "fatal: shim" >&2; exit 128;;\n'
        "esac; done\n"
        f'exec {real} "$@"\n'
    )
    shim.chmod(0o755)
    rig.pr(BASELINE_EDIT)
    rig.labels("baseline-change")
    assert_refused(rig, rig.ship())


@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_pr_not_open_is_refused(rig: Rig, state: str) -> None:
    rig.view["state"] = state
    assert_refused(rig, rig.ship())


def test_draft_is_refused(rig: Rig) -> None:
    rig.view["isDraft"] = True
    assert_refused(rig, rig.ship())


def test_fork_is_refused(rig: Rig) -> None:
    rig.view["isCrossRepository"] = True
    assert_refused(rig, rig.ship())


@pytest.mark.parametrize("base", ["develop", "Main", "release/1"])
def test_base_other_than_main_is_refused(rig: Rig, base: str) -> None:
    rig.view["baseRefName"] = base
    assert_refused(rig, rig.ship())


def test_fetched_head_differing_from_head_ref_oid_is_refused(rig: Rig) -> None:
    rig.view["headRefOid"] = rig.base  # a real commit, but not the PR's head
    assert_refused(rig, rig.ship())


def test_head_moved_mid_wait_is_refused(rig: Rig) -> None:
    rig.view_after = {"headRefOid": rig.base}
    assert_refused(rig, rig.ship())


def test_merge_success_but_final_state_open_does_not_runner_sync(rig: Rig) -> None:
    rig.final_state = "OPEN"
    done = rig.ship()
    assert done.returncode != 0
    assert rig.merge_calls()  # the merge was attempted ...
    assert rig.uv_calls() == []  # ... but runner-sync never ran
    assert "Fast-forward the runner" not in done.stdout + done.stderr


def test_merge_failure_does_not_runner_sync(rig: Rig) -> None:
    rig.merge_rc = 1
    done = rig.ship()
    assert done.returncode != 0
    assert rig.uv_calls() == []


# -- finding 6: the reviewed SHA --------------------------------------------------------------


def test_sha_equal_to_the_head_merges(rig: Rig) -> None:
    assert_merged(rig, rig.ship(sha=rig.head))


def test_sha_that_is_not_an_ancestor_of_the_head_is_refused(rig: Rig) -> None:
    side = rig.commit("main", changes={"docs/side.md": "s\n"})
    rig.git("update-ref", "refs/heads/side", side)
    rig.git("fetch", "-q", "origin", "refs/heads/side:refs/side", cwd=rig.clone)
    assert_refused(rig, rig.ship(sha=side))


def test_a_twin_commit_with_the_same_tree_is_not_the_reviewed_commit(rig: Rig) -> None:
    # The diff from a twin is empty, so only the ancestor check can refuse it.
    twin = rig.git("commit-tree", f"{rig.head}^{{tree}}", "-p", "main", "-m", "twin")
    rig.git("update-ref", "refs/heads/twin", twin)
    rig.git("fetch", "-q", "origin", "refs/heads/twin:refs/twin", cwd=rig.clone)
    assert_refused(rig, rig.ship(sha=twin), reason="not in PR")


def test_sha_unknown_to_the_clone_is_refused(rig: Rig) -> None:
    assert_refused(rig, rig.ship(sha="1" * 40))


def test_a_later_commit_touching_code_is_refused_with_its_path(rig: Rig) -> None:
    reviewed = rig.pr({"docs/y.md": "a\n"})
    rig.pr({"services/x/y.py": "x = 1\n"}, parent=reviewed)
    assert_refused(rig, rig.ship(sha=reviewed), "services/x/y.py")


@pytest.mark.parametrize(
    "later",
    [
        {"PROGRESS.md": "done\n"},
        {"COVERAGE.md": "done\n"},
        {"PROGRESS.md": "done\n", "COVERAGE.md": "done\n"},
    ],
    ids=["progress", "coverage", "both"],
)
def test_a_later_commit_touching_only_the_progress_files_merges(
    rig: Rig, later: dict[str, str]
) -> None:
    reviewed = rig.pr({"docs/y.md": "a\n"})
    rig.pr(later, parent=reviewed)
    assert_merged(rig, rig.ship(sha=reviewed))


def test_a_later_commit_touching_a_doc_is_refused(rig: Rig) -> None:
    reviewed = rig.pr({"docs/y.md": "a\n"})
    rig.pr({"docs/x.md": "later\n"}, parent=reviewed)
    assert_refused(rig, rig.ship(sha=reviewed), "docs/x.md")


def test_a_later_commit_mixing_progress_and_code_is_refused(rig: Rig) -> None:
    reviewed = rig.pr({"docs/y.md": "a\n"})
    rig.pr({"PROGRESS.md": "done\n", "services/x/y.py": "x = 1\n"}, parent=reviewed)
    assert_refused(rig, rig.ship(sha=reviewed), "services/x/y.py")


@pytest.mark.parametrize("kind", ["empty", "short", "uppercase", "non-hex", "too-long"])
def test_make_refuses_a_bad_sha(rig: Rig, kind: str) -> None:
    sha = {
        "empty": "",
        "short": rig.head[:12],
        "uppercase": rig.head.upper(),
        "non-hex": "g" * 40,
        "too-long": rig.head + "0",
    }[kind]
    done = rig.ship(sha=sha)
    assert done.returncode != 0
    assert rig.gh_calls() == []


# -- finding 7: make must not expand the arguments ---------------------------------------------


@pytest.mark.parametrize(
    ("pr", "sha"),
    [
        ("$(shell touch expanded)", None),
        (PR, "$(shell touch expanded)"),
        ("$(touch x)", None),
        (PR, "$(touch x)"),
        ("`touch expanded`", None),
    ],
    ids=["pr-shell", "sha-shell", "pr-var", "sha-var", "pr-backtick"],
)
def test_make_does_not_expand_the_arguments(rig: Rig, pr: str, sha: str | None) -> None:
    done = rig.ship(pr=pr, sha=sha)
    assert done.returncode != 0
    assert not (rig.clone / "expanded").exists()
    assert not (rig.clone / "x").exists()
    assert rig.gh_calls() == []


# -- argument and location checks ---------------------------------------------------------------


def test_refuses_outside_the_clone(rig: Rig, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    done = rig.ship(runner_dir=elsewhere)
    assert done.returncode != 0
    assert rig.gh_calls() == []


@pytest.mark.parametrize("value", ["abc", "1;touch x", "", "7 8", "-1", "$(touch x)"])
def test_refuses_a_bad_pr_value(rig: Rig, value: str) -> None:
    done = rig.ship(pr=value)
    assert done.returncode != 0
    assert rig.gh_calls() == []
    assert not (rig.clone / "x").exists()


# -- finding 13: a spending window -----------------------------------------------------------


@pytest.fixture
def open_window(rig: Rig) -> Iterator[int]:
    """A real listener, standing in for the runner's port."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        port = int(listener.getsockname()[1])
        rig.sh_env["LLM_RUNNER_PORT"] = str(port)
        yield port


def test_ship_refuses_while_a_window_is_open(rig: Rig, open_window: int) -> None:
    done = rig.ship()
    assert_refused(rig, done, "spending window")
    assert rig.gh_calls() == []


def test_runner_sync_refuses_while_a_window_is_open(rig: Rig, open_window: int) -> None:
    rig.advance_main({"docs/x.md": "later\n"})
    done = rig.sync()
    assert_sync_refused(rig, done, "spending window")
    probe = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "--verify", "-q", "refs/runner/incoming"],  # noqa: S607
        cwd=rig.clone,
        capture_output=True,
        env=rig.sh_env,
        check=False,
    )
    assert probe.returncode != 0, "runner-sync fetched before refusing"


# -- refused paths, in ship and in runner-sync ---------------------------------------------------


@pytest.mark.parametrize("path", REFUSED_PATHS)
def test_ship_refuses_the_path(rig: Rig, path: str) -> None:
    rig.pr({path: "x\n"})
    assert_refused(rig, rig.ship(), path)


@pytest.mark.parametrize("path", REFUSED_PATHS)
def test_runner_sync_refuses_the_path_before_asking(rig: Rig, path: str) -> None:
    rig.advance_main({path: "x\n"})
    assert_sync_refused(rig, rig.sync(["y"]), path)


@pytest.mark.parametrize("path", NOT_REFUSED_PATHS)
def test_ship_does_not_refuse_the_path(rig: Rig, path: str) -> None:
    rig.pr({path: BASE_FILES["Makefile"] + "# changed\n" if path == "Makefile" else "x\n"})
    assert_merged(rig, rig.ship())


@pytest.mark.parametrize("path", NOT_REFUSED_PATHS)
def test_runner_sync_does_not_refuse_the_path(rig: Rig, path: str) -> None:
    new = rig.advance_main(
        {path: BASE_FILES["Makefile"] + "# changed\n" if path == "Makefile" else "x\n"}
    )
    done = rig.sync(["y"])
    assert done.returncode == 0, done.stdout
    assert rig.clone_head() == new
    assert rig.uv_calls()


def test_runner_sync_with_a_clean_incoming_commit_still_works(rig: Rig) -> None:
    new = rig.advance_main({"docs/x.md": "later\n"})
    done = rig.sync(["y"])
    assert done.returncode == 0, done.stdout
    assert rig.clone_head() == new
    assert rig.origin_main_in_clone() == new
    assert rig.uv_calls()


def test_runner_sync_refusal_leaves_the_answer_unread(rig: Rig) -> None:
    rig.advance_main({"GNUmakefile": "x\n"})
    done = rig.sync(["y"])
    assert_sync_refused(rig, done)
    assert done.typed == []


IGNORED = ["ignored-dir/x", "a.log", "sub/b.log"]


@pytest.mark.parametrize("path", IGNORED)
def test_ship_refuses_a_path_the_checked_out_gitignore_ignores(rig: Rig, path: str) -> None:
    rig.pr({path: "x\n"})
    assert_refused(rig, rig.ship(), path)


@pytest.mark.parametrize("path", IGNORED)
def test_runner_sync_refuses_an_ignored_path(rig: Rig, path: str) -> None:
    rig.advance_main({path: "x\n"})
    assert_sync_refused(rig, rig.sync(["y"]), path)


ADDS_PATTERN = {
    ".gitignore": BASE_FILES[".gitignore"] + "ignored-by-pr/\n",
    "ignored-by-pr/f.txt": "x\n",
}
DROPS_PATTERN = {".gitignore": "ignored-dir/\n", "z.log": "x\n"}


def test_ship_ignores_a_pattern_the_pr_itself_adds(rig: Rig) -> None:
    rig.pr(ADDS_PATTERN)
    assert_merged(rig, rig.ship())


def test_ship_refuses_a_file_matching_a_pattern_the_pr_removes(rig: Rig) -> None:
    rig.pr(DROPS_PATTERN)
    assert_refused(rig, rig.ship(), "z.log")


def test_runner_sync_ignores_a_pattern_the_incoming_commit_adds(rig: Rig) -> None:
    new = rig.advance_main(ADDS_PATTERN)
    done = rig.sync(["y"])
    assert done.returncode == 0, done.stdout
    assert rig.clone_head() == new


def test_runner_sync_refuses_a_file_matching_a_pattern_the_incoming_commit_removes(
    rig: Rig,
) -> None:
    rig.advance_main(DROPS_PATTERN)
    assert_sync_refused(rig, rig.sync(["y"]), "z.log")


def test_ship_refuses_paths_that_differ_only_by_case(rig: Rig) -> None:
    rig.pr({"readme": "two\n"})
    assert_refused(rig, rig.ship())


def test_runner_sync_refuses_paths_that_differ_only_by_case(rig: Rig) -> None:
    rig.advance_main({"readme": "two\n"})
    assert_sync_refused(rig, rig.sync(["y"]))


def test_runner_sync_shows_the_sync_paths_case_insensitively(rig: Rig) -> None:
    rig.advance_main({"Services/common/new.py": "SENTINEL_DIFF_LINE = 1\n"})
    done = rig.sync(["n"])
    assert "SENTINEL_DIFF_LINE" in done.stdout
    assert rig.clone_head() == rig.base


def test_runner_sync_shows_an_escape_in_a_synced_file_as_caret_bracket(rig: Rig) -> None:
    rig.advance_main({"services/common/x.py": "x = '\x1b[31m'\nSENTINEL_DIFF_LINE = 1\n"})
    done = rig.sync(["n"])
    assert "\x1b" not in done.stdout
    assert "^[" in done.stdout
    assert "SENTINEL_DIFF_LINE" in done.stdout


# -- the script and the Makefile ----------------------------------------------------------------


@pytest.fixture
def sync_paths(tmp_path: Path) -> list[str]:
    extra = tmp_path / "show.mk"
    extra.write_text("show:\n\t@echo $(RUNNER_SYNC_PATHS)\n")
    done = run(
        tmp_path,
        "make",
        "-s",
        "-f",
        str(REPO / "Makefile"),
        "-f",
        str(extra),
        "show",
        sh_env={**os.environ, "HOME": str(tmp_path)},
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


@pytest.mark.parametrize("script", ["tools/ship.sh", "tools/refused-paths.sh"])
def test_script_is_in_runner_sync_paths(sync_paths: list[str], script: str) -> None:
    assert script in sync_paths


@pytest.mark.parametrize("script", ["ship.sh", "refused-paths.sh"])
def test_script_shebang_is_bin_bash(script: str) -> None:
    first = (REPO / "tools" / script).read_text().splitlines()[0]
    assert first == "#!/bin/bash"


# -- row 5b: gaps, trying to make `ship` accept more than the one failure ----------------------


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("name", EVENT_NAMES_REFUSED)
def test_gate_change_added_by_anyone_but_the_user_accepts_nothing(rig: Rig, name: str) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    rig.events = refusing_events("gate-change")[name]
    rig.checks = main_gate_failed()
    assert_refused(rig, rig.ship(), "gate-change")


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("name", EVENT_NAMES_PASSING)
def test_gate_change_last_added_by_the_user_accepts_the_one_failure(rig: Rig, name: str) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    rig.events = passing_events("gate-change")[name]
    rig.checks = main_gate_failed()
    assert_merged(rig, rig.ship())


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("flag", ["user_fail", "events_fail"])
def test_a_failing_actor_lookup_accepts_nothing_for_gate_change(rig: Rig, flag: str) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    rig.flags.add(flag)
    rig.checks = main_gate_failed()
    assert_refused(rig, rig.ship())


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize(
    "path",
    [
        "evals/srcx/a.py",
        "evals/src-notes.md",
        "evals/tests/test_x.py",
        "docs/evals/src/a.py",
        "services/evals/src/a.py",
        "evals/fixtures/other.jsonl",
        "evals/SRC.md",
    ],
)
def test_only_a_change_under_evals_src_counts_as_a_gate_change(rig: Rig, path: str) -> None:
    rig.pr({path: "changed\n"})
    rig.labels("gate-change")
    rig.checks = main_gate_failed()
    assert_refused(rig, rig.ship(), "eval-gate-main")


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"evals/src/northwind_evals/gate.py": None}, id="deleted"),
        pytest.param({"evals/src/a b.py": "x\n"}, id="space in name"),
    ],
)
def test_a_gate_change_of_any_shape_counts_with_the_label(
    rig: Rig, change: dict[str, str | None]
) -> None:
    rig.pr({"evals/src/northwind_evals/gate.py": "x\n"})
    rig.git("update-ref", "refs/heads/main", rig.head)
    rig.base = rig.git("rev-parse", "main")
    rig.git("fetch", "-q", "origin", cwd=rig.clone)
    rig.pr(change, parent="main")
    rig.labels("gate-change")
    rig.checks = main_gate_failed()
    assert_merged(rig, rig.ship())


@pytest.mark.req("RAG-12")
def test_a_rename_out_of_evals_src_counts_as_a_gate_change(rig: Rig) -> None:
    rig.pr({"evals/src/a.py": "x\n"})
    rig.git("update-ref", "refs/heads/main", rig.head)
    rig.base = rig.git("rev-parse", "main")
    rig.git("fetch", "-q", "origin", cwd=rig.clone)
    rig.pr(renames={"evals/src/a.py": "docs/a.py"}, parent="main")
    rig.labels("gate-change")
    rig.checks = main_gate_failed()
    assert_merged(rig, rig.ship())


@pytest.mark.req("RAG-12")
def test_a_missing_main_gate_is_not_a_failure_the_label_accepts(rig: Rig) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    rig.checks = [c for c in checks() if c["name"] != "eval-gate-main"]
    assert_refused(rig, rig.ship(), "eval-gate-main")


@pytest.mark.req("RAG-12")
def test_a_rerun_still_pending_beside_the_failure_is_refused(rig: Rig) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    rerun = {"name": "eval-gate-main", "bucket": "pending", "workflow": "Guard"}
    rig.checks = [*main_gate_failed(), rerun]
    assert_refused(rig, rig.ship(), "eval-gate-main")


@pytest.mark.req("RAG-12")
def test_two_failed_runs_of_the_main_gate_are_still_the_one_failure(rig: Rig) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    again = {"name": "eval-gate-main", "bucket": "fail", "workflow": "Guard"}
    rig.checks = [*main_gate_failed(), again]
    assert_merged(rig, rig.ship())


@pytest.mark.req("RAG-12")
def test_the_gate_change_label_does_not_stand_in_for_baseline_change(rig: Rig) -> None:
    rig.pr({**GATE_EDIT, **BASELINE_EDIT})
    rig.labels("gate-change")
    rig.checks = main_gate_failed()
    assert_refused(rig, rig.ship(), "baseline-change")


@pytest.mark.req("RAG-12")
def test_the_gate_change_label_does_not_stand_in_for_ci_change(rig: Rig) -> None:
    rig.pr({**GATE_EDIT, **WORKFLOW_CHANGES["new workflow"]})
    rig.labels("gate-change")
    rig.checks = main_gate_failed()
    assert_refused(rig, rig.ship(), "ci-change")


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("label", ["gate-change ", " gate-change", "gate‐change", "gate-change\n"])
def test_a_label_with_stray_characters_accepts_nothing(rig: Rig, label: str) -> None:
    rig.pr(GATE_EDIT)
    rig.labels(label)
    rig.checks = main_gate_failed()
    assert_refused(rig, rig.ship())


@pytest.mark.req("RAG-12")
@pytest.mark.parametrize("job", ["eval-integrity-main", "baseline-label"])
@pytest.mark.parametrize("bucket", ["skipping", "pending", "cancel", "fail"])
def test_the_guard_jobs_the_label_cannot_touch_refuse_on_any_state(
    rig: Rig, job: str, bucket: str
) -> None:
    rig.pr(GATE_EDIT)
    rig.labels("gate-change")
    rig.checks = checks(**{job: bucket})
    assert_refused(rig, rig.ship(), job)


@pytest.mark.req("RAG-12")
def test_the_gate_change_message_names_the_label_the_user_added_and_every_changed_path(
    rig: Rig,
) -> None:
    rig.pr({**GATE_EDIT, "evals/src/northwind_evals/other.py": "x\n"})
    rig.labels("gate-change")
    rig.checks = main_gate_failed()
    done = rig.ship()
    assert_merged(rig, done)
    assert "evals/src/northwind_evals/other.py" in done.stdout
