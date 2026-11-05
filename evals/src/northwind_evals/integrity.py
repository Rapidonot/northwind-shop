"""What only a comparison with the base branch can see (RAG-12, decision 0003).

A cassette holds the answer the gate scores, and its name only covers the request, so an edited
verdict would replay as if recorded. The same request never needs a new recording, so a modified
cassette is always refused. Deleting one is fine: the next replay misses, which the gate reports.
`overrides` is the record of approvals, so it can only grow. `known_failures.json` waives a
containment failure, so every entry this branch adds is printed for a person to read: its
`approved_by` is a claim, and only a reviewer can tell whether the user made it.

Anything missing here fails closed: an unknown base branch is an error, not a pass.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

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


def _entries(text: str, source: str) -> list[Any]:
    try:
        entries = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{source} is not valid JSON: {error}") from None
    if not isinstance(entries, list):
        raise RuntimeError(f"{source} is not a list")
    return entries


def added_known_failures(commit: str, path: Path) -> list[Any]:
    """Entries not in the file at `commit`, in HEAD or on disk. Both, because they can differ: under
    `denyWrite` a failed switch leaves the old file on disk (decision 0006). Whole entries are
    compared, so a changed `approved_by` counts as added. No file at `commit` means all of them."""
    earlier = _git("show", f"{commit}:{path.as_posix()}")
    before = _entries(earlier.stdout, f"{path} at {commit}") if earlier.returncode == 0 else []
    committed = _git("show", f"HEAD:{path.as_posix()}")
    now = _entries(committed.stdout, f"{path} at HEAD") if committed.returncode == 0 else []
    if path.exists():
        now += [e for e in _entries(path.read_text(encoding="utf-8"), str(path)) if e not in now]
    return [e for e in now if e not in before]


def integrity_problems(base: str, baseline: Path) -> list[str]:
    commit = merge_base(base)
    problems = [f"{path}: an existing cassette was modified" for path in modified_cassettes(commit)]
    return problems + lost_overrides(commit, baseline)
