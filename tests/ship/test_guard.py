"""Row 5b: the Guard workflow. Its scripts run here against real git, and its YAML is pinned.

What only GitHub can show (how `gh` reports a re-run, that the jobs start at all) waits for the
first real PR. These prove what the scripts do with what they are given.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.req("RAG-12")

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / ".github" / "workflows" / "guard"
WORKFLOW = REPO / ".github" / "workflows" / "guard.yml"
EVAL_JOBS = ("eval-gate-main",)
GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    "HOME": "/nonexistent",
    "TMPDIR": os.environ.get("TMPDIR", "/tmp"),  # noqa: S108
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def commit(repo: Path, files: dict[str, str | None], message: str = "x") -> str:
    for name, text in files.items():
        path = repo / name
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    commit(
        path,
        {
            "README.md": "x\n",
            "evals/baselines/dense-only.json": "{}\n",
            "evals/fixtures/known_failures.json": "[]\n",
            "evals/fixtures/starter_set.jsonl": "\n",
        },
    )
    return path


def label_check(
    repo: Path, base: str, head: str, labels: Any = (), raw: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["/bin/bash", str(GUARD / "baseline-label.sh")],
        cwd=repo,
        env={
            **GIT_ENV,
            "BASE_SHA": base,
            "HEAD_SHA": head,
            "PR_LABELS": json.dumps(list(labels)) if raw is None else raw,
        },
        capture_output=True,
        text=True,
        check=False,
    )


PROTECTED = {
    "baseline": {"evals/baselines/dense-only.json": "changed\n"},
    "new baseline": {"evals/baselines/other.json": "{}\n"},
    "deleted baseline": {"evals/baselines/dense-only.json": None},
    "known failures": {"evals/fixtures/known_failures.json": "[1]\n"},
    "item set": {"evals/fixtures/starter_set.jsonl": "{}\n"},
    "case variant": {"Evals/Baselines/x.json": "{}\n"},
    "case variant of the list": {"evals/FIXTURES/Known_Failures.JSON": "[1]\n"},
}


@pytest.mark.parametrize("name", PROTECTED)
def test_a_protected_change_without_the_label_fails(repo: Path, name: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, PROTECTED[name])
    done = label_check(repo, base, head)
    assert done.returncode == 1, done.stdout
    assert "baseline-change" in done.stderr


@pytest.mark.parametrize("name", PROTECTED)
def test_a_protected_change_with_the_label_passes(repo: Path, name: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, PROTECTED[name])
    done = label_check(repo, base, head, ["other", "baseline-change"])
    assert done.returncode == 0, done.stderr


def test_a_rename_out_of_baselines_fails(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "mv", "evals/baselines/dense-only.json", "docs-moved.json")
    git(repo, "commit", "-qm", "move")
    head = git(repo, "rev-parse", "HEAD")
    assert label_check(repo, base, head).returncode == 1


def test_an_unrelated_change_needs_no_label(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"README.md": "changed\n", "evals/fixtures/other.jsonl": "{}\n"})
    assert label_check(repo, base, head).returncode == 0


@pytest.mark.parametrize(
    "label",
    [
        "baseline-change-x",
        "Baseline-Change",
        "baseline-changes",
        'x"baseline-change"y',
        "gate-change",
    ],
)
def test_a_lookalike_label_does_not_count(repo: Path, label: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, PROTECTED["baseline"])
    assert label_check(repo, base, head, [label]).returncode == 1


def test_a_baseline_change_on_main_alone_does_not_count_against_the_pr(repo: Path) -> None:
    """The diff starts at the merge base, so main moving on is not this PR's change."""
    fork = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-qc", "pr")
    head = commit(repo, {"README.md": "pr\n"})
    git(repo, "switch", "-q", "main")
    main = commit(repo, PROTECTED["baseline"])
    assert fork != main
    assert label_check(repo, main, head).returncode == 0


@pytest.mark.parametrize("raw", ["not json", "null", "{"])
def test_labels_that_cannot_be_read_fail(repo: Path, raw: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, PROTECTED["baseline"])
    assert label_check(repo, base, head, raw=raw).returncode != 0


def test_an_unknown_commit_fails_rather_than_passes(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    assert label_check(repo, "0" * 40, head).returncode != 0


@pytest.mark.parametrize("variable", ["BASE_SHA", "HEAD_SHA", "PR_LABELS"])
def test_a_missing_input_fails(repo: Path, variable: str) -> None:
    head = git(repo, "rev-parse", "HEAD")
    env = {**GIT_ENV, "BASE_SHA": head, "HEAD_SHA": head, "PR_LABELS": "[]"}
    del env[variable]
    done = subprocess.run(  # noqa: S603
        ["/bin/bash", str(GUARD / "baseline-label.sh")],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0


# -- use-main-code.sh --------------------------------------------------------------------------

MAIN_CLI = 'BASELINE = "evals/baselines/dense-only.json"\n'
PR_CLI = 'BASELINE = "evals/baselines/easy.json"\n'


@pytest.fixture
def pr_checkout(tmp_path: Path) -> Path:
    """A full clone on a PR branch that repoints the gate, as AUTO-1 finding 3 described."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    commit(
        origin,
        {
            "evals/src/northwind_evals/cli.py": MAIN_CLI,
            "evals/src/northwind_evals/gate.py": "x = 1\n",
        },
    )
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "-q", str(origin), str(checkout))
    git(checkout, "switch", "-qc", "pr")
    commit(
        checkout,
        {
            "evals/src/northwind_evals/cli.py": PR_CLI,
            "evals/src/northwind_evals/added.py": "planted = True\n",
            "evals/src/northwind_evals/gate.py": None,
        },
    )
    return checkout


def use_main(checkout: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["/bin/bash", str(GUARD / "use-main-code.sh")],
        cwd=checkout,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
    )


def cli_text(checkout: Path) -> str:
    return (checkout / "evals/src/northwind_evals/cli.py").read_text()


def test_the_prs_gate_code_is_replaced_by_mains(pr_checkout: Path) -> None:
    assert cli_text(pr_checkout) == PR_CLI
    done = use_main(pr_checkout)
    assert done.returncode == 0, done.stderr
    assert cli_text(pr_checkout) == MAIN_CLI


def test_a_file_the_pr_added_does_not_survive(pr_checkout: Path) -> None:
    assert use_main(pr_checkout).returncode == 0
    assert not (pr_checkout / "evals/src/northwind_evals/added.py").exists()


def test_a_file_the_pr_deleted_comes_back(pr_checkout: Path) -> None:
    assert use_main(pr_checkout).returncode == 0
    assert (pr_checkout / "evals/src/northwind_evals/gate.py").exists()


def test_main_is_fetched_not_taken_from_a_stale_ref(pr_checkout: Path, tmp_path: Path) -> None:
    """Main moved after the checkout: the gate that judges must be main's latest."""
    newer = 'BASELINE = "evals/baselines/newer.json"\n'
    commit(tmp_path / "origin", {"evals/src/northwind_evals/cli.py": newer})
    assert use_main(pr_checkout).returncode == 0
    assert cli_text(pr_checkout) == newer


def test_a_failed_fetch_is_red_and_never_runs_the_prs_copy_as_mains(pr_checkout: Path) -> None:
    """No fallback: the local origin/main still resolves, and this must not use it."""
    git(pr_checkout, "remote", "set-url", "origin", "/nonexistent/origin")
    done = use_main(pr_checkout)
    assert done.returncode != 0
    assert "evals/src is main's copy" not in done.stdout


def test_main_with_no_evals_src_is_red(pr_checkout: Path, tmp_path: Path) -> None:
    """Addition 2: a failed restore. `rm` has already run, and the job must stop there."""
    origin = tmp_path / "origin"
    commit(
        origin,
        {
            "evals/src/northwind_evals/cli.py": None,
            "evals/src/northwind_evals/gate.py": None,
        },
    )
    done = use_main(pr_checkout)
    assert done.returncode != 0
    assert "evals/src is main's copy" not in done.stdout


def test_a_shallow_clone_is_red(tmp_path: Path, pr_checkout: Path) -> None:
    shallow = tmp_path / "shallow"
    origin = tmp_path / "origin"
    git(tmp_path, "clone", "-q", "--depth=1", f"file://{origin}", str(shallow))
    done = use_main(shallow)
    assert done.returncode != 0
    assert "shallow" in done.stderr


def test_outside_a_repository_is_red(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert use_main(empty).returncode != 0


# -- assert-main-code.py -----------------------------------------------------------------------


def package(root: Path) -> None:
    (root / "northwind_evals").mkdir(parents=True)
    for name in ("__init__", "cli", "gate", "integrity"):
        (root / "northwind_evals" / f"{name}.py").write_text("")


def assert_main(cwd: Path, *path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, str(GUARD / "assert-main-code.py")],
        cwd=cwd,
        env={
            "PATH": os.environ["PATH"],
            "PYTHONSAFEPATH": "1",
            "PYTHONPATH": os.pathsep.join(path),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_code_loading_from_evals_src_passes(tmp_path: Path) -> None:
    package(tmp_path / "evals" / "src")
    done = assert_main(tmp_path, str(tmp_path / "evals" / "src"))
    assert done.returncode == 0, done.stderr


def test_a_package_ahead_of_evals_src_is_caught(tmp_path: Path) -> None:
    """A PR adding `northwind_evals/` at the repo root, which sits ahead of `evals/src`."""
    package(tmp_path / "evals" / "src")
    package(tmp_path)
    done = assert_main(tmp_path, str(tmp_path), str(tmp_path / "evals" / "src"))
    assert done.returncode == 1
    assert "not a member's src folder" in done.stderr


def test_one_shadowed_module_is_caught(tmp_path: Path) -> None:
    """A second copy under another `src` can win for the package while gate.py looks right."""
    package(tmp_path / "evals" / "src")
    package(tmp_path / "services" / "src")
    done = assert_main(
        tmp_path, str(tmp_path / "services" / "src"), str(tmp_path / "evals" / "src")
    )
    assert done.returncode == 1


def test_a_gate_that_cannot_be_imported_is_red(tmp_path: Path) -> None:
    (tmp_path / "evals" / "src").mkdir(parents=True)
    assert assert_main(tmp_path, str(tmp_path / "evals" / "src")).returncode != 0


# -- guard.yml ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict[Any, Any]:
    return dict(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def runs(job: dict[str, Any]) -> list[str]:
    return [step["run"] for step in job["steps"] if "run" in step]


def test_it_runs_on_pull_requests_and_label_changes_only(workflow: dict[Any, Any]) -> None:
    """`yaml` reads the key `on` as True. A push trigger would report on the same commit."""
    assert workflow[True] == {
        "pull_request": {
            "types": ["opened", "synchronize", "reopened", "edited", "labeled", "unlabeled"]
        }
    }


def test_the_token_can_only_read(workflow: dict[Any, Any]) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    assert all("permissions" not in job for job in workflow["jobs"].values())


def test_no_job_can_report_skipping(workflow: dict[Any, Any]) -> None:
    """`ship` refuses `skipping`, so a job-level `if` would refuse every PR."""
    assert all("if" not in job for job in workflow["jobs"].values())


def test_nothing_in_ci_can_waive_a_failure(workflow: dict[Any, Any]) -> None:
    """A waiver here would sit in a PR-editable file and read a re-run's stale labels."""
    for job in workflow["jobs"].values():
        assert "continue-on-error" not in job
        for step in job["steps"]:
            assert "continue-on-error" not in step
            assert "if" not in step
        assert not any("|| true" in line or "gate-change" in line for line in runs(job))


def test_every_job_checks_out_with_full_history(workflow: dict[Any, Any]) -> None:
    for job in workflow["jobs"].values():
        checkout = next(
            s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")
        )
        assert checkout["with"]["fetch-depth"] == 0


def test_no_event_text_is_interpolated_into_a_run_line(workflow: dict[Any, Any]) -> None:
    for job in workflow["jobs"].values():
        assert not any("${{" in line for line in runs(job))


@pytest.mark.parametrize("name", EVAL_JOBS)
def test_main_code_comes_first_and_is_asserted_before_anything_runs(
    workflow: dict[Any, Any], name: str
) -> None:
    lines = runs(workflow["jobs"][name])
    main_code = next(i for i, ln in enumerate(lines) if "use-main-code.sh" in ln)
    asserted = next(i for i, ln in enumerate(lines) if "assert-main-code.py" in ln)
    evals = [i for i, ln in enumerate(lines) if "northwind_evals.cli" in ln]
    index = [i for i, ln in enumerate(lines) if "northwind_rag.cli" in ln]
    assert evals
    assert main_code < asserted < min([*evals, *index])


@pytest.mark.parametrize("name", EVAL_JOBS)
def test_uv_cannot_rebuild_over_the_restored_code(workflow: dict[Any, Any], name: str) -> None:
    env = workflow["jobs"][name]["env"]
    assert env["UV_NO_SYNC"] == "1"
    assert env["PYTHONSAFEPATH"] == "1"


def test_the_gate_job_never_spends(workflow: dict[Any, Any]) -> None:
    assert workflow["jobs"]["eval-gate-main"]["env"]["LLM_MODE"] == "replay"


def test_integrity_runs_in_its_own_job_so_nothing_can_accept_it(workflow: dict[Any, Any]) -> None:
    """`ship` accepts only `eval-gate-main`, so integrity must not share that job."""
    assert any("integrity-main.py" in ln for ln in runs(workflow["jobs"]["eval-integrity-main"]))
    assert not any("integrity" in ln for ln in runs(workflow["jobs"]["eval-gate-main"]))


def test_ci_keeps_running_the_prs_own_gate() -> None:
    """How a legitimate gate change gets tested: this job must not be switched to main's copy."""
    ci = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    lines = runs(ci["jobs"]["eval-gate"])
    assert any("northwind_evals.cli gate" in ln for ln in lines)
    assert not any("use-main-code" in ln for ln in lines)


# -- gaps: trying to get past the scripts ------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '"baseline-change"',
        '"x-baseline-change-x"',
        '{"baseline-change": 1}',
        '[["baseline-change"]]',
        '[{"name": "baseline-change"}]',
        '["baseline-change "]',
        '[" baseline-change"]',
        '["baseline\\u2010change"]',
        "true",
        "1",
    ],
)
def test_only_a_list_holding_the_exact_name_counts(repo: Path, raw: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, PROTECTED["baseline"])
    assert label_check(repo, base, head, raw=raw).returncode != 0


def test_a_mode_change_alone_on_the_baseline_needs_the_label(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--chmod=+x", "evals/baselines/dense-only.json")
    git(repo, "commit", "-qm", "chmod")
    head = git(repo, "rev-parse", "HEAD")
    assert label_check(repo, base, head).returncode == 1


def test_a_symlink_put_in_place_of_the_baseline_needs_the_label(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    (repo / "evals/baselines/dense-only.json").unlink()
    (repo / "evals/baselines/dense-only.json").symlink_to("../../README.md")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "link")
    head = git(repo, "rev-parse", "HEAD")
    assert label_check(repo, base, head).returncode == 1


def test_a_newline_in_a_protected_path_still_needs_the_label(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"evals/baselines/a\nb.json": "{}\n", "evals/baselines/é.json": "{}"})
    done = label_check(repo, base, head)
    assert done.returncode == 1
    assert "\n" not in done.stdout.replace("protected path changed: ", "").splitlines()[0][30:]


def test_a_path_that_only_starts_like_a_protected_one_needs_no_label(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(
        repo,
        {
            "evals/baselines-notes.md": "x\n",
            "docs/evals/baselines/x.json": "x\n",
            "evals/fixtures/known_failures.json.md": "x\n",
        },
    )
    assert label_check(repo, base, head).returncode == 0


def test_a_change_that_is_reverted_inside_the_pr_needs_no_label(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, PROTECTED["baseline"])
    head = commit(repo, {"evals/baselines/dense-only.json": "{}\n"})
    assert label_check(repo, base, head).returncode == 0


def test_a_head_with_no_history_in_common_with_the_base_fails(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-q", "--orphan", "unrelated")
    head = commit(repo, {"evals/baselines/dense-only.json": "other\n"})
    assert label_check(repo, base, head).returncode != 0


@pytest.mark.parametrize("hostile", ["--all", "--is-ancestor", "-h", "--fork-point"])
def test_a_base_that_looks_like_an_option_cannot_turn_the_check_off(
    repo: Path, hostile: str
) -> None:
    head = commit(repo, PROTECTED["baseline"])
    assert label_check(repo, hostile, head).returncode != 0


# -- use-main-code.sh: gaps --------------------------------------------------------------------


def test_a_symlink_the_pr_put_at_evals_src_is_replaced_by_a_real_folder(
    pr_checkout: Path, tmp_path: Path
) -> None:
    evil = tmp_path / "evil"
    (evil / "northwind_evals").mkdir(parents=True)
    (evil / "northwind_evals" / "cli.py").write_text("planted = True\n")
    for path in (pr_checkout / "evals" / "src",):
        subprocess.run(["rm", "-rf", str(path)], check=True)  # noqa: S603, S607
        path.symlink_to(evil)
    git(pr_checkout, "add", "-A")
    git(pr_checkout, "commit", "-qm", "link")
    done = use_main(pr_checkout)
    assert done.returncode == 0, done.stderr
    assert not (pr_checkout / "evals" / "src").is_symlink()
    assert cli_text(pr_checkout) == MAIN_CLI
    assert (evil / "northwind_evals" / "cli.py").read_text() == "planted = True\n"
    assert sorted(p.name for p in evil.rglob("*") if p.is_file()) == ["cli.py"]


def test_a_symlinked_file_inside_evals_src_does_not_survive(
    pr_checkout: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("planted = True\n")
    link = pr_checkout / "evals/src/northwind_evals/linked.py"
    link.symlink_to(outside)
    done = use_main(pr_checkout)
    assert done.returncode == 0, done.stderr
    assert not link.is_symlink() and not link.exists()


def test_a_file_the_pr_ignores_in_evals_src_does_not_survive(pr_checkout: Path) -> None:
    """A .gitignore the PR adds must not hide a planted file from the restore."""
    commit(pr_checkout, {".gitignore": "evals/src/**/hidden.py\n"})
    planted = pr_checkout / "evals/src/northwind_evals/hidden.py"
    planted.write_text("planted = True\n")
    assert use_main(pr_checkout).returncode == 0
    assert not planted.exists()


def test_an_uncommitted_edit_to_main_code_is_overwritten(pr_checkout: Path) -> None:
    (pr_checkout / "evals/src/northwind_evals/cli.py").write_text("dirty = True\n")
    assert use_main(pr_checkout).returncode == 0
    assert cli_text(pr_checkout) == MAIN_CLI


@pytest.mark.parametrize("name", ["a b.py", "\u00e9.py"])
def test_a_main_file_with_an_unusual_name_is_restored_not_refused(
    pr_checkout: Path, tmp_path: Path, name: str
) -> None:
    """Main may hold a file with a space or a non-ASCII name: the on-disk comparison must cope."""
    origin = tmp_path / "origin"
    commit(origin, {f"evals/src/northwind_evals/{name}": "x = 2\n"})
    done = use_main(pr_checkout)
    assert done.returncode == 0, done.stderr
    assert (pr_checkout / "evals/src/northwind_evals" / name).read_text() == "x = 2\n"


# -- assert-main-code.py: gaps -----------------------------------------------------------------


def test_a_sibling_folder_that_shares_the_prefix_is_not_evals_src(tmp_path: Path) -> None:
    package(tmp_path / "evals" / "src-evil")
    (tmp_path / "evals" / "src").mkdir(parents=True)
    done = assert_main(tmp_path, str(tmp_path / "evals" / "src-evil"))
    assert done.returncode == 1


def test_a_gate_file_that_is_a_symlink_out_of_evals_src_is_caught(tmp_path: Path) -> None:
    package(tmp_path / "evals" / "src")
    outside = tmp_path / "outside" / "gate.py"
    outside.parent.mkdir()
    outside.write_text("")
    gate = tmp_path / "evals" / "src" / "northwind_evals" / "gate.py"
    gate.unlink()
    gate.symlink_to(outside)
    done = assert_main(tmp_path, str(tmp_path / "evals" / "src"))
    assert done.returncode == 1


def test_a_module_already_imported_from_elsewhere_is_caught(tmp_path: Path) -> None:
    """A `.pth` or sitecustomize that imported the package first would win over evals/src."""
    package(tmp_path / "evals" / "src")
    package(tmp_path / "early")
    (tmp_path / "early" / "sitecustomize.py").write_text("import northwind_evals.gate\n")
    done = assert_main(tmp_path, str(tmp_path / "early"), str(tmp_path / "evals" / "src"))
    assert done.returncode == 1


# -- after the triage (CI F1, F2, F3, F5, F6) ---------------------------------------------------


@pytest.mark.parametrize("name", ["json.py", "os.py", "sys.py"])
def test_a_stdlib_module_planted_in_the_pr_cannot_pass_the_label_check(
    repo: Path, name: str
) -> None:
    """Reproduced: with a root `json.py` and no label, the job printed that the label was there."""
    base = git(repo, "rev-parse", "HEAD")
    planted = "import sys\nsys.exit(0)\n"
    head = commit(repo, {**PROTECTED["baseline"], name: planted})
    assert label_check(repo, base, head).returncode == 1


def test_a_head_with_two_merge_bases_fails(repo: Path) -> None:
    """`ship` refuses this too. One base could be where the protected file looks unchanged."""
    root = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-qc", "a")
    a = commit(repo, {"a.txt": "a\n"})
    git(repo, "switch", "-qc", "b", root)
    b = commit(repo, {"b.txt": "b\n"})
    git(repo, "switch", "-qc", "x", a)
    git(repo, "merge", "-q", "--no-edit", b)
    x = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-qc", "y", b)
    git(repo, "merge", "-q", "--no-edit", a)
    y = git(repo, "rev-parse", "HEAD")
    assert label_check(repo, x, y).returncode != 0


def spoofing(root: Path) -> None:
    """A shadow package that says it is `evals/src`: `__file__` is the module's to set."""
    (root / "northwind_evals").mkdir(parents=True)
    for name in ("__init__", "cli", "gate", "integrity"):
        (root / "northwind_evals" / f"{name}.py").write_text(
            f"import os\n__file__ = os.path.abspath('evals/src/northwind_evals/{name}.py')\n"
        )


def test_a_spoofed_file_attribute_is_caught(tmp_path: Path) -> None:
    """Reproduced: this passed the first version of the assertion, exit 0."""
    package(tmp_path / "evals" / "src")
    spoofing(tmp_path / "services" / "assistant" / "src")
    done = assert_main(
        tmp_path, str(tmp_path / "services/assistant/src"), str(tmp_path / "evals/src")
    )
    assert done.returncode == 1


@pytest.mark.parametrize(
    "planted",
    ["northwind_common/__init__.py", "northwind_rag/__init__.py", "sitecustomize.py"],
)
def test_a_member_holding_anything_but_its_own_package_is_caught(
    tmp_path: Path, planted: str
) -> None:
    """Every `__file__` can be honest while `northwind_common` patches the gate from beside it."""
    package(tmp_path / "evals" / "src")
    member = tmp_path / "services" / "assistant" / "src"
    (member / "northwind_assistant").mkdir(parents=True)
    (member / planted).parent.mkdir(parents=True, exist_ok=True)
    (member / planted).write_text("")
    done = assert_main(tmp_path, str(tmp_path / "evals/src"), str(member))
    assert done.returncode == 1
    assert "services/assistant/src" in done.stderr


def test_members_holding_only_their_own_package_pass(tmp_path: Path) -> None:
    """Including a member added later: the rule comes from the folder's name, not a list."""
    package(tmp_path / "evals" / "src")
    new = tmp_path / "services" / "mcp-catalog" / "src"
    (new / "northwind_mcp_catalog").mkdir(parents=True)
    (new / "__pycache__").mkdir()
    done = assert_main(tmp_path, str(tmp_path / "evals/src"), str(new))
    assert done.returncode == 0, done.stderr


# -- integrity-main.py -------------------------------------------------------------------------


ROOT_TOML = """
[project]
name = "root"
[tool.uv]
package = false
[tool.uv.sources]
northwind-assistant = { workspace = true }
[tool.uv.workspace]
members = ["services/*", "data", "evals"]
"""
MEMBER_TOML = """
[project]
name = "northwind-assistant"
[tool.uv.sources]
northwind-common = { workspace = true }
[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
"""


@pytest.fixture
def integrity_repo(tmp_path: Path) -> Path:
    """Main's real `integrity.py`, a cassette and a baseline, on a PR branch."""
    path = tmp_path / "pr"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    source = (REPO / "evals/src/northwind_evals/integrity.py").read_text(encoding="utf-8")
    commit(
        path,
        {
            "evals/src/northwind_evals/__init__.py": "",
            "evals/src/northwind_evals/integrity.py": source,
            "evals/baselines/dense-only.json": '{"overrides": []}\n',
            "evals/fixtures/known_failures.json": "[]\n",
            "cassettes/suite/a.json": "{}\n",
            "pyproject.toml": ROOT_TOML,
            "services/assistant/pyproject.toml": MEMBER_TOML,
            "services/assistant/src/northwind_assistant/__init__.py": "",
        },
    )
    git(path, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(path, "switch", "-qc", "pr")
    return path


def integrity(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-I", str(GUARD / "integrity-main.py")],
        cwd=cwd,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_clean_pr_passes_integrity(integrity_repo: Path) -> None:
    commit(integrity_repo, {"README.md": "x\n"})
    done = integrity(integrity_repo)
    assert done.returncode == 0, done.stderr
    assert "eval-integrity passed" in done.stdout


def test_an_edited_cassette_fails_integrity(integrity_repo: Path) -> None:
    commit(integrity_repo, {"cassettes/suite/a.json": '{"forged": true}\n'})
    done = integrity(integrity_repo)
    assert done.returncode == 1
    assert "cassettes/suite/a.json" in done.stderr


def test_an_added_known_failure_is_printed(integrity_repo: Path) -> None:
    commit(integrity_repo, {"evals/fixtures/known_failures.json": '[{"item_id": "EV-1"}]\n'})
    assert "known failure added" in integrity(integrity_repo).stdout


def test_nothing_in_the_pr_can_load_into_integrity(integrity_repo: Path) -> None:
    """Run with `-I`, and with no PR packages on the path, so these cannot run."""
    planted = "raise SystemExit(0)\n"
    commit(
        integrity_repo,
        {
            "cassettes/suite/a.json": '{"forged": true}\n',
            "json.py": planted,
            "subprocess.py": planted,
            "northwind_common/__init__.py": planted,
        },
    )
    assert integrity(integrity_repo).returncode == 1


def test_a_missing_base_fails_integrity(integrity_repo: Path) -> None:
    git(integrity_repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert integrity(integrity_repo).returncode == 1


def test_the_integrity_job_runs_no_pr_environment(workflow: dict[Any, Any]) -> None:
    job = workflow["jobs"]["eval-integrity-main"]
    lines = runs(job)
    assert not any("uv " in line for line in lines)
    assert not any("astral-sh" in str(step.get("uses", "")) for step in job["steps"])
    assert "python3 -I .github/workflows/guard/integrity-main.py" in lines


def test_the_gate_job_puts_main_code_first_on_the_path(workflow: dict[Any, Any]) -> None:
    env = workflow["jobs"]["eval-gate-main"]["env"]
    assert env["PYTHONPATH"] == "${{ github.workspace }}/evals/src"


def test_main_code_is_restored_and_asserted_right_before_the_gate(workflow: dict[Any, Any]) -> None:
    """The index build runs PR code, which could rewrite `evals/src` for the steps after it."""
    steps = workflow["jobs"]["eval-gate-main"]["steps"]
    gate = next(i for i, s in enumerate(steps) if "cli gate" in s.get("run", ""))
    assert "use-main-code.sh" in steps[gate - 2]["run"]
    assert "assert-main-code.py" in steps[gate - 1]["run"]
    assert steps[gate]["env"]["EVAL_ALLOW_STALE"] == ""


def test_the_label_job_keeps_no_token_on_disk(workflow: dict[Any, Any]) -> None:
    checkout = workflow["jobs"]["baseline-label"]["steps"][0]
    assert checkout["with"]["persist-credentials"] is False


# -- the second review (N3, N4, N5) -------------------------------------------------------------


@pytest.mark.parametrize(
    "planted",
    [
        "services/assistant/src/sitecustomize.py",
        "services/assistant/src/usercustomize.py",
        "services/assistant/src/northwind_common/__init__.py",
        "services/gateway/src/northwind_evals/cli.py",
        "data/src/sitecustomize.py",
        "evals/src/sitecustomize.py",
    ],
)
def test_integrity_refuses_a_member_holding_anything_else(
    integrity_repo: Path, planted: str
) -> None:
    """Reproduced: `os._exit(0)` in a `sitecustomize.py` beat the assertion, which it runs before.
    Here nothing from the PR runs, and `ship` accepts no failure of this job, so `gate-change` on an
    expected red cannot carry a planted shadow through."""
    commit(
        integrity_repo,
        {
            "services/assistant/src/northwind_assistant/__init__.py": "",
            planted: "import os\nos._exit(0)\n",
        },
    )
    done = integrity(integrity_repo)
    assert done.returncode == 1
    assert planted.split("/src/")[0] in done.stderr


def test_integrity_passes_members_holding_only_their_own_package(integrity_repo: Path) -> None:
    commit(
        integrity_repo,
        {
            "services/assistant/src/northwind_assistant/__init__.py": "",
            "services/mcp-catalog/pyproject.toml": MEMBER_TOML,
            "services/mcp-catalog/src/northwind_mcp_catalog/__init__.py": "",
            "data/src/northwind_data/__init__.py": "",
        },
    )
    done = integrity(integrity_repo)
    assert done.returncode == 0, done.stderr


def test_a_src_folder_in_the_wrong_place_is_caught(tmp_path: Path) -> None:
    """The name alone would pass: its parent is `common`, and it holds `northwind_common`."""
    package(tmp_path / "evals" / "src")
    vendor = tmp_path / "vendor" / "common" / "src"
    (vendor / "northwind_common").mkdir(parents=True)
    done = assert_main(tmp_path, str(tmp_path / "evals/src"), str(vendor))
    assert done.returncode == 1
    assert "vendor/common/src" in done.stderr


# -- F1: what `pyproject.toml` can do to the load path -------------------------------------------

ASSISTANT = "services/assistant/pyproject.toml"
BAD_MEMBERS = {
    "module root": MEMBER_TOML + '[tool.uv.build-backend]\nmodule-root = "lib"\n',
    "any build table": MEMBER_TOML + '[tool.uv.build-backend]\nmodule-name = "other"\n',
    "another backend": MEMBER_TOML.replace(
        'build-backend = "uv_build"', 'build-backend = "setuptools.build_meta"'
    ),
    "a local backend": MEMBER_TOML.replace(
        'build-backend = "uv_build"', 'build-backend = "uv_build"\nbackend-path = ["."]'
    ),
    "a replaced uv_build": MEMBER_TOML.replace(
        '"uv_build>=0.12,<0.13"', '"uv_build @ https://example.invalid/evil.whl"'
    ),
    "an extra requirement": MEMBER_TOML.replace(
        '"uv_build>=0.12,<0.13"', '"uv_build>=0.12,<0.13", "evil"'
    ),
    "no build system": '[project]\nname = "northwind-assistant"\n',
    "a path out of the tree": MEMBER_TOML.replace(
        "northwind-common = { workspace = true }", 'northwind-common = { path = "../../vendor/x" }'
    ),
    "an absolute path": MEMBER_TOML.replace(
        "northwind-common = { workspace = true }", 'northwind-common = { path = "/opt/x" }'
    ),
    "a path in a list": MEMBER_TOML.replace(
        "northwind-common = { workspace = true }",
        'northwind-common = [{ path = "../../vendor/x", marker = "os_name == \'posix\'" }]',
    ),
    "a nested workspace": MEMBER_TOML + '[tool.uv.workspace]\nmembers = ["lib/*"]\n',
    "an in-tree wheel": MEMBER_TOML.replace(
        "northwind-common = { workspace = true }",
        'northwind-common = { path = "../evil-0.0.0-py3-none-any.whl" }',
    ),
    "a member shape with nothing there": MEMBER_TOML.replace(
        "northwind-common = { workspace = true }", 'northwind-common = { path = "../nothing" }'
    ),
    "a git source": MEMBER_TOML.replace(
        "northwind-common = { workspace = true }",
        'northwind-common = { git = "https://example.invalid/x" }',
    ),
    "a url source": MEMBER_TOML.replace(
        "northwind-common = { workspace = true }",
        'northwind-common = { url = "https://example.invalid/x.whl" }',
    ),
    "a replaced uv_build by source": MEMBER_TOML.replace(
        "northwind-common = { workspace = true }",
        'uv_build = { url = "https://example.invalid/evil.whl" }',
    ),
    "not toml": "this is = = not toml",
}


@pytest.mark.parametrize("name", BAD_MEMBERS)
def test_integrity_refuses_a_member_config_that_moves_the_load_path(
    integrity_repo: Path, name: str
) -> None:
    commit(integrity_repo, {ASSISTANT: BAD_MEMBERS[name]})
    done = integrity(integrity_repo)
    assert done.returncode == 1, done.stdout
    assert ASSISTANT in done.stderr


BAD_ROOTS = {
    "a member elsewhere": ROOT_TOML.replace('"evals"]', '"evals", "vendor/*"]'),
    "a deep glob": ROOT_TOML.replace('"services/*"', '"services/*/inner"'),
    "a member above the tree": ROOT_TOML.replace('"data"', '"../data"'),
    "a path source": ROOT_TOML.replace(
        "northwind-assistant = { workspace = true }",
        'northwind-assistant = { path = "vendor/assistant" }',
    ),
    "a dot member": ROOT_TOML.replace('"evals"]', '"evals", "services/."]'),
    "a dot dot member": ROOT_TOML.replace('"evals"]', '"evals", "services/.."]'),
    "an exclude": ROOT_TOML.replace('"evals"]', '"evals"]\nexclude = ["services/src"]'),
    "a package root": ROOT_TOML.replace("package = false", "package = true"),
    "no package line": ROOT_TOML.replace("package = false\n", ""),
    "a root build system": ROOT_TOML + '[build-system]\nbuild-backend = "uv_build"\n',
    "no workspace": '[project]\nname = "root"\n[tool.uv]\npackage = false\n',
    "not toml": "this is = = not toml",
}


@pytest.mark.parametrize("name", BAD_ROOTS)
def test_integrity_refuses_a_root_config_that_moves_the_load_path(
    integrity_repo: Path, name: str
) -> None:
    commit(integrity_repo, {"pyproject.toml": BAD_ROOTS[name]})
    done = integrity(integrity_repo)
    assert done.returncode == 1, done.stdout
    assert "pyproject.toml" in done.stderr


def test_integrity_refuses_a_missing_root_config(integrity_repo: Path) -> None:
    commit(integrity_repo, {"pyproject.toml": None})
    assert integrity(integrity_repo).returncode == 1


def test_the_measured_module_root_move_is_refused(integrity_repo: Path) -> None:
    """Measured: `uv sync --locked` passes this, and the `.pth` points at `lib`, a folder the member
    rule does not walk. The package has really moved, so `src` no longer exists to be checked."""
    commit(
        integrity_repo,
        {
            ASSISTANT: BAD_MEMBERS["module root"],
            "services/assistant/src/northwind_assistant/__init__.py": None,
            "services/assistant/lib/northwind_assistant/__init__.py": "",
            "services/assistant/lib/northwind_common/__init__.py": "",
        },
    )
    assert integrity(integrity_repo).returncode == 1


@pytest.mark.parametrize(
    "good",
    [
        MEMBER_TOML.replace(
            "northwind-common = { workspace = true }", 'northwind-common = { path = "../common" }'
        ),
        MEMBER_TOML.replace(
            "northwind-common = { workspace = true }", 'northwind-common = { path = "../../data" }'
        ),
    ],
    ids=["a sibling service", "data"],
)
def test_a_path_source_to_a_real_member_shape_passes(integrity_repo: Path, good: str) -> None:
    common = "services/common/pyproject.toml"
    commit(
        integrity_repo, {ASSISTANT: good, common: MEMBER_TOML, "data/pyproject.toml": MEMBER_TOML}
    )
    done = integrity(integrity_repo)
    assert done.returncode == 0, done.stderr


def test_the_repo_as_it_stands_passes_the_workspace_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every real `pyproject.toml`: a rule that refused these would fail every PR."""
    spec = importlib.util.spec_from_file_location("integrity_main", GUARD / "integrity-main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(REPO)
    assert module.workspace_problems() == []
    assert module.member_problems() == []


def test_the_measured_dot_member_is_refused(integrity_repo: Path) -> None:
    """Measured: with `exclude`, `uv sync --locked` makes `services/.` a member, and its `.pth`
    points at `services/src`, which neither rule walks."""
    root = BAD_ROOTS["a dot member"].replace(
        '"services/."]', '"services/."]\nexclude = ["services/src"]'
    )
    commit(
        integrity_repo,
        {
            "pyproject.toml": root,
            "services/pyproject.toml": MEMBER_TOML,
            "services/src/evil_pkg/__init__.py": "",
        },
    )
    assert integrity(integrity_repo).returncode == 1


def test_the_measured_in_tree_wheel_is_refused(integrity_repo: Path) -> None:
    """Measured: `uv sync --locked` installed the `.pth` from a wheel a path source named."""
    commit(
        integrity_repo,
        {
            ASSISTANT: BAD_MEMBERS["an in-tree wheel"],
            "services/evil-0.0.0-py3-none-any.whl": "a wheel\n",
        },
    )
    done = integrity(integrity_repo)
    assert done.returncode == 1
    assert "source northwind-common" in done.stderr


@pytest.mark.parametrize(
    "planted",
    ["services/plain/setup.py", "services/Odd_Name/pyproject.toml"],
    ids=["no toml", "odd"],
)
def test_a_folder_in_services_that_is_not_a_member_is_refused(
    integrity_repo: Path, planted: str
) -> None:
    """Skipped, a `setup.py` there would go unchecked, where the root file is required."""
    commit(integrity_repo, {planted: "x = 1\n"})
    done = integrity(integrity_repo)
    assert done.returncode == 1
    assert planted.split("/")[1] in done.stderr
