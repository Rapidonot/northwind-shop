"""OPS-1: this row changes none of the files RECORD-ALL froze (the user's list, 2026-11-27).

RECORD-ALL is running in window A with these frozen: the assistant, the agent runtime and agents,
the tools (the MCP servers and the common tool modules), llm.py, tool_scopes.py, chat.py, the
orchestrator's planner, the inventory's substitutes and the eval answerers. The staff console only
reads them. The diff is taken from the merge base with origin/main, committed and uncommitted work
and new files included.

It needs origin/main: in a shallow clone (GitHub CI's unit job checks out one commit) it skips and
says why; `make ci` and `make check` run it with origin/main present.
"""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("OPS-1")

REPO = Path(__file__).resolve().parents[3]
FROZEN = (
    "services/assistant/*",
    "services/mcp-*",
    "services/backend/src/northwind_backend/agents/*",
    "services/backend/src/northwind_backend/orchestrator/plan.py",
    "services/backend/src/northwind_backend/inventory/substitutes.py",
    "services/common/src/northwind_common/llm.py",
    "services/common/src/northwind_common/tool_scopes.py",
    "services/common/src/northwind_common/catalog_tool.py",
    "services/common/src/northwind_common/order_tool.py",
    "services/common/src/northwind_common/policy_tool.py",
    "services/gateway/src/northwind_gateway/chat.py",
    "evals/src/northwind_evals/answerers.py",
)


def _git(*args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise FileNotFoundError("git")
    # Fixed arguments from this file only, never input.
    return subprocess.run(  # noqa: S603
        [git, *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def frozen(path: str) -> bool:
    return any(
        fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, f"{pattern}/*")
        for pattern in FROZEN
    )


def test_the_freeze_list_names_real_files() -> None:
    for pattern in FROZEN:
        assert list(REPO.glob(pattern)), pattern


@pytest.mark.parametrize(
    ("path", "is_frozen"),
    [
        ("services/assistant/src/northwind_assistant/turn.py", True),
        ("services/mcp-orders/src/northwind_mcp_orders/book.py", True),
        ("services/backend/src/northwind_backend/agents/runtime.py", True),
        ("services/backend/src/northwind_backend/orchestrator/plan.py", True),
        ("services/backend/src/northwind_backend/orchestrator/run.py", False),
        ("services/gateway/src/northwind_gateway/chat.py", True),
        ("services/gateway/src/northwind_gateway/handoff.py", False),
        ("evals/src/northwind_evals/answerers.py", True),
    ],
)
def test_what_counts_as_frozen(path: str, is_frozen: bool) -> None:
    assert frozen(path) is is_frozen


def test_this_branch_changes_no_frozen_file() -> None:
    try:
        base = _git("merge-base", "origin/main", "HEAD").strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        pytest.skip(f"needs origin/main for the merge base (a shallow clone has none): {error}")
    changed = set(_git("diff", "--name-only", base).split()) | set(
        _git("ls-files", "--others", "--exclude-standard").split()
    )
    touched = sorted(path for path in changed if frozen(path))
    assert touched == [], f"OPS-1 must not change these RECORD-ALL files: {touched}"
