"""What only a comparison with the base branch can see (RAG-12, decision 0003).

A cassette holds the answer the gate scores, and its name only covers the request, so an edited
verdict would replay as if recorded. The same request never needs a new recording, so a modified
cassette is always refused, and so is a symlink under `cassettes/`, added or not, because it would
replay whatever it points at. Deleting one is fine: the next replay misses, which the gate reports.
`overrides` is the record of approvals, so it can only grow. `known_failures.json` waives a
containment failure, so every entry this branch adds is printed for a person to read: its
`approved_by` is a claim, and only a reviewer can tell whether the user made it.
The frozen vectors in `data/embeddings/` decide every saved context's ranking, so they only grow:
an existing key keeps its exact line, at HEAD and on disk (D3 of the RAG-03 drift plan).

Anything missing here fails closed: an unknown base branch is an error, not a pass.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

CASSETTES = "cassettes"
EMBEDDINGS = "data/embeddings"
# The model's recorded follow-up searches (row #13b): recordings, so add-only like a cassette.
REFINEMENTS = "evals/contexts/refinements.ndjson"


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
    """Cassettes at `commit` that differ now, committed or not. T: a file became a link."""
    done = _git("diff", "--name-only", "--no-renames", "--diff-filter=MT", commit, "--", CASSETTES)
    if done.returncode != 0:
        raise RuntimeError(f"git diff failed: {done.stderr.strip()}")
    return done.stdout.split()


def linked_cassettes() -> list[str]:
    """Symlinks under `cassettes/`: at HEAD, in the index, or only on disk."""
    found: set[str] = set()
    for args in (("ls-tree", "-r", "HEAD", "--", CASSETTES), ("ls-files", "-s", "--", CASSETTES)):
        done = _git(*args)
        if done.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {done.stderr.strip()}")
        for line in done.stdout.splitlines():
            meta, _, path = line.partition("\t")
            if meta.startswith("120000 "):
                found.add(path)
    for folder, dirs, files in os.walk(CASSETTES):
        found.update(p for name in dirs + files if os.path.islink(p := os.path.join(folder, name)))
    return sorted(found)


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


def _frozen_lines(text: str, source: str) -> dict[str, str]:
    """Key to line. A duplicate key is refused: a second line could hide a planted vector."""
    lines: dict[str, str] = {}
    for line in text.splitlines():
        try:
            key = str(json.loads(line)["key"])
        except (ValueError, KeyError, TypeError):
            raise RuntimeError(f"{source} has a line that is not a frozen vector") from None
        if key in lines:
            raise RuntimeError(f"{source} holds key {key} twice")
        lines[key] = line
    return lines


def changed_frozen_vectors(commit: str) -> list[str]:
    """Every frozen file at `commit` must still hold each of its keys with the same line, both
    committed at HEAD and on disk. New keys and new files are fine."""
    listed = _git("ls-tree", "-r", "--name-only", commit, "--", EMBEDDINGS)
    if listed.returncode != 0:
        raise RuntimeError(f"git ls-tree failed: {listed.stderr.strip()}")
    problems: list[str] = []
    for path in (p for p in listed.stdout.splitlines() if p.endswith(".jsonl")):
        shown = _git("show", f"{commit}:{path}")
        if shown.returncode != 0:
            raise RuntimeError(f"git show {commit}:{path} failed: {shown.stderr.strip()}")
        before = _frozen_lines(shown.stdout, f"{path} at {commit}")
        committed = _git("show", f"HEAD:{path}")
        disk = Path(path)
        versions = {
            "HEAD": committed.stdout if committed.returncode == 0 else None,
            "disk": disk.read_text(encoding="utf-8") if disk.is_file() else None,
        }
        for where, text in versions.items():
            if text is None:
                problems.append(f"{path}: deleted ({where}); frozen vectors may only be added")
                continue
            now = _frozen_lines(text, f"{path} ({where})")
            removed = [k for k in before if k not in now]
            changed = [k for k in before if k in now and now[k] != before[k]]
            problems += [f"{path}: frozen vector {k} removed ({where})" for k in removed]
            problems += [f"{path}: frozen vector {k} changed ({where})" for k in changed]
    return problems


def added_frozen_vectors(commit: str) -> dict[tuple[str, str], dict[str, list[float]]]:
    """Per frozen file, committed at HEAD and on disk, the entries whose key is not in that file at
    `commit`: the ones nobody has vouched for yet. A whole new file counts in full."""
    listed = _git("ls-tree", "-r", "--name-only", "HEAD", "--", EMBEDDINGS)
    if listed.returncode != 0:
        raise RuntimeError(f"git ls-tree failed: {listed.stderr.strip()}")
    on_disk = {p.as_posix() for p in Path(EMBEDDINGS).glob("*.jsonl")}
    paths = sorted({p for p in listed.stdout.splitlines() if p.endswith(".jsonl")} | on_disk)
    added: dict[tuple[str, str], dict[str, list[float]]] = {}
    for path in paths:
        shown = _git("show", f"{commit}:{path}")
        before = _frozen_lines(shown.stdout, f"{path} at {commit}") if shown.returncode == 0 else {}
        committed = _git("show", f"HEAD:{path}")
        versions = {
            "HEAD": committed.stdout if committed.returncode == 0 else None,
            "disk": Path(path).read_text(encoding="utf-8") if path in on_disk else None,
        }
        for where, text in versions.items():
            if text is None:
                continue
            entries: dict[str, list[float]] = {}
            for key, line in _frozen_lines(text, f"{path} ({where})").items():
                if key not in before:
                    try:
                        entries[key] = [float(x) for x in json.loads(line)["vector"]]
                    except (ValueError, KeyError, TypeError, OverflowError):
                        raise RuntimeError(f"{path} ({where}): key {key} is not a vector") from None
            if entries:
                added[(path, where)] = entries
    return added


def _refinement_lines(text: str, source: str) -> dict[str, str]:
    """`item_id#n` to line. A duplicate is refused: a second line could hide a changed search."""
    lines: dict[str, str] = {}
    for line in text.splitlines():
        try:
            record = json.loads(line)
            key = f"{record['item_id']}#{record['n']}"
        except (ValueError, KeyError, TypeError):
            raise RuntimeError(f"{source} has a line that is not a refinement") from None
        if key in lines:
            raise RuntimeError(f"{source} holds refinement {key} twice")
        lines[key] = line
    return lines


def changed_refinements(commit: str) -> list[str]:
    """Every refinement recorded at `commit` must still be there with the same line, committed at
    HEAD and on disk (the user, row #13b). New entries pass; a base with no file protects
    nothing."""
    shown = _git("show", f"{commit}:{REFINEMENTS}")
    if shown.returncode != 0:
        return []
    before = _refinement_lines(shown.stdout, f"{REFINEMENTS} at {commit}")
    committed = _git("show", f"HEAD:{REFINEMENTS}")
    disk = Path(REFINEMENTS)
    versions = {
        "HEAD": committed.stdout if committed.returncode == 0 else None,
        "disk": disk.read_text(encoding="utf-8") if disk.is_file() else None,
    }
    problems: list[str] = []
    for where, text in versions.items():
        if text is None:
            problems.append(f"{REFINEMENTS}: deleted ({where}); refinements may only be added")
            continue
        now = _refinement_lines(text, f"{REFINEMENTS} ({where})")
        problems += [
            f"{REFINEMENTS}: refinement {k} removed ({where})" for k in before if k not in now
        ]
        problems += [
            f"{REFINEMENTS}: refinement {k} changed ({where})"
            for k in before
            if k in now and now[k] != before[k]
        ]
    return problems


def integrity_problems(base: str, baseline: Path) -> list[str]:
    commit = merge_base(base)
    problems = [f"{path}: an existing cassette was modified" for path in modified_cassettes(commit)]
    problems += [f"{path}: a cassette may not be a symlink" for path in linked_cassettes()]
    problems += changed_frozen_vectors(commit)
    problems += changed_refinements(commit)
    return problems + lost_overrides(commit, baseline)
