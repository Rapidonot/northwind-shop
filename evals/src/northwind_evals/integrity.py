"""What only a comparison with the base branch can see (RAG-12, decision 0003).

A cassette holds the answer the gate scores, and its name only covers the request, so an edited
verdict would replay as if recorded. The same request never needs a new recording, so a modified
cassette is always refused. Deleting one is fine: the next replay misses, which the gate reports.
`overrides` is the record of approvals, so it can only grow.

Anything missing here fails closed: an unknown base branch is an error, not a pass.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

CASSETTES = "cassettes"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  (fixed program, arguments built here)
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )


def merge_base(base: str) -> str:
    done = _git("merge-base", base, "HEAD")
    if done.returncode != 0:
        raise RuntimeError(f"cannot compare with {base}: fetch it first")
    return done.stdout.strip()


def modified_cassettes(commit: str) -> list[str]:
    """Cassettes that exist at `commit` and differ now, committed or not."""
    done = _git("diff", "--name-only", "--no-renames", "--diff-filter=M", commit, "--", CASSETTES)
    if done.returncode != 0:
        raise RuntimeError(f"git diff failed: {done.stderr.strip()}")
    return done.stdout.split()


def lost_overrides(commit: str, baseline: Path) -> list[str]:
    """Problems if the baseline no longer starts with every override recorded at `commit`."""
    earlier = _git("show", f"{commit}:{baseline.as_posix()}")
    if earlier.returncode != 0:
        return []  # the base branch has no baseline yet
    before = json.loads(earlier.stdout).get("overrides", [])
    if not baseline.exists():
        return [f"{baseline} is gone"]
    now = json.loads(baseline.read_text(encoding="utf-8")).get("overrides", [])
    return [] if now[: len(before)] == before else ["recorded overrides were removed or changed"]


def integrity_problems(base: str, baseline: Path) -> list[str]:
    commit = merge_base(base)
    problems = [f"{path}: an existing cassette was modified" for path in modified_cassettes(commit)]
    return problems + lost_overrides(commit, baseline)
