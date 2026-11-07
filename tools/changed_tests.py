"""AUTO-2: print which tests `make check` runs for what changed since the merge base with main.

For during a row, and never evidence: only `make ci` is. Prints `all`, or pytest paths and `portal`
separated by spaces, or nothing. What it cannot map runs everything, and so does anything shared,
because a change there can break tests in any service. Run from the repo root.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

ALL = "all"
PORTAL = "portal"

# Changing one of these can move any test.
SHARED = re.compile(
    r"services/(common|llm-replay)/|(.*/)?(conftest\.py|pyproject\.toml)$|uv\.lock$|Makefile$"
    r"|\.github/|cassettes/|infra/|(.*/)?package\.json$|pnpm-"
)
# First match wins. `\1` keeps the service directory.
RULES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"(services/[^/]+)/"), (r"\1",)),
    (re.compile(r"data/enrichment/"), ("data", "services/rag", "evals")),
    (re.compile(r"data/"), ("data",)),
    (re.compile(r"evals/"), ("evals",)),
    (re.compile(r"tests/ship/"), ("tests/ship",)),
    (re.compile(r"tools/.*\.proposed|tools/changed_tests\.py$"), ("tests/hooks",)),
    (re.compile(r"tools/"), ("tests/ship", "tests/hooks")),
    (re.compile(r"CLAUDE\.md$|\.claude/"), ("tests/hooks",)),
    (re.compile(r"PROGRESS\.md$|COVERAGE\.md$|docs/"), ("tests/governance",)),
    (re.compile(r"apps/portal/"), (PORTAL,)),
)
# A test file runs alone, except a helper, which any file beside it can load.
TEST_FILE = re.compile(r"tests/.*/test_[^/]+\.py$")


def targets(paths: Iterable[str]) -> set[str]:
    found: set[str] = set()
    for path in paths:
        if SHARED.match(path):
            return {ALL}
        if TEST_FILE.fullmatch(path) and not path.startswith("services/"):
            found.add(path)
            continue
        rule = next(((match, out) for pat, out in RULES if (match := pat.match(path))), None)
        if rule is None:
            return {ALL}
        match, out = rule
        found.update(match.expand(item) for item in out)
    return found


def plan(found: set[str]) -> str:
    return ALL if ALL in found else " ".join(sorted(found))


def changed_paths(repo: Path) -> list[str] | None:
    """Committed, uncommitted and untracked. None when there is no base to compare with."""

    def git(*args: str) -> list[str] | None:
        done = subprocess.run(  # noqa: S603  (fixed program, arguments built here)
            ["git", *args],  # noqa: S607
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        return done.stdout.splitlines() if done.returncode == 0 else None

    base = git("merge-base", "origin/main", "HEAD")
    if not base:
        return None
    tracked = git("diff", "--name-only", "--no-renames", base[0])
    untracked = git("ls-files", "--others", "--exclude-standard")
    if tracked is None or untracked is None:
        return None
    return sorted({*tracked, *untracked})


def main(repo: Path) -> str:
    paths = changed_paths(repo)
    return ALL if paths is None else plan(targets(paths))


if __name__ == "__main__":
    print(main(Path.cwd()))
