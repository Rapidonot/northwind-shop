"""Row 5b: `eval-integrity` from main's copy, with nothing from the PR loaded.

`cli.py` would pull in `northwind_common`, `northwind_rag` and `northwind_llm_replay`, so PR code
would run beside the one check nothing may accept. `integrity.py` needs only the stdlib. So this
runs on the system `python3 -I`, after `use-main-code.sh`, with main's `evals/src` as the only
addition to the path. It is appended, so nothing there can shadow the stdlib. Only an edit to this
file or the workflow can beat it, which is the stated residual (decision 0009).

It also holds the two rules on what can load beside main's gate, read from the tree with nothing
from the PR running, in the job `ship` never accepts a failure of, so `gate-change` on an expected
red cannot carry a planted package through:

- `member_problems`: each workspace `src/` holds only its own package.
- `workspace_problems`: every `pyproject.toml` keeps the load path where that rule looks. A member's
  `.pth` follows its `module-root`, and `uv sync --locked` does not notice (measured), so a moved
  package would sit in a folder nothing walks.

What stays residual is in decision 0009, clause 11.

The base is the full ref `use-main-code.sh` fetched: a short `origin/main` would lose to a tag of
that name.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

BASE = "refs/remotes/origin/main"
BASELINE = Path("evals/baselines/dense-only.json")
KNOWN_FAILURES = Path("evals/fixtures/known_failures.json")
# The folders whose `src` is a load path: `services/<name>/src`, `data/src`, `evals/src`.
# One plain name: with `exclude`, uv makes `services/.` a member (measured).
MEMBER_DIR = re.compile(r"services/[a-z0-9][a-z0-9-]*|data|evals")
# `uv_build`, by name and version only: a direct URL would replace the backend under its own name.
BUILD_REQUIRES = re.compile(r"uv_build[<>=!~0-9.,\s]*")


def member_problems() -> list[str]:
    problems = []
    for folder in [*sorted(Path("services").glob("*/src")), Path("data/src"), Path("evals/src")]:
        if not folder.is_dir():
            continue
        own = "northwind_" + folder.parent.name.replace("-", "_")
        extra = sorted(p.name for p in folder.iterdir() if p.name not in (own, "__pycache__"))
        problems += [f"{folder} holds {item}, which is not {own}" for item in extra]
    return problems


def read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def source_problems(path: Path, base: str, config: dict[str, Any]) -> list[str]:
    """A `path` source must lead to a real member, whichever file declares it: a wheel in
    `services/` has the right shape as a string, and `uv sync --locked` installs its `.pth`
    (measured). No git or URL source: none is used, and one on `uv_build` would replace the backend.
    """
    problems = []
    for name, entries in config.get("tool", {}).get("uv", {}).get("sources", {}).items():
        for entry in entries if isinstance(entries, list) else [entries]:
            if "git" in entry or "url" in entry:
                problems.append(f"{path}: source {name} is a git or URL source")
            if "path" in entry:
                target = os.path.normpath(os.path.join(base, entry["path"]))
                project = Path(target) / "pyproject.toml"
                if not MEMBER_DIR.fullmatch(target) or not project.is_file():
                    problems.append(f"{path}: source {name} is not a member's folder")
    return problems


def root_problems(path: Path) -> list[str]:
    config = read_toml(path)
    uv = config.get("tool", {}).get("uv", {})
    problems = source_problems(path, "", config)
    if uv.get("package") is not False:
        problems.append(f"{path}: the root must stay `package = false`")
    if "build-system" in config:
        problems.append(f"{path}: the root must have no build system")
    if "exclude" in uv.get("workspace", {}):
        problems.append(f"{path}: a workspace `exclude` can hide a folder from the member glob")
    members = uv.get("workspace", {}).get("members")
    if not isinstance(members, list) or not members:
        return [*problems, f"{path}: no workspace members"]
    for member in members:
        if member != "services/*" and not MEMBER_DIR.fullmatch(str(member)):
            problems.append(f"{path}: member {member} is not `services/<name>`, `data` or `evals`")
    return problems


def member_config_problems(path: Path) -> list[str]:
    config = read_toml(path)
    uv = config.get("tool", {}).get("uv", {})
    build = config.get("build-system", {})
    problems = source_problems(path, path.parent.as_posix(), config)
    if "build-backend" in uv:
        problems.append(f"{path}: `[tool.uv.build-backend]` can move the load path")
    if "workspace" in uv:
        problems.append(f"{path}: a member holds no workspace of its own")
    if build.get("build-backend") != "uv_build" or "backend-path" in build:
        problems.append(f"{path}: the build backend must be `uv_build`")
    requires = build.get("requires")
    if not requires or not all(BUILD_REQUIRES.fullmatch(str(item)) for item in requires):
        problems.append(f"{path}: a build may require `uv_build` only")
    return problems


def checked(step: Any, path: Path) -> list[str]:
    """A file that cannot be read or has the wrong shape proves nothing, so it is a problem."""
    try:
        return list(step(path))
    except (OSError, ValueError, AttributeError, TypeError):
        return [f"{path}: cannot be read as a project file"]


def workspace_problems() -> list[str]:
    """`data` and `evals` are read if present. A folder in `services/` must be a member: skipped, a
    `setup.py` there would go unchecked."""
    problems = checked(root_problems, Path("pyproject.toml"))
    for folder in sorted(p for p in Path("services").glob("*") if p.is_dir()):
        if folder.name == "__pycache__":
            continue
        if not MEMBER_DIR.fullmatch(folder.as_posix()):
            problems.append(f"{folder} is not a plain member name")
        elif not (folder / "pyproject.toml").is_file():
            problems.append(f"{folder} has no pyproject.toml")
        else:
            problems += checked(member_config_problems, folder / "pyproject.toml")
    for folder in (Path("data"), Path("evals")):
        if (folder / "pyproject.toml").exists():
            problems += checked(member_config_problems, folder / "pyproject.toml")
    return problems


def main() -> int:
    members = member_problems() + workspace_problems()
    for line in members:
        print(f"  {line!r}", file=sys.stderr)
    sys.path.append(str(Path("evals/src").resolve()))
    from northwind_evals.integrity import added_known_failures, integrity_problems, merge_base

    try:
        problems = integrity_problems(BASE, BASELINE)
        added = added_known_failures(merge_base(BASE), KNOWN_FAILURES)
    except RuntimeError as error:
        print(f"eval-integrity FAILED: {error}", file=sys.stderr)
        return 1
    for entry in added:  # one JSON line each, so text inside an entry cannot pose as output
        print(f"  known failure added: {json.dumps(entry, sort_keys=True)}")
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    failed = bool(problems or members)
    print(f"eval-integrity {'FAILED' if failed else 'passed'} against {BASE}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
