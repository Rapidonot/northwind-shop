# 0006: Eval gate hardening

Date: 2026-11-04. Requirement: RAG-12, row 5a. Addresses third-pass findings F10, F5 and F6: F10 in
full, F5 by printing only, and F6's prompt through the installed settings (PR #19). Extends decision 0003
and replaces its last consequence.

## Context

`EVAL_ALLOW_STALE` waived every stale item, adversarial and unanswerable ones included, and the gate
could pass with no answers replayed. Nothing compared `known_failures.json` with the base branch,
overrides named no approver, and nothing stood between Claude and `make eval-promote`.

## Decision

1. A stale adversarial or unanswerable item fails the gate. Neither `EVAL_ALLOW_STALE` nor a
   `known_failures.json` entry waives it, because a stale item has no current evidence.
2. The gate fails if no answers replay.
3. `make eval-integrity` prints every `known_failures.json` entry that the base branch does not hold
   word for word, so a changed `approved_by` shows as added. Printing does not fail the run: the check
   is a person reading the line.
4. A recorded reason needs an approver: `ACCEPT_REGRESSION` and `BOOTSTRAP` both need `APPROVED_BY`.
   The gate refuses a baseline holding an override with no approver.
5. **No promotion at all until row 5b lands.** After that, promotion is the user's, from the runner
   clone through a new entry point, not from a checkout.
   `sandbox.filesystem.denyWrite` on `evals/baselines` and `evals/fixtures/known_failures.json` stops
   sandboxed Bash writing those two paths in this checkout, and `ask` rules cover the file tools, which
   the sandbox does not reach. Claude Code warns that `Write(path)` rules are never matched and
   `Edit(path)` rules cover both tools, so the `Edit` rules are the ones doing this. The post-install
   check confirmed it: the Write tool prompted. This protects the
   working-tree files, not the committed content the gate reads in CI. See "Open limits".
6. The `ask` rules on `make eval-promote`, `make eval-gate-stale-ok` and the `uv run` promote command
   are a courtesy. `Bash(make *)` and `Bash(uv run *)` reach the same code, and some ways of writing a
   command miss the rules. The rig results list which. A miss costs a prompt, and the write to those
   two paths is still refused: the rig's write and rename rows held under the real
   sandbox. A form with no `allow` rule stops in default mode only. In auto mode the classifier
   decides, so `denyWrite` is the control there.
7. The three free-text `eval-promote` variables are pinned, so a hostile value is passed literally.
   This protects against a hostile value, not a hostile command line: whoever controls the whole
   command can use another variable name. See "Found during this row".
8. `denyWrite` also names what redirects or configures git for a linked worktree, because your
   terminal's git trusts those files: the `.git` pointer file, and `gitdir`, `commondir` and
   `config.worktree` in the common directory's `worktrees/<name>/`. `make runner-check` measures them.
   HEAD, index, refs and logs stay writable, because a sandboxed commit writes them. The entries are
   absolute: a relative `./.git` would be the main checkout's directory, which its commits need.

## Consequences

- **After the credits expire, a stale adversarial or unanswerable item has no honest way through the
  gate.** Any change that shifts one of those items' retrieved context fails, `make eval-gate-stale-ok`
  included, until it is re-recorded with API access. Row #8 hits this inside the credit window, where
  `make eval-live` handles it. In Window B it blocks merges, and needs a new decision then. This
  replaces 0003's "stale recordings need `EVAL_ALLOW_STALE=1` in CI".
- `approved_by` is still a claim, not proof, as 0003 says.
- `EVAL_ALLOW_STALE=1 uv run python -m northwind_evals.cli gate` matches no `ask` rule. Rule 1 limits
  what it can waive.
- In the sandbox, `make eval-promote` fails at the write with a raw traceback. The candidate survives,
  because it is deleted only after the save.
- Sandboxed git operations that would change these two paths become the user's. See below.

## Open limits

From the row 5a security reviews. None of these is closed by this row.

- **Promoting from a checkout runs code the session can edit, unsandboxed (first review, finding 1,
  high).** This is the shape decision 0005 called F1. `make eval-promote` in a terminal runs this
  checkout's Makefile and `evals/src` with no sandbox, so an edit there could reach the key file, the
  runner and the ledger. **Decided by the user: nobody promotes until row 5b lands, and there is no
  interim procedure.** 5b adds a small promote entry point for the runner clone, because `cli.py`
  imports the database and RAG code and the clone has no full `.venv`. 5b must land before row #8, the
  next row that needs a promotion.
- **`denyWrite` covers paths in one checkout (first review, finding 2, high). Confirmed by rig run 2:**
  `git hash-object`, `git update-index --cacheinfo` and `git commit` changed the committed baseline in
  the real sandbox without touching the file. The nested-clone route stays unmeasured: `git clone`
  failed copying its template into `.git/hooks`, so no clone existed to test.
  `make eval-integrity` does not report a changed baseline unless an override was lost. **Decided by
  the user: the control is a CI check in row 5b**, which fails any PR
  changing either path unless it has a `baseline-change` label. Only the user can add a label, because
  the session has no GitHub credentials. Required review is not an option: the user is the only
  reviewer and cannot approve their own PR. **Residual risk, accepted:** a commit could edit the
  workflow that holds the check, and the user's PR review covers that. Until 5b lands, the user checks
  each PR's file list for those two paths.
- **Git in a terminal trusts files in the checkout (second review, finding 1).** A rewritten `.git`
  pointer could send your terminal's `git status` to a config the session wrote, and that config can
  run a program. `make runner-check` measured this from the sandbox on 2026-11-04: the pointer and
  `commondir` refuse writes, `config.worktree` cannot be created, and `gitdir` **is** writable. Hooks
  and `.git/config` already refuse writes. What refuses them is the session's sandbox policy, not an
  entry of ours, so rule 8 states them explicitly. With rule 8's entries, rig run 2 saw all four
  refused, and a move of the pointer refused, with the globs working in their absolute form. The installed
  settings refuse `gitdir` too: `make runner-check` reported 0 problem(s) in the linked worktree on
  2026-11-05. The recovery below still starts by checking the pointer.
- **A forged new cassette can make a stale item look fresh (first review, finding 11).**
  `make eval-integrity` refuses modified cassettes, not added ones.
- **The added-entry line sits in a passing CI step (first review, finding 7).** Nothing makes anyone
  read it. A push to main also compares HEAD with itself (finding 12), so a direct commit to main shows
  nothing.
- **Older branches keep their own settings.** The rules reach a branch only once it holds the new
  tracked file.
- **`lost_overrides` reads the working tree only**, so it can lag HEAD the way the known-failure check
  did before this row fixed it.

## Found during this row

`$(if $(ACCEPT_REGRESSION),…)` and `$(if $(BOOTSTRAP),…)` ran a `$(shell …)` held in the value, from
the command line and from the environment, before any recipe started (measured with `make -n`, GNU
Make 3.81). Not a third-pass finding. The Makefile now pins all three free-text variables with
`override VAR := $(value VAR)` and tests them in the shell, and
`test_eval_promote_passes_a_value_literally_and_runs_nothing` covers each one by both routes.

The pins sit above every `$(shell …)`, because make 4.4 and later reportedly export variables to those
calls, which would expand a command-line value during parsing. That is from the security review, not
measured: only 3.81 is installed here, and `ubuntu-latest` reportedly ships 4.3, so 4.4 is exercised
nowhere. A test pins the order instead. `SUITE`, `EVAL_SLICE`, `RUN_BUDGET` and `CASSETTES_DIR` are not
pinned. By the same reasoning, make likely expands a `$(shell …)` in those from the command line
before the recipe's own check runs. Rule 7 already accepts a hostile command line.

## A git switch or merge that failed partway

Rig run 2 measured the two switch rows and the fast-forward row under the real `denyWrite`, with
`Operation not permitted`. The true-merge row was measured only in throwaway repos, with
`evals/baselines` and `evals/fixtures` made read-only folders by `chmod`, which gives
`Permission denied`. A read-only file alone blocks nothing, because git replaces a file through its
folder. The states matched wherever both were measured.

**The exit code does not tell you.** Under the real sandbox, a failed `git switch` exits 0.

| Operation | Exit | Prints | State afterwards |
|---|---|---|---|
| `git switch` | **0** | `error: unable to unlink old '…'`, then `Switched to branch` | **Branch moved.** Everything updated except the protected files, which keep the old branch's content and show as ` M` |
| `git switch` to a branch lacking a protected file | **0** | `warning: unable to unlink '…'`, then `Switched to branch` | **Branch moved.** The file stays behind as `??` |
| `git merge`, fast-forward | 1 | `error: unable to unlink old '…'`, then `Updating …` | HEAD did not move, but other files did: they show as ` M`, ` D` and `??`. The protected files show nothing |
| `git merge`, true merge | 2 | `error: unable to unlink old '…'`, then `Merge with strategy ort failed.` | HEAD did not move. Tracked files are back as they were; files the merge added stay as `??` |

No failed merge left a `MERGE_HEAD`, so `git merge --abort` does not work.

**The danger:** after a failed switch, `git commit -a` commits the old baseline onto the new branch.
This was measured too. So does anything else that stages the two files.

### Detect

After any git command that updates the working tree, look for `unable to unlink`,
`unable to create file` or `cannot create directory` in the output, whatever the exit code. Claude
stops and reports this, and does not commit, because it cannot repair the state from the sandbox.

**Only `switch` and `merge` were measured and have a recovery.** If `pull`, `rebase`, `cherry-pick`,
`reset`, `stash` or any other command prints one of these, stop: no recovery is written for it.

### Recover from your terminal

The broken worktree is not repaired. You save what is yours, build a fresh worktree on the right
branch, put your work there, and discard the broken one. Nothing runs `git clean` or `git restore`.
The broken worktree's files stay where they are until step 8. After a merge at step 4 the branch has
moved underneath them, so from then on go forward, not back.

Each block is one chained command, so a failure stops it, and it prints its last line only when
everything worked. If a block fails, stop. Do not run the next one.

**This covers a linked worktree on a branch, after a failed `git switch` or `git merge`.** If the broken
tree is the main checkout, or `git branch --show-current` prints nothing because HEAD is detached, stop:
this does not apply.

The blocks in steps 3 to 6 and 8 were run verbatim as zsh script files, on 18 cases with read-only
folders in place of the real sandbox. Steps 1, 2 and 7 were not run, and nothing was pasted into an
interactive shell. The cases: a failed switch, a switch to a branch lacking a protected file, a
fast-forward, a true merge, a `--no-ff` merge, a branch adding a file named `*`, a branch changing a
file your side deleted, both sides making the same change with an uncommitted edit on top, nothing
uncommitted at all, detached HEAD, a failed `worktree add`, your own files in the protected paths, a
rerun of step 3, `ff` set wrongly each way, and rig run 2's exact fast-forward state, with and without
your own work beside it.

**1. Close any Claude session in the broken worktree, and set the names.** After a failed switch, leave
`merged` and `ff` empty. After a failed merge, set `merged` to the branch you were merging. Set `ff=yes`
only if the merge printed `Updating …` and did not print `Merge with strategy ort failed.`. A `--no-ff`
merge prints the second, so leave `ff` empty for it. If you are unsure, leave `ff` empty: that fails
safely at step 6.

```
main=~/Projects/northwind-shop
broken=~/Projects/northwind-shop-b
fresh=~/Projects/northwind-shop-recover
save=~/recovery-$(date +%s)
merged=
ff=
```

**2. Check the pointer before running git in the broken worktree.** The first line must print
`gitdir:` and a path under `$main/.git/worktrees/`, and the second must list `$broken`. If either does
not, stop. The second line runs git from the main checkout, so it does not trust the pointer.

```
cat "$broken/.git"
git -C "$main" worktree list
```

**3. Save your work and build the fresh worktree.** It must end by printing `saved, and the fresh
worktree is built`. It stops on detached HEAD, and if `$save` already exists, so a good save cannot be
overwritten.

A failed merge leaves the branch's changes in the tree, such as ` M src/other.txt` and `?? src/new.txt`
with HEAD unmoved. **Those are not your work, and must not be saved as yours.** So this block compares
the tree with temporary indexes built from the commit you are aiming for, not your real index, which it
never touches. A file the merge added then counts as tracked, so it appears in neither the patch nor
the archive. On that state with nothing of yours, both come out empty.

The patch and the archive skip the two protected paths, because they can hold the wrong content. Those
paths are copied as they stand into `$save/protected/` instead, and never put back for you. The `git status` line shows what is in them: if it lists a new file of yours, or your own edit to
`known_failures.json`, copy that back by hand after step 8. `-f` is needed because the branch is still
checked out in the broken worktree.

```
cd "$broken" &&
branch=$(git branch --show-current) &&
[ -n "$branch" ] &&
base=HEAD &&
if [ "$ff" = yes ]; then base=$merged; fi &&
target=HEAD &&
if [ -n "$merged" ]; then target=$merged; fi &&
mkdir "$save" "$save/protected" &&
GIT_INDEX_FILE="$save/base.index" git read-tree "$base" &&
GIT_INDEX_FILE="$save/target.index" git read-tree "$target" &&
GIT_INDEX_FILE="$save/base.index" git diff --binary "$base" -- . ':(exclude)evals/baselines' ':(exclude)evals/fixtures/known_failures.json' > "$save/tracked.patch" &&
GIT_INDEX_FILE="$save/target.index" git ls-files --others --exclude-standard -z -- . ':(exclude)evals/baselines' ':(exclude)evals/fixtures/known_failures.json' | tar --null -T - -cf "$save/untracked.tar" &&
cp -R evals/baselines "$save/protected/" &&
if [ -e evals/fixtures/known_failures.json ]; then cp evals/fixtures/known_failures.json "$save/protected/"; fi &&
git status --short -- evals/baselines evals/fixtures/known_failures.json &&
git -C "$main" worktree add -f "$fresh" "$branch" &&
echo "saved, and the fresh worktree is built"
```

**4. After a failed merge, redo it in the fresh worktree.** After a failed switch this does nothing. A
conflict here is a normal merge conflict, with no sandbox involved: resolve and commit it before step
5.

```
cd "$fresh" &&
if [ -n "$merged" ]; then git merge --no-edit "$merged"; fi
```

**5. Read what will be put back.** The first list is the patch and the second is the archive. Both
should show only your own work, and empty is correct if you had none. If the patch shows the branch's
changes, forwards or reversed, `ff` was wrong: stop, and do not run step 6.

```
cd "$fresh" &&
if [ -s "$save/tracked.patch" ]; then git apply --stat "$save/tracked.patch"; fi &&
tar -tf "$save/untracked.tar"
```

**6. Put your work in the fresh worktree.** If `git apply` refuses, nothing changed: read
`$save/tracked.patch` and redo those edits by hand.

```
cd "$fresh" &&
if [ -s "$save/tracked.patch" ]; then git apply "$save/tracked.patch"; fi &&
tar -xkf "$save/untracked.tar"
```

**7. Confirm, in the fresh worktree.** The second line should show only your work. The third should
list only changes you meant to make. If it lists a baseline change you did not make, a wrong baseline
was committed before this started: do not merge, and tell Claude.

```
cd "$fresh" &&
git status --short --branch &&
git diff --stat origin/main -- evals/baselines evals/fixtures/known_failures.json
```

**8. Discard the broken worktree and move the fresh one to its path.** This is the step you cannot
undo, so keep `$save` until you are happy. It does nothing unless the fresh worktree exists, and it
must end by printing its last line. It also deletes the broken worktree's ignored files, such as
`.venv`, `node_modules` and `.llm-proxy-log`, including any eval candidate.

```
cd "$main" &&
[ -f "$fresh/.git" ] &&
git -C "$main" worktree remove --force "$broken" &&
git -C "$main" worktree move "$fresh" "$broken" &&
echo "broken worktree discarded, fresh worktree moved to its path"
```

**9. In a new Claude session there, have Claude run** `make setup`, `make runner-check`,
`make eval-integrity` and `make eval-gate`. These run in the sandbox, not your terminal, for the reason
in "Open limits". `make eval-gate` is what compares the baseline with the recordings.
