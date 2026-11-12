"""AUTO-1: the advisor-reviewer agent definition and `make review-diff`.

The proposed agent file is read as data. `make review-diff` runs for real in a throwaway repo
that has an `origin/main`; nothing here touches the network.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("AUTO-1")

REPO = Path(__file__).resolve().parents[2]
AGENT = (REPO / "tools" / "advisor-reviewer.proposed.md").read_text()


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "no frontmatter"
    keys: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        keys[key.strip()] = value.strip()
    return keys


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


FRONT = frontmatter(AGENT)
BODY = flat(AGENT)


def test_frontmatter_is_pinned() -> None:
    assert set(FRONT) == {"name", "description", "tools", "model"}
    assert FRONT["name"] == "advisor-reviewer"
    assert FRONT["tools"] == "Read, Grep, Glob"
    assert FRONT["model"] == "opus"
    assert FRONT["description"]


@pytest.mark.parametrize("tool", ["Write", "Edit", "Bash", "NotebookEdit", "MultiEdit"])
def test_no_write_or_run_tool(tool: str) -> None:
    assert tool not in [t.strip() for t in FRONT["tools"].split(",")]


CHECKLIST = [
    "scope matches the row and stays out of the other window's paths",
    "every fix and security test was seen failing first",
    "no test was weakened after a failure",
    "hard rules and protected paths are respected",
    "spend and window are stated before any recording",
    "revised plans contain every requested change",
    "any change to the control surface is flagged for human attention, never passed silently",
]


@pytest.mark.parametrize(("number", "item"), list(enumerate(CHECKLIST, start=1)))
def test_checklist_item_is_present(number: int, item: str) -> None:
    assert f"{number}. {item}" in BODY


CONTROL_SURFACE = [
    ".github/workflows/**",
    ".claude/**",
    "claude.md",
    "tools/*.proposed*",
    "makefile",
    "tools/ship.sh",
    "tools/finish.sh",
    "tools/refused-paths.sh",
    "runner_sync_paths",
    "evals/baselines/**",
    "evals/src/**",
    "evals/fixtures/**",
    "cassettes/**",
    "data/enrichment/**",
    "data/embeddings/**",
    "evals/adversarial/**",  # HARDEN-1
    "**/conftest.py",
    ".gitignore",
    "pyproject.toml",
    "package.json",
]


@pytest.mark.parametrize("path", CONTROL_SURFACE)
def test_control_surface_names_the_path(path: str) -> None:
    assert path in BODY


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize(
    "needle",
    [
        "plan approval on a routine row",
        "the user's start prompt",
        "that the row is routine as `claude.md`",
        "a row that is not routine is a high finding",
        "`pass` approves the plan",
    ],
)
def test_the_advisor_can_approve_a_routine_plan(needle: str) -> None:
    assert needle in BODY


def test_returns_pass_or_findings_with_severity() -> None:
    assert "`pass`, `findings` or `unconfirmed`" in BODY
    for severity in ("critical", "high", "medium", "low"):
        assert severity in BODY


def test_diff_comes_from_review_diff_and_sha_lines_gate_the_review() -> None:
    assert "`make review-diff`" in BODY
    assert "`base <sha>` and `head <sha>`" in BODY
    assert "missing, or do not match" in BODY
    assert "report the whole review as `unconfirmed`" in BODY


def test_the_sha_lines_catch_accidents_only() -> None:
    assert "they catch accidents, not an attacker" in BODY


def test_refuses_to_edit() -> None:
    assert "refuse" in BODY
    assert "read-only" in BODY


# -- make review-diff, for real ----------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


@pytest.fixture
def work(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    (seed / "Makefile").write_text((REPO / "Makefile").read_text())
    (seed / "README").write_text("one\n")
    git(seed, "add", ".")
    git(seed, "commit", "-q", "-m", "first")
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    clone = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    git(clone, "switch", "-q", "-c", "req/AUTO-1-x")
    (clone / "feature.txt").write_text("feature line\n")
    (clone / "README").write_text("two\n")
    git(clone, "add", ".")
    git(clone, "commit", "-q", "-m", "feature")
    return clone


def review_diff(work: Path, out: Path) -> subprocess.CompletedProcess[str]:
    shell_env = {**os.environ, "HOME": str(work.parent), "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(  # noqa: S603
        ["make", "-s", "review-diff", f"REVIEW_DIFF={out}"],  # noqa: S607
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
        env=shell_env,
    )


def test_review_diff_starts_with_the_true_shas_then_the_diff(work: Path, tmp_path: Path) -> None:
    out = tmp_path / "review.diff"
    done = review_diff(work, out)
    assert done.returncode == 0, done.stdout + done.stderr
    lines = out.read_text().splitlines()
    assert lines[0] == f"base {git(work, 'rev-parse', 'origin/main')}"
    assert lines[1] == f"head {git(work, 'rev-parse', 'HEAD')}"
    body = "\n".join(lines[2:])
    assert "diff --git a/feature.txt b/feature.txt" in body
    assert "+feature line" in body
    assert "+two" in body


@pytest.mark.parametrize("dirty", ["modified", "untracked"])
def test_review_diff_refuses_a_dirty_tree(work: Path, tmp_path: Path, dirty: str) -> None:
    if dirty == "modified":
        (work / "README").write_text("three\n")
    else:
        (work / "stray.txt").write_text("x\n")
    out = tmp_path / "review.diff"
    done = review_diff(work, out)
    assert done.returncode != 0
    assert not out.exists()


# -- HARDEN-1 S1: a per-checkout default for REVIEW_DIFF ---------------------------------------

REVIEW_DIFF_PROBE = "\nreviewdiffpath:\n\t@echo $(REVIEW_DIFF)\n"


def review_diff_default(checkout: Path, *overrides: str) -> str:
    """REVIEW_DIFF as the Makefile resolves it in `checkout`, which need not be a git repo."""
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "Makefile").write_text((REPO / "Makefile").read_text() + REVIEW_DIFF_PROBE)
    tmpdir = str(checkout.parent / "tmpdir")
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(checkout), "TMPDIR": tmpdir}
    return subprocess.run(  # noqa: S603
        ["make", "-s", "reviewdiffpath", *overrides],  # noqa: S607
        cwd=checkout,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()


@pytest.mark.req("HARDEN-1")
def test_review_diff_default_differs_per_checkout(tmp_path: Path) -> None:
    """Two windows writing the same default path overwrote each other's review diff."""
    a = review_diff_default(tmp_path / "northwind-shop")
    b = review_diff_default(tmp_path / "northwind-shop-wt-b")
    assert a != b
    for path in (a, b):
        pattern = re.escape(str(tmp_path / "tmpdir")) + r"/northwind-review-\d+\.diff"
        assert re.fullmatch(pattern, path), path


@pytest.mark.req("HARDEN-1")
def test_review_diff_default_is_stable_for_one_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "northwind-shop"
    assert review_diff_default(checkout) == review_diff_default(checkout)


@pytest.mark.req("HARDEN-1")
def test_an_explicit_review_diff_still_wins(tmp_path: Path) -> None:
    out = str(tmp_path / "elsewhere" / "northwind-review-HARDEN-1.diff")
    assert review_diff_default(tmp_path / "x", f"REVIEW_DIFF={out}") == out
