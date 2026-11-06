"""Row 5b: the gate about to run must be the code `use-main-code.sh` put in `evals/src`.

Restoring `evals/src` does not decide what gets imported. `services/assistant/src`,
`services/common/src` and `data/src` come before it on `sys.path`, one `.pth` each, in sorted order.
So the job puts `evals/src` first on `PYTHONPATH`, and this checks two things:

- Where each module would load from, read from the import system. A module's own `__file__` proves
  nothing: the module sets it.
- That each workspace folder on the path is a member's `src`, by its exact place, and holds only
  that member's package.

This runs after `site`, so a `sitecustomize.py` that loads has already run, and can defeat it:
`os._exit(0)` did, from a folder on `PYTHONPATH`. Through a `.pth`, Homebrew's Python loads its own
stdlib `sitecustomize` first; CI's is unmeasured. So the same rule runs from the tree with nothing
from the PR loaded, in `integrity-main.py`, in the job nothing may accept. That job is the control.
This one sees what only a live `sys.path` shows.

Residual (decision 0009): the real `services/rag`, `llm-replay` and `common` still run beside main's
gate.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

MODULES = (
    "northwind_evals",
    "northwind_evals.cli",
    "northwind_evals.gate",
    "northwind_evals.integrity",
)


MEMBER = re.compile(r"(services/[^/]+|data|evals)/src")


def member_problems(root: Path) -> list[str]:
    problems = []
    for entry in sys.path:
        folder = Path(entry or ".").resolve()
        if not folder.is_relative_to(root) or folder.is_relative_to(root / ".venv"):
            continue
        name = folder.relative_to(root).as_posix()
        if not MEMBER.fullmatch(name):
            problems.append(f"{name or '.'} is on sys.path and is not a member's src folder")
            continue
        own = "northwind_" + folder.parent.name.replace("-", "_")
        extra = sorted(p.name for p in folder.iterdir() if p.name not in (own, "__pycache__"))
        problems += [f"{name} holds {item}, which is not {own}" for item in extra]
    return problems


def module_problems(source: Path) -> list[str]:
    problems = []
    for module in MODULES:
        spec = importlib.util.find_spec(module)
        origin = Path(spec.origin or "").resolve() if spec else None
        if origin is None or not origin.is_relative_to(source):
            problems.append(f"{module} loads from {origin}, not from {source}")
    return problems


def main() -> int:
    root = Path.cwd().resolve()
    # Members first: `find_spec` imports the parent package, and that must not run a shadow.
    problems = member_problems(root) or module_problems(root / "evals" / "src")
    for line in problems:
        print(f"assert-main-code: FAILED: {line}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
