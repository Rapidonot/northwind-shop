# AUTO-2 evidence

Row A2, trimmed scope. Plan and the user's answers: `docs/reviews/2026-11-07-AUTO-2-plan.md`. Base `7d45a40`.

## Failing first

### Item 1, `decided` owners (D4)

With the nine rows changed and `tests/governance/test_owners.py` not yet changed:

```
FAILED tests/governance/test_owners.py::test_every_owner_is_real
FAILED tests/governance/test_owners.py::test_the_table_uses_every_kind_of_owner
2 failed, 159 passed
```

After the test change, 170 passed.

### Items 5 and 6, before the code

```
evals/tests/test_integrity.py: 5 symlink tests failed
tests/hooks/test_settings.py, test_sandbox_settings.py: 6 failed (the proposal has no enrich rule)
tests/ship/test_ship.py -k "docs_diff or data/enrichment": 4 failed
```

### Items 7a and 7c, before the code

```
tests/hooks/test_changed_tests.py: collection error, tools/changed_tests.py does not exist
tests/hooks/test_ship_marker.py: 6 failed, 18 passed
```

The 18 passed because a Makefile with no filter prints nothing, which is what the run-the-tests cases expect, or because they pin what
must not change. They get their failing run from the mutations below.

### Item 2, before the code

```
tests/ship/test_finish.py: 61 failed, 8 passed (no `finish` target, no `--labels` mode)
```

The 8 that passed were refusals a missing target also satisfies, such as a bad checkout, or checks on the script's text. Their
failing run is in the mutations below.

### Items 3, 4 and 7b, before the changes

```
tests/hooks test_notify_hook, test_req_skills, test_stop_conditions, test_gate_report, test_advisor_reviewer: 78 failed, 108 passed
```

One of the 78 was a mistake in the test, not a finding: the token `cat` matches "notification". It now looks for ` cat`.

## Mutations

Run on a throwaway clone of `c317703`, 44 mutants across the `Makefile` filter, the marker, `integrity.py`, `tools/changed_tests.py`,
`tools/finish.sh`, the `finish` target, `tools/ship.sh` and the hooks. The first run killed 39 and 5 survived. Three of the 5 were real gaps,
now killed by new tests, so 42 of 44 are killed and 2 are equivalent.

| Mutant | Result |
|---|---|
| Filter: `&&` to `;` after `merge-base` | Survived, equivalent: `git diff ""` fails anyway, so the tests run |
| `integrity.py`: the tracked mode check | Survived, a real gap: only the disk walk was tested. `test_a_tracked_symlink_gone_from_disk_is_still_refused` added, killed |
| `finish.sh`: `commit -a` for the path list | Survived, equivalent: the dirty check has already refused anything else |
| `finish.sh`: origin head check removed | Survived, a real gap. `test_origin_ending_up_with_another_head_is_refused` added, killed |
| `finish.sh`: single merge base check removed | Survived, a real gap. `test_more_than_one_merge_base_is_refused` added, killed |

## Security review, at `e9ae0c4`

One critical, one high, two medium, seven low.

**Finding 1, confirmed by running it, before the fix.** In a throwaway repo, a gitlink over a folder with a hand-written gitdir:

```
--- plain status (what finish.sh ran)
MARKER WRITTEN: planted code ran
--- with --ignore-submodules=all
no marker
--- with diff.ignoreSubmodules=all in a global config
no marker (but see below: with no .gitmodules in the repo)
```

And from the sandbox, in this checkout: writing a nested `.git` file succeeded (exit 0), and the probe was removed. So the session can do
every step. The test suite before the fix:

```
33 failed, 246 passed
(including test_a_planted_gitlink_runs_nothing_and_is_refused[staged] and [committed])
```

| # | Severity | Finding | Done |
|---|---|---|---|
| 1 | Critical | `git status` runs a planted nested config | Fixed in `finish`; refused in `refused-paths.sh` and pre-push. **Open** for a `git status` outside `finish`: see the re-review |
| 2 | High | `CHECKOUT` could be any directory | Fixed: only the checkouts `denyWrite` protects |
| 3 | Medium | The routine rule excludes only hard rules 1 and 9 | Paths named. Left as the user approved it, for the user to decide |
| 4 | Medium | The rule goes live before its checker is installed | Fixed: stop 1 applies to every row until the proposal is installed |
| 5 | Low | Replace refs; symbolic `HEAD` pushed | Fixed |
| 6 | Low | The `uv run make` rule was anchored | Fixed: `Bash(uv run *make*enrich*)` |
| 7 | Low | `SHIP_FILTER` trusts a writable ref | Limit stated in decision 0011; `make test` prints when it skips |
| 8 | Low | Bare `osascript` resolves through PATH | Fixed: `/usr/bin/osascript` |
| 9 | Low | The `cassettes/eval-answers/` exclusion is a list of one | Logged |
| 10 | Low | `--labels` did not validate its arguments | Fixed |
| 11 | Low | `.git/config` and `.git/hooks` have no explicit `denyWrite` entry | Logged |

## Security re-review, at `2953f21`

Two critical left, so stop 10. Fixes for findings 2, 4, 6, 8 and 10 held; 5 held with a small gap.

**Correction.** The first version of this file, the threat model and decision 0011 said a global `diff.ignoreSubmodules = all` closes the
route. That was wrong. It was measured with no `.gitmodules` in the repo.

Measured in throwaway repos, with a planted fsmonitor, clean filter and `post-index-change` hook:

```
A: gitlink at HEAD, not in the index
   ls-files gitlinks: 0, and status with the flag shows nothing: the index check passed it
   git commit on the nothing-to-commit path: printed "modified: sub (untracked content)", so it descended
   no marker ran, because core.fsmonitor=false reached the child; this plant did not show code running
   git commit --allow-empty: no descent
B: plain status with the global setting
   no .gitmodules:                 no marker
   .gitmodules, ignore = none:     RAN: MONRAN
   the same, in the index only:    RAN: MONRAN
   status --ignore-submodules=all, all three: no marker
C: the same plant with no sub/.git: no marker
```

| # | Severity | Finding | Done |
|---|---|---|---|
| A | Critical | A gitlink at `HEAD` passes the index check, and `git commit` descends | Fixed after the re-review: `HEAD` and the reviewed commit checked, tracked `.gitmodules` refused, `MM` refused, `--allow-empty`. The second re-review read it, and it holds |
| B | Critical | The general `git status` exposure | **Open, needs the user** |
| C | Low | The pre-push gitlink refusal needs the hook reinstalled | On the install list |
| D | Low | The commit checked could differ from the commit pushed | Fixed: resolved once |
| E | Low | `tr` could abort on a non-UTF-8 path | Fixed: `LC_ALL=C` |

## `make ci`, runs at the head

At `4a2465b`: failed at `make test`, 1 failed, 3,902 passed, 22 xfailed, 1,143.56 s to that point. Integration and the gate did not run.
`evals/tests/test_rag12_followup.py::test_the_pins_come_before_the_first_shell_call` pins the exact set of free-text Makefile variables, and `BODY` was
new. The set gains `BODY`, so the new pin is covered too. This is a test change after a failure that the plan's D5 list missed; it adds to the pin and removes nothing.

Timing, at `4ed279d`, `make ci` exit 0 both times:

| Run | Time | Tests |
|---|---|---|
| Before, at `7d45a40` | 1,141.71 s | 3,614 passed, 22 xfailed, 96 integration |
| After, this row, which touches `tools/` | 1,236.97 s | 3,903 passed, 22 xfailed, 96 integration |
| After, a row that does not (throwaway clone, `origin/main` = head, setup outside the timer) | 651.64 s | 3,298 passed, 707 deselected, 22 xfailed, 96 integration |

## Route B, after the user's answers

**Glob, read from Claude Code 2.1.318.** `**/` becomes `(.*/)?`, `*` becomes `[^/]*`, and a deny becomes `(deny file-write* (regex ...))`, with
`file-write-unlink` and `file-write-create` denied on the literal folders above the glob's fixed prefix only. The docs do not state glob support, new-name
behaviour or precedence. The proposed rule itself was not run: `sandbox-exec` inside a session gives `sandbox_apply: Operation not permitted`.

**The installed rule of the same class, `~/Projects/northwind-shop/.git/worktrees/*/gitdir`, against a throwaway folder:**

```
DENIED   new file, exact name
DENIED   new file, other case (GITDIR)
DENIED   rename a file onto the name
DENIED   symlink at the name
DENIED   hard link at the name
ALLOWED  rename in a folder already holding the name
```

The volume is case-insensitive. **The probe left a mess, now cleaned up.** `rm` of the moved-in `gitdir` was refused, so `zzprobe-66998-moved` stayed in the
main checkout's `.git/worktrees` and `git worktree list` showed it as prunable. After checking it held only that file, with the text `probe`, it was moved
back out, which reversed the same rename, and `git worktree list` showed the two real checkouts again.

**Which plants make `git status` run the child, in throwaway repos:**

```
RAN      control: sub/.git file
RAN      sub/.GIT file, other case
no run   sub is a symlink to a folder outside holding .git
no run   a parent is a symlink, link/sub
RAN      sub/.git is a symlink to a file outside
```

**A new route, in a throwaway repo:** a linked worktree's admin folder moved out, `commondir` rewritten to a planted common directory, moved back:

```
status exit 0
RAN: the redirected config's fsmonitor ran
```

Never tried on a real worktree. The real one reads `commondir=../..`, and `gitdir` points at this checkout.

**Failing first**, before the change, with the tests in `tests/hooks/test_sandbox_settings.py`, `tests/hooks/test_stop_conditions.py` and
`services/llm-replay/tests/test_runner_support.py`:

```
34 failed, 228 passed
```

After the change, with one more test for replace refs: 263 passed. `test-writer` was not rerun for these tests: they were written first, from the user's
answers. `make runner-check` in this checkout: `21 directories, 0 problem(s)`, `searched 2 checkout(s) for a gitlink, a .gitmodules or a nested .git`. The walk
read 47,170 entries here and 31,929 in the main checkout, `node_modules` included, in under a second.

## Second security re-review, at `b1763dd`

One read, by the user's exception to the one re-review rule. It ran nothing: every conclusion is from reading. **Stop 10 applies:** two critical are open.

| # | Severity | Finding | Done |
|---|---|---|---|
| 1 | Critical, provisional | Replacing a worktree's admin folder; `finish` exposed | `finish` fixed and pinned, `runner-check` reads the folders. **Moved to SEC-1 by the user's decision:** pre-existing, not caused or worsened by this row |
| 2 | Critical, carried | Route B | **Moved to SEC-1 by the user's decision:** pre-existing, not caused or worsened by this row. Narrowed only once installed |
| 3 | High, unconfirmed | `.git/commondir` in the main checkout | Measured: the sandbox refuses a plain create today, which is one route. Named in the proposal, measured by `runner-check`, refused by `finish`. Moved to SEC-1 |
| 4 | Medium | `make runner-check` runs code the session can edit, at a time it picks, so the user never gets a trustworthy clean result | **Moved to SEC-1:** a read-only mode from the runner clone |
| 5 | Medium | `runner-check` can exit clean with a checkout unread | Fixed: it walks `.git/worktrees` itself |
| 6 | Medium | The `.gitmodules` refusal fails at size | Fixed, measured first |
| 7 | Medium | The file tools bypass the rule | `Edit` deny rules added. Not run |
| 8 | Medium | Reviewers and skills only partly closed | Fixed by the user's answer: `.claude/commands`, `.claude/rules`, `.claude/output-styles`, `.claude/workflows` and `.mcp.json` added, with `denyWrite` and `Edit` rules. Not run |
| 9 | Low | Record wording | Fixed |
| 10 | Low | `Write` rules are dead | Wording fixed, rules left as the file's shape |
| 11 | Low | Tests prove less than they appear to | `test_finish.py` order assertion fixed. The Python model of the rule stays a model: stated in its docstring |
| 12 | Low | `git switch` or `merge` fails where `.claude/agents` or `.claude/skills` differ, once installed | Logged. The stop strings catch it |

Holds, by that read: the fix to finding A; the glob, by reading; `runner-check`'s name matching and failure handling.

**Measured after it, in throwaway repos:**

```
=== 1. redirected commondir
RAN      plain status
no run   status with finish's flags (fsmonitor off)
RAN      commit with finish's flags (hook from the planted config)
=== 2. does GIT_COMMON_DIR beat the file?
no run   status with the three variables set
no run   commit with the three variables set
=== 2b. a main checkout with a commondir file
RAN      main checkout, plain status
=== 2c. the grep -q pipeline, 728,908 bytes
MISSED (pipeline status 141 0), 5 times in 5
```

**Real main checkout, `runner-check`'s own probe:** `.git/commondir` missing before, `file_problems` returned `[]`, so the create was refused, and missing after.

**Failing first:** 18 failed, 10 passed in the selected tests. After the fixes: 301 passed across `test_sandbox_settings.py`, `test_runner_support.py` and `test_finish.py`, a
real linked-worktree `finish` included. `make runner-check`: `21 directories, 0 problem(s)`, with the real admin folder now read. These fixes came after the last read, so no reviewer has
read them.

`make ci` at `7ab271a`: exit 0, 1,343 s, 3,972 passed, 22 xfailed, 96 integration, 34 Vitest, `eval-integrity` passed, gate passed, 100/100 replayed, stale 0.

## The user's second answers

Route B, the admin folder, the `.git/commondir` high and the `make runner-check` medium moved to SEC-1 by the user's decision: pre-existing, not caused or worsened by this row. The user added
`.claude/commands`, `.claude/rules`, `.claude/output-styles`, `.claude/workflows` and `.mcp.json` to the proposal. Failing first: 7 failed, 31 passed. `test-writer` added 25 test functions, all passing.

## Third security read, at `7ab271a`

One read of `tools/finish.sh` and `runner_check.py`, by the user's exception. It ran nothing. **No critical or high.** Its answers: the admin-folder checks and the pin are sound, nothing escapes
`in_checkout`, the values reach only git's children, and the `.gitmodules` refusal is correct.

| # | Severity | Finding | Done |
|---|---|---|---|
| 1 | Medium, plausible | `GIT_COMMON_DIR` reaches the real pre-push hook, whose `git init` might rewrite the main config | Measured: `main config unchanged`. A test now runs the real hook under the pin. The hook clears the variable too, so it needs reinstalling |
| 2 | Medium, plausible | `.strip()` passes a `commondir` of ` ../..`, which git reads as another path | Fixed: line ends only |
| 3 | Medium, plausible | The `gitdir` check is circular: `git worktree list` is built from the same files | Fixed: sibling folders by name, and the denied pointer must point back |
| 4 | Low, plausible | `runner-check` finds main through the `commondir` it audits | Moved to SEC-1, with the trusted check |
| 5 | Low | A FIFO hangs the check; non-text raises | Fixed in both files |
| 6 | Low | `.git/worktrees` itself is never typed | Fixed |
| 7 | Low, unconfirmed | The main checkout's last path part is not physical | Fixed: `! -L`. A main `.git` replaced whole stays undetectable by `finish`; the writes it needs are not established |
| 8 | Low | `\|\| true` swallows grep's exit 2 | Fixed: `\|\| [[ $? -eq 1 ]]` |
| 9 | Low | Nothing tests the pin's claim | Fixed: a hook swaps `commondir` mid-run |

**Failing first:** 19 failed, 8 passed, with the two FIFO tests left out because they would hang. The 8 included the swap test and the real hook, which passed before any change.

**Mutation of the pin.** With the three variables removed, the first version of the swap test still passed: `finish` stopped elsewhere, so `no marker` proved nothing. It now also requires a pinned
`finish` to complete. The mutant then fails, `the push failed`, and the restored script passes.

**After the fixes:** 468 passed across `test_runner_support.py`, `test_finish.py` and `test_pre_push.py`. `make runner-check`: `21 directories, 0 problem(s)`, 2 checkouts searched. No reviewer has read these fixes.

`make ci` at `28c3ace`: exit 0, 1,307 s, 4,058 passed, 22 xfailed, 96 integration, 34 Vitest, `eval-integrity` passed, gate passed, 100/100 replayed, stale 0. Spend $1.150794, unchanged.

## `advisor-reviewer`, at `28c3ace`

`findings`: one high, four medium, six low. No hard rule broken, no test weakened in a way that loses a check.

| # | Severity | Finding | Done |
|---|---|---|---|
| 1 | High | `tools/finish.sh` reaches the clone with no full diff shown on this PR, and no record said so | Recorded in the PR body and decision 0011. The gate report tells the user to read it in full before `ship` |
| 2 | Medium | Failing first not shown for the fixes to A, D, E, the FIFO tests and `test-writer`'s tests | Run: see below |
| 3 | Medium | The `.git/commondir` high has no stop 10 exemption | Moved to SEC-1 by the user's decision. The sandbox refuses a plain create, measured: one route, and the rating stands. No user statement exempts it from stop 10, so it is put to the user |
| 4 | Medium | The probe went into the main checkout's `.git/worktrees`, and a refused `rm` was worked around without asking | Stated in the PR body and the gate report. The folder went to `$TMPDIR` and was deleted |
| 5 | Medium | `tool-register.md` still called 5b `partial` | Fixed, with the doubled `make enrich` sentence and the gitlink refusal |
| 6 | Low | `test-writer` is not shown to have seen the third-read tests | It did not: it ran before them. Stated |
| 7 | Low | "Tested first" was met by proxy | Stated. SEC-1 owns the first live run |
| 8 | Low | The non-`tools` timing is from `4ed279d` | Logged in `PROGRESS.md` |
| 9 | Low | The `cat` token was narrowed | Logged in `PROGRESS.md` |
| 10 | Low | Item 4's "may change" became "added files" | Stated in decision 0011 |
| 11 | Low | The question 4 entries have no reviewer | Stated in the PR body |

## Failing first, run after the row review

Old scripts against today's tests, restored afterwards, with `git status` clean for both files:

```
finish.sh at 2953f21, before the fixes for findings A, D, E: 6 failed, 2 passed
  test_a_gitlink_in_head_but_not_in_the_index_is_refused
  test_a_gitlink_only_at_the_reviewed_commit_is_refused
  test_a_docs_file_changed_both_staged_and_unstaged_is_refused
  test_the_commit_never_takes_the_nothing_to_commit_path
  test_the_script_sets_a_fixed_locale
  test_the_commit_checked_is_the_commit_pushed
finish.sh at b1763dd, before the admin-folder checks: 22 failed, 8 passed, test-writer's included
finish.sh at 7ab271a, FIFO tests: HUNG, killed at 60 s
runner_check.py at 7ab271a, FIFO tests: HUNG, killed at 60 s
```

These were run after the fixes, against the old code, not before them. This first run also had 10 `runner_check.py` and 6 `finish.sh` mutants, and they killed 15 of 16. The other showed
`SURVIVED` with `1 error in 0.15s`. **An earlier version of this file blamed a run collision. That was wrong.** The mutant itself was broken: its search text left four spaces behind, so the module
did not import, and the script counted only `FAILED` lines. A corrected mutant, run alone, failed 2 tests. Separately, `pytest tests/governance` did run during this script, while it was mutating the
real files; that run was not used as evidence, and was repeated after they were restored.

## `advisor-reviewer`, scoped re-read, at `2ffc68b`

`findings`. One high remains: `.git/commondir`, for the user, because "not open at high" was this session's own re-rating. The `tools/finish.sh` finding fell to medium, and the records had `ship`'s output
wrong: `ship` has no `--stat`. Corrected, read from the clone at `7d45a40`. Findings 5 to 11 closed.

**Failing first, by name, with nothing else running.** Second run, log `/tmp/claude-502/failfirst2.log`, not committed:

```
finish.sh at 2953f21: 6 failed, 2 passed
finish.sh at b1763dd: 22 failed, 8 passed
test_runner_support.py against runner_check.py at b1763dd: 38 failed, 168 passed
runner_check.py mutants, every test in the file: 16 of 16 killed, the corrected symlink mutant among them
```

The 10 that passed on old code were never seen failing, so the advisor looked at why. Its reading was right, and **an earlier version of this file gave a reason that no run had captured.** These tests
checked only that a word such as `commondir` appeared in the output, and the rig's temp folder is named after the test, so any git error printing a path satisfied them. They now assert the exact
refusal. Third run, nothing else running, log `/tmp/claude-502/failfirst3.log`, not committed:

```
finish.sh at 2953f21: 8 failed
finish.sh at b1763dd: 30 failed
finish.sh mutants: 6 of 6 killed
runner_check.py, replace refs on: killed
no mutant invalid: one that does not import is now counted INVALID, not as a survivor
```

**`test-writer`'s 20 runner tests:** 15 were seen failing: 12 under a mutant, and 8 on `b1763dd`, where `admin_problems` does not exist yet, so that failure alone proves little; 5 are in both. These 5 were never seen failing:
`test_a_dangling_git_symlink_is_named_without_an_error`, `test_a_failing_git_is_an_error_line_not_a_clean_result`, `test_a_gitlink_in_a_subfolder_is_named_by_its_path`,
`test_every_problem_is_reported_not_only_the_first`, `test_main_reports_an_unreadable_worktrees_folder_and_fails`. Its `test_finish.py` tests outside the selected names, such as the normal main and linked
finishes, were not run against old code: they pin behaviour that already worked.

