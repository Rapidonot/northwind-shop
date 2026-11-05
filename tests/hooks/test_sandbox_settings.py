"""CC-27: pins the proposed sandbox settings exactly.

These tests read tools/settings.proposed.json and tools/settings.user.proposed.json, which the
user installs by hand. Blocks are compared whole, because a new key can weaken them as easily as a
changed one: `allowRead` beats `denyRead`, for example. The proof that the OS enforces them is in
docs/reviews/2026-11-04-cc-27-sandbox-evidence.md.
"""

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.req("CC-27")

TOOLS = Path(__file__).resolve().parents[2] / "tools"

SANDBOX = {
    "enabled": True,
    "failIfUnavailable": True,  # false would run unsandboxed with only a warning
    "allowUnsandboxedCommands": False,  # false makes dangerouslyDisableSandbox ignored
    "autoAllowBashIfSandboxed": False,  # keeps today's prompts
    # No excludedCommands: nothing runs outside the sandbox. Spending goes through the runner,
    # which the user starts from a clean clone (decision 0005).
    "filesystem": {
        "denyWrite": [
            "./.claude/settings*",
            "./.claude/hooks",
            "./.env*",
            "~/.northwind",  # the runner, its caches, the ledger and its lock are all budget state
            "~/.northwind/secrets",  # stated on its own, so narrowing the line above cannot lose it
            # What the gate trusts. This is the control for Bash; the `ask` rules are not (0006).
            "./evals/baselines",
            "./evals/fixtures/known_failures.json",
            # What redirects or configures git for a linked worktree: your terminal's git trusts
            # these. Absolute, because `./.git` is the main checkout's directory, which a commit
            # needs. HEAD, index, refs and logs stay writable for the same reason (0006).
            "~/Projects/northwind-shop-*/.git",
            "~/Projects/northwind-shop/.git/worktrees/*/gitdir",
            "~/Projects/northwind-shop/.git/worktrees/*/commondir",
            "~/Projects/northwind-shop/.git/worktrees/*/config.worktree",
        ],
        "denyRead": [
            "./.env*",  # this checkout
            "~/Projects/northwind-shop/.env*",  # the main checkout, whose key file worktrees use
            "~/Library/Keychains",  # the GitHub token gh and git use
            "~/.northwind/secrets",  # the API key, which only the runner reads
            "~/.northwind/ledger.lock",  # flock works on a read-only descriptor, and would hang it
        ],
        "allowWrite": [
            "~/.cache/uv",
            "~/Library/pnpm/store",
            "/private/tmp/pnpm-store-operation-locks-502",  # pnpm's shared lock; 502 is the uid
        ],
    },
    # Strict mode ignores project allowedDomains, so a list here would only mislead.
    "network": {"allowLocalBinding": True},
}

USER_FILE = {
    # In user settings too: project settings are per checkout, and an older branch keeps the old
    # ones. `make runner-check`, run in each checkout, proves these merge with a project's lists.
    "permissions": {
        "deny": [
            "Read(~/.northwind/secrets)",
            "Read(~/.northwind/secrets/**)",
            "Edit(~/.northwind/**)",
            "Write(~/.northwind/**)",
        ]
    },
    "sandbox": {
        "filesystem": {
            "denyRead": ["~/.northwind/secrets", "~/.northwind/ledger.lock"],
            "denyWrite": ["~/.northwind"],
        },
        "network": {
            "strictAllowlist": True,
            # No Anthropic host: only the runner reaches it, from outside the sandbox.
            "allowedDomains": [
                "pypi.org",
                "files.pythonhosted.org",
                "registry.npmjs.org",
                "github.com",
                "api.github.com",
                "*.githubusercontent.com",
            ],
        },
    },
}

PERMISSIONS = {
    "deny": [
        "Read(./.env)",
        "Read(./.env.*)",
        "Read(~/Projects/northwind-shop/.env*)",
        "Edit(./.env)",
        "Edit(./.env.*)",
        "Write(./.env)",
        "Write(./.env.*)",
        # The sandbox covers Bash only; these close R3's Write tool case.
        "Edit(./.claude/settings*)",
        "Write(./.claude/settings*)",
        "Edit(./.claude/hooks/**)",
        "Write(./.claude/hooks/**)",
        # The sandbox covers Bash only, so the file tools need their own rules for these.
        "Read(~/.northwind/secrets)",
        "Read(~/.northwind/secrets/**)",
        "Edit(~/.northwind/secrets/**)",
        "Write(~/.northwind/secrets/**)",
        "Edit(~/.northwind/**)",
        "Write(~/.northwind/**)",
    ],
    "allow": [
        "Bash(make *)",
        "Bash(uv run *)",
        "Bash(uv sync*)",
        "Bash(corepack pnpm *)",
        "Bash(docker compose *)",
    ],
    # A courtesy, not a gate: `Bash(make *)` and `Bash(uv run *)` can reach the same code without a
    # prompt. The window the user opens is the gate (decision 0005).
    "ask": [
        "Bash(git push*)",
        "Bash(make record*)",
        "Bash(make eval-live*)",
        "Bash(make llm-proxy*)",
        # Wide on purpose: rig run 2 showed narrower forms miss `make "eval-promote"`,
        # `make .DEFAULT_GOAL=eval-promote`, `uv run make …` and `uv run python <file> promote`.
        "Bash(make *eval-promote*)",
        "Bash(make *eval-gate-stale-ok*)",
        "Bash(uv run *eval-promote*)",
        "Bash(uv run *eval-gate-stale-ok*)",
        "Bash(uv run *northwind_evals*promote*)",
        # The sandbox covers Bash only, so the file tools need their own rules for these.
        "Edit(./evals/baselines/**)",
        "Write(./evals/baselines/**)",
        "Edit(./evals/fixtures/known_failures.json)",
        "Write(./evals/fixtures/known_failures.json)",
    ],
}


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((TOOLS / name).read_text())
    return data


def test_sandbox_block_is_exactly_the_reviewed_one() -> None:
    assert load("settings.proposed.json")["sandbox"] == SANDBOX


def test_permissions_are_exactly_the_reviewed_ones() -> None:
    assert load("settings.proposed.json")["permissions"] == PERMISSIONS


def test_user_file_is_exactly_the_reviewed_one() -> None:
    assert load("settings.user.proposed.json") == USER_FILE


def test_nothing_is_excluded_from_the_sandbox() -> None:
    assert "excludedCommands" not in load("settings.proposed.json")["sandbox"]


def test_the_sandbox_network_list_has_no_anthropic_host() -> None:
    hosts = load("settings.user.proposed.json")["sandbox"]["network"]["allowedDomains"]
    assert not [host for host in hosts if "anthropic" in host or "claude" in host]


def test_the_sandbox_cannot_write_any_budget_state() -> None:
    filesystem = load("settings.proposed.json")["sandbox"]["filesystem"]
    assert "~/.northwind" in filesystem["denyWrite"]
    assert not [path for path in filesystem["allowWrite"] if ".northwind" in path]


def test_proposal_has_no_other_top_level_settings() -> None:
    assert set(load("settings.proposed.json")) == {"$schema", "permissions", "hooks", "sandbox"}


@pytest.mark.req("RAG-12")
@pytest.mark.req("RAG-12")
def test_only_what_redirects_git_is_denied_so_a_commit_still_works() -> None:
    """Row 5a: your terminal's git trusts these four. Everything else in `.git` stays writable, and
    a relative `./.git` would be the main checkout's directory, which would stop its commits."""
    deny_write = load("settings.proposed.json")["sandbox"]["filesystem"]["denyWrite"]
    git_entries = [path for path in deny_write if ".git" in path and ".gitignore" not in path]
    assert git_entries == [
        "~/Projects/northwind-shop-*/.git",
        "~/Projects/northwind-shop/.git/worktrees/*/gitdir",
        "~/Projects/northwind-shop/.git/worktrees/*/commondir",
        "~/Projects/northwind-shop/.git/worktrees/*/config.worktree",
    ]


@pytest.mark.req("RAG-12")
def test_the_gate_inputs_are_denied_to_bash_and_asked_for_in_the_edit_tools() -> None:
    """RAG-12 5a: the baseline and the known-failure list are what the gate trusts, so the sandbox
    cannot write them, and the user is asked before the tools, `make` or `uv run` change them."""
    settings = load("settings.proposed.json")
    paths = ["./evals/baselines", "./evals/fixtures/known_failures.json"]
    deny_write = settings["sandbox"]["filesystem"]["denyWrite"]
    assert all(path in deny_write for path in paths)
    ask = settings["permissions"]["ask"]
    for target in ("eval-promote", "eval-gate-stale-ok"):
        assert f"Bash(make *{target}*)" in ask
        assert f"Bash(uv run *{target}*)" in ask
        # The narrower forms are covered by these, and rig run 2 showed them missing commands.
        assert f"Bash(make {target}*)" not in ask
        assert f"Bash(make * {target}*)" not in ask
    assert "Bash(uv run *northwind_evals*promote*)" in ask
    assert "Bash(uv run * northwind_evals.cli promote*)" not in ask
    for tool in ("Edit", "Write"):
        assert f"{tool}(./evals/baselines/**)" in ask
        assert f"{tool}(./evals/fixtures/known_failures.json)" in ask
