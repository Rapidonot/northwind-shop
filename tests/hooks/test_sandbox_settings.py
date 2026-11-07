"""CC-27: pins the proposed sandbox settings exactly.

These tests read tools/settings.proposed.json and tools/settings.user.proposed.json, which the
user installs by hand. Blocks are compared whole, because a new key can weaken them as easily as a
changed one: `allowRead` beats `denyRead`, for example. The proof that the OS enforces them is in
docs/reviews/2026-11-04-cc-27-sandbox-evidence.md.
"""

import json
import re
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
            # A session must not edit its own reviewers or skills (AUTO-2).
            "./.claude/agents",
            "./.claude/skills",
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
            "~/Projects/northwind-shop/.git/config.worktree",
            # Refused today by a list the runtime builds, not by anything here (measured, AUTO-2).
            "~/Projects/northwind-shop/.git/commondir",
            # A nested `.git` is what `git status` outside the sandbox runs a child in (AUTO-2). The
            # `*/` keeps a checkout's own `.git` out, which a commit needs. Narrows, does not close:
            # moving in a folder that already holds one is not refused (measured, decision 0011).
            "~/Projects/northwind-shop/*/**/.git",
            "~/Projects/northwind-shop-*/*/**/.git",
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
        # The same for a session's own reviewers and skills (AUTO-2).
        "Edit(./.claude/agents/**)",
        "Write(./.claude/agents/**)",
        "Edit(./.claude/skills/**)",
        "Write(./.claude/skills/**)",
        # The file tools never meet the sandbox, so route B needs its own rules (second re-review).
        "Edit(./**/.git)",
        "Edit(./**/.git/**)",
        "Edit(~/Projects/northwind-shop/.git/worktrees/**)",
        "Edit(~/Projects/northwind-shop/.git/commondir)",
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
        "Bash(uv run *northwind_llm_replay*promote*)",
        # Wide for the same reason. `make ingest-enriched` and `make eval-enrichment-report` prompt
        # too: neither spends, and a false prompt is the safe direction (AUTO-2, item 5).
        "Bash(make *enrich*)",
        "Bash(uv run *make*enrich*)",
        "Bash(uv run *northwind_evals*enrich*)",
        # The sandbox covers Bash only, so the file tools need their own rules for these.
        "Edit(./evals/baselines/**)",
        "Write(./evals/baselines/**)",
        "Edit(./evals/fixtures/known_failures.json)",
        "Write(./evals/fixtures/known_failures.json)",
    ],
}


NESTED_GIT_GLOBS = ["~/Projects/northwind-shop/*/**/.git", "~/Projects/northwind-shop-*/*/**/.git"]


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
    """Rows 5a and 5b: your terminal's git trusts these five. Everything else in `.git` stays
    writable, and a relative `./.git` would be the main checkout's directory, which would stop its
    commits."""
    deny_write = load("settings.proposed.json")["sandbox"]["filesystem"]["denyWrite"]
    git_entries = [path for path in deny_write if ".git" in path and ".gitignore" not in path]
    assert git_entries == [
        "~/Projects/northwind-shop-*/.git",
        "~/Projects/northwind-shop/.git/worktrees/*/gitdir",
        "~/Projects/northwind-shop/.git/worktrees/*/commondir",
        "~/Projects/northwind-shop/.git/worktrees/*/config.worktree",
        "~/Projects/northwind-shop/.git/config.worktree",
        "~/Projects/northwind-shop/.git/commondir",
        "~/Projects/northwind-shop/*/**/.git",
        "~/Projects/northwind-shop-*/*/**/.git",
    ]


def write_rule(glob: str) -> re.Pattern[str]:
    """What Claude Code 2.1.318 turns a `denyWrite` glob into: read from the installed binary, and
    measured against an installed rule (AUTO-2 evidence). A model: the real rule is not run here."""
    body = re.escape(glob).replace(r"\*\*/", "(.*/)?").replace(r"\*", "[^/]*")
    return re.compile(f"^{body}(/.*)?$", re.IGNORECASE)


NESTED_RULES = [write_rule(path) for path in NESTED_GIT_GLOBS]


def denied(path: str) -> bool:
    return any(rule.match(path) for rule in NESTED_RULES)


@pytest.mark.req("AUTO-2")
def test_the_nested_git_rules_are_in_the_proposal() -> None:
    deny_write = load("settings.proposed.json")["sandbox"]["filesystem"]["denyWrite"]
    assert [path for path in deny_write if "/**/" in path] == NESTED_GIT_GLOBS


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize("checkout", ["~/Projects/northwind-shop", "~/Projects/northwind-shop-b"])
@pytest.mark.parametrize(
    "path",
    ["sub/.git", "sub/.GIT", "a/b/c/.git", "sub/.git/config", "node_modules/x/.git/HEAD"],
)
def test_a_nested_git_is_denied_in_both_checkouts(checkout: str, path: str) -> None:
    assert denied(f"{checkout}/{path}")


@pytest.mark.req("AUTO-2")
@pytest.mark.parametrize(
    "path",
    [
        # A commit in either checkout writes here, so a rule that matched would stop it.
        "~/Projects/northwind-shop/.git",
        "~/Projects/northwind-shop/.git/index",
        "~/Projects/northwind-shop/.git/worktrees/northwind-shop-b/index",
        "~/Projects/northwind-shop/.git/objects/ab/cd",
        "~/Projects/northwind-shop/.gitignore",
        "~/Projects/northwind-shop/.github/workflows/ci.yml",
        "~/Projects/northwind-shop-b/.gitignore",
        "~/Projects/northwind-shop-b/sub/.gitignore",
        "~/Projects/northwind-shop-b/sub/.github/x",
        "~/Projects/northwind-shop-b/sub/x.git",
        "~/Projects/other/sub/.git",
    ],
)
def test_the_nested_git_rules_leave_a_commit_alone(path: str) -> None:
    assert not denied(path)


@pytest.mark.req("AUTO-2")
def test_a_session_cannot_edit_its_own_reviewers_or_skills() -> None:
    """The sandbox covers Bash, and the file tools follow permission rules, so it takes both."""
    settings = load("settings.proposed.json")
    deny_write = settings["sandbox"]["filesystem"]["denyWrite"]
    for folder in ("agents", "skills"):
        assert f"./.claude/{folder}" in deny_write
        assert f"Edit(./.claude/{folder}/**)" in settings["permissions"]["deny"]
        assert f"Write(./.claude/{folder}/**)" in settings["permissions"]["deny"]


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


@pytest.mark.req("AUTO-2")
def test_the_enrich_rules_are_the_wide_forms() -> None:
    ask = load("settings.proposed.json")["permissions"]["ask"]
    assert "Bash(make *enrich*)" in ask
    # Rig run 2 showed these miss `make "x"` and `make .DEFAULT_GOAL=x`.
    assert "Bash(make enrich*)" not in ask
    assert "Bash(make * enrich*)" not in ask
