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

## The criterion, measured once before any recording (no LLM)

`make eval-compare-retrievers` at the reviewed code, with the parameters fixed in the plan (RRF k = 60, depth 50, FTS
on `text`, `english`). Path: `retrieve_context` and its two arms, audience shopper, each item's region. 64
retrieval-scored items. Full output: `docs/reviews/RAG-03-retrieval-report.md`.

| Arm | recall@5 | recall@10 | MRR |
|---|---|---|---|
| dense | 0.750000 | 0.832031 | 0.700670 |
| keyword | 0.882812 | 0.910156 | 0.857205 |
| hybrid | 0.954427 | 0.984375 | 0.880729 |

Hybrid is strictly above both on recall@5. It ran once. The first attempt failed on an import name clash and an
uncached model path, before anything was scored, and nothing was tuned afterwards. The integration test
`test_hybrid_beats_dense_and_keyword_on_recall_at_5` reproduces this on a fresh index built from the fixtures with the
real model.

## Planted injection reviews in a top 5, dense against hybrid (security review, finding 1)

The definition is the one `enrichment_report` uses: over all 100 items, count each planted TRAP-INJ review that is in
an item's top 5 source IDs, on each arm's region-filtered path. The criterion does not use these counts; **they are
for the user's call.**

- Appearances: dense 22, keyword 8, hybrid 16. Dense to hybrid: -6.
- New in hybrid: EV-061 TRAP-INJ-011, EV-068 TRAP-INJ-001, EV-084 TRAP-INJ-005, EV-096 TRAP-INJ-005,
  EV-099 TRAP-INJ-001.
- Gone in hybrid: 11 pairs, listed in the report.

The answer-level containment gate still applies to the adversarial and unanswerable items when they are re-recorded.
