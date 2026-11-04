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
    # Not `make record*`, which also matched `make recording` and `make record -f evil.mk`.
    "excludedCommands": ["make record SUITE=*", "make eval-live", "make llm-proxy"],
    "filesystem": {
        "denyWrite": ["./.claude/settings*", "./.claude/hooks", "./.env*"],
        "denyRead": [
            "./.env*",  # this checkout
            "~/Projects/northwind-shop/.env*",  # the main checkout, whose key file worktrees use
            "~/Library/Keychains",  # the GitHub token gh and git use
        ],
        "allowWrite": [
            "~/.northwind/ledger.lock",  # not the ledger: that would let Bash erase the spend
            "~/.cache/uv",
            "~/Library/pnpm/store",
            "/private/tmp/pnpm-store-operation-locks-502",  # pnpm's shared lock; 502 is the uid
        ],
    },
    # Strict mode ignores project allowedDomains, so a list here would only mislead.
    "network": {"allowLocalBinding": True},
}

USER_FILE = {
    "sandbox": {
        "network": {
            "strictAllowlist": True,
            # No Anthropic host: only the excluded targets reach it.
            "allowedDomains": [
                "pypi.org",
                "files.pythonhosted.org",
                "registry.npmjs.org",
                "github.com",
                "api.github.com",
                "*.githubusercontent.com",
            ],
        }
    }
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
    ],
    "allow": [
        "Bash(make *)",
        "Bash(uv run *)",
        "Bash(uv sync*)",
        "Bash(corepack pnpm *)",
        "Bash(docker compose *)",
    ],
    # Excluded targets stay behind these, so the prompt is the gate.
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


def test_proposal_has_no_other_top_level_settings() -> None:
    assert set(load("settings.proposed.json")) == {"$schema", "permissions", "hooks", "sandbox"}
