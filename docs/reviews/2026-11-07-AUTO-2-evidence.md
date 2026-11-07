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
| A | Critical | A gitlink at `HEAD` passes the index check, and `git commit` descends | Fixed after the re-review: `HEAD` and the reviewed commit checked, tracked `.gitmodules` refused, `MM` refused, `--allow-empty`. **No reviewer has read this fix** |
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

