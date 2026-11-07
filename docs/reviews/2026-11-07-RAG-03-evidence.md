# RAG-03 (#8) evidence

Plan: `docs/reviews/2026-11-07-RAG-03-plan.md`. Branch `req/RAG-03-hybrid`, made by the user on main `8218b02`,
with the user's hotfix commits `d286c27`, `3821e4d` and `c0238d8`.

## `git fetch origin` (2026-11-07)

From the sandbox: `fatal: could not read Username for 'https://github.com': Device not configured`, exit 128. No
`Operation not permitted`. So `origin/main` is the local ref at `8218b02`, not refreshed. The user runs
`git fetch origin` in their terminal before `make ci` and `make review-diff`, and this file records the base SHA used.

## Commit 1 (`ed0d6d9`): fail-first log for the hotfix mirror

`tests/hooks/test_sandbox_settings.py` (new version) run against the old `tools/settings.proposed.json` (from `HEAD`
before the commit), copied here from the scratch log because the scratch folder is temporary:

```
FAILED tests/hooks/test_sandbox_settings.py::test_sandbox_block_is_exactly_the_reviewed_one
FAILED tests/hooks/test_sandbox_settings.py::test_permissions_are_exactly_the_reviewed_ones
FAILED tests/hooks/test_sandbox_settings.py::test_only_what_redirects_git_is_denied_so_a_commit_still_works
FAILED tests/hooks/test_sandbox_settings.py::test_the_rules_that_blocked_git_are_not_proposed_again[~/Projects/northwind-shop/.git/commondir]
FAILED tests/hooks/test_sandbox_settings.py::test_the_rules_that_blocked_git_are_not_proposed_again[~/Projects/northwind-shop/*/**/.git]
FAILED tests/hooks/test_sandbox_settings.py::test_the_rules_that_blocked_git_are_not_proposed_again[~/Projects/northwind-shop-*/*/**/.git]
FAILED tests/hooks/test_sandbox_settings.py::test_the_rules_that_blocked_git_are_not_proposed_again[Edit(./**/.git)]
FAILED tests/hooks/test_sandbox_settings.py::test_the_rules_that_blocked_git_are_not_proposed_again[Edit(./**/.git/**)]
FAILED tests/hooks/test_sandbox_settings.py::test_the_rules_that_blocked_git_are_not_proposed_again[Edit(~/Projects/northwind-shop/.git/worktrees/**)]
FAILED tests/hooks/test_sandbox_settings.py::test_the_rules_that_blocked_git_are_not_proposed_again[Edit(~/Projects/northwind-shop/.git/commondir)]
FAILED tests/hooks/test_sandbox_settings.py::test_no_proposed_rule_reaches_into_git_beyond_the_five
11 failed, 13 passed in 0.10s
```

On the new proposal: all pass. `make check` at the commit: 624 passed, 22 xfailed. `tests/ship` and
`tests/governance`: 973 passed.
