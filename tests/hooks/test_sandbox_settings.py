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
