# 0009: Promotion from the clone, and the gate from main's copy

Date: 2026-11-05 · Row: 5b (RAG-12, no new PRD ID) · Status: accepted by the user

Closes 5a review findings 1 and 2 (decision 0006) and AUTO-1 security finding 3 (decision 0008).

## Context

`make eval-promote` ran the checkout's code in your terminal, with no sandbox. `denyWrite` protects the
working files, not what gets committed. And CI ran the PR's own gate code, so a one-line change to
`BASELINE` in `cli.py` could repoint the gate and still go green.

## Decision

1. **Promotion runs from the runner clone, and the checkout is data.**
   `make -C ~/.northwind/runner eval-promote CHECKOUT=$HOME/… APPROVED_BY="…"` runs on `.venv-runner` with
   `python -I`. Use `$HOME`, because zsh does not expand `~` there. Outside the clone the target refuses,
   and it keeps its name so the installed `ask` rules still match. It also refuses unless the clone is
   clean, at its `origin/main` and at the real main from `git ls-remote`, with no spending window open.
2. **The code lives in `services/llm-replay`**, as `northwind_llm_replay.promotion`, because
   `RUNNER_SYNC_PATHS` already shows that path in full. It takes the gate's rules from the clone's
   `evals/src`, and refuses unless `northwind_evals` and its `gate`, `items` and `metrics` modules really
   loaded from there. `RUNNER_SYNC_PATHS` gains `evals/src`, `evals/baselines` and the two fixture files
   promote reads.
3. **What decides comes from the clone:** the old baseline and `known_failures.json`, read through the same
   `O_NOFOLLOW` walk as the checkout.
4. **Three files are read from the checkout, and no git runs in it:**
   - the candidate, parsed strictly;
   - the item set, which may add items and reword the query of an item that is neither zero-tolerance nor
     human-reviewed, but may not lose an item or move one to another segment. Any other change is a relabel;
   - `known_failures.json`, which may only shrink, word for word.
5. **A relabel needs a reason and an approver.** These decide what an item is tested for:
   `expected_behaviour`, `reviewed_by`, `relevant_sources`, `reference_facts`, `region` and `trap_id`, and the
   `query` of a zero-tolerance or human-reviewed item, because a harmless query lets containment pass on a test
   that no longer attacks anything. `relevant_sources` never reaches a model, so widening it hides a retrieval
   drop with no recording going stale. `why` reaches no gate and is not included. A relabel is not refused
   outright, because a legitimate one has to land with its baseline. It is recorded in `accepted` as
   `<item> <field>: <old> -> <new>`.
6. **An approval covers what you read.** `CHECK=1` prints one sha256 over the three files the checkout can
   change: the candidate, the item set and `known_failures.json`, length-prefixed. A reason is accepted only
   with `CANDIDATE_SHA=<hex>` from that run, so any of them rewritten after you read it is refused. This
   applies to anything a reason accepts: a regression or a relabel.
7. **The only write is the baseline in the named checkout.** The candidate stays, and `.gitignore` covers
   it. Every path is opened one folder at a time with `O_NOFOLLOW`, the checkout root included.
8. **`APPROVED_BY`, `ACCEPT_REGRESSION` and `BOOTSTRAP` are literal.** They arrive through the environment,
   so `--check` is a value, and they are stored as given. Blank or unprintable text is refused, because a
   blank approver would fail every later gate and overrides are append-only. `BOOTSTRAP` is refused when
   a baseline exists, because its record would say there was none.
9. **A citation score under the floor is final.** No reason accepts it. Hard rule 5 is absolute.
10. **`Guard / baseline-label` fails a PR that changes `evals/baselines`, `known_failures.json` or
    `starter_set.jsonl` without the `baseline-change` label**, and `ship` refuses the same. The relabel rule runs
    only at promotion, and a PR need not promote: widened `relevant_sources` would hide a recall drop from main's
    gate with nothing else asking. Its Python runs with `-I`, so a PR's `json.py` cannot answer for it.
11. **`Guard / eval-integrity-main` runs main's `integrity.py` with nothing from the PR loaded:** the system
    `python3 -I`, no `uv sync`, and main's `evals/src` as the only addition to the path. Nothing accepts a failure
    of this job. It also checks, reading files only:
    - each workspace `src/` holds only `northwind_<member>`;
    - the root `pyproject.toml` stays `package = false` with no build system, and its workspace `members` are
      `services/*`, `services/<name>`, `data` or `evals`;
    - each member's `pyproject.toml` has no `[tool.uv.build-backend]` and no workspace of its own, builds with
      `uv_build` alone, with no `backend-path` and no requirement beyond `uv_build` and a version;
    - a `path` entry in any `[tool.uv.sources]`, root or member, leads to `services/<name>`, `data` or `evals`.

    So `services/<name>/src`, `data/src` and `evals/src` are the only workspace load paths, and a member's
    `[build-system]` cannot run PR code. **This does not cover** what `uv.lock` pins: a new or changed registry,
    git or URL dependency, including a wheel carrying its own `.pth`. Nor does it cover `uv.toml` and
    `.python-version`, a file planted in `evals/src`, which the restore removes before this check sees it, or an
    edit to this script or the workflow. Those are in `ship`'s full diff, or stated below.
12. **`Guard / eval-gate-main` runs main's `evals/src` beside the PR's services.** `PYTHONPATH` puts it first.
    `assert-main-code.py` then reads each module's origin from the import system, because a module sets its
    own `__file__`, and refuses a folder on `sys.path` that is not a member's `src`, by its exact place. It runs
    after `site`, so a `sitecustomize.py` that loads can defeat it: `os._exit(0)` did from a folder on
    `PYTHONPATH`. Through a `.pth`, Homebrew's Python loads its own stdlib `sitecustomize` first, and CI's is
    unmeasured. Clause 11 is the control for that. The restore and the check run again right before the gate,
    because the index build runs PR code.
13. **Both jobs fetch main explicitly:** `refs/heads/main` into `refs/remotes/origin/main` at full depth, and a
    shallow clone is refused. A failed fetch or restore fails the job: nothing falls back to the PR's copy.
14. **`CI / eval-gate` stays, running the PR's own copy.** That and the PR's `evals/tests` are how a legitimate
    gate change gets tested.
15. **`ship` may accept one failure: `Guard / eval-gate-main`, with bucket `fail`, on a PR that changes
    `evals/src`, with a `gate-change` label last added by your account.** Row #8 will need this, because it
    changes `_live` in `cli.py`. `fail` is one bucket for the whole job, so it also covers a failed restore or
    assertion: read that job's log before adding the label.
16. **Add `Guard / baseline-label` and `Guard / eval-integrity-main` as required checks in the main ruleset, not
    `eval-gate-main`.** Requiring that one would block the failure `ship` is allowed to accept. Until you add
    them, a merge outside `ship` is not stopped by these checks.
17. **`ship`, `runner-sync` and the pre-push hook refuse a tracked symlink anywhere in the tree.** It checks out
    clean, so `git status` cannot show that what the clone trusts points somewhere the session can write. The
    repo tracks none. `NW_ALLOW_CONTROL=1` skips the whole hook; `ship` and `runner-sync` have no such override.
18. **Every target the clone runs uses an isolated Python, `-I`.** `llm-spend` was the one gap.

## Why these shapes

- **Guard is its own workflow, with no push trigger and no job-level `if`.** Push runs report on the same
  commit, and `ship` refuses any check that did not pass, `skipping` included. A job that skipped itself on
  push would refuse every PR.
- **There is no waiver in CI.** A waiver would sit in a PR-editable file, and a re-run keeps its old labels.
  So red stays red, and `ship`, running from the clone, reads the label live and checks who added it.
- **The load order is why the restore is not enough.** With `PYTHONPATH` unset and `PYTHONSAFEPATH=1`, the order
  is `services/assistant/src`, `services/common/src`, `data/src`, `evals/src`, then gateway, llm-replay and rag.
  `services/assistant` is not in `RUNNER_SYNC_PATHS`, so a package planted there shows in `ship` as a file name
  only. An earlier version of this decision said the repo root came first: that reading came from a shell whose
  `PYTHONPATH` starts with `:`.
- **A row that adds adversarial items must land them with their baseline**, because `coverage_problems` fails
  a set with an ungated zero-tolerance item. So the clone's item set cannot be the only one read.
- **Fixing a known failure must land with its baseline too.** Left in, `now passes` refuses for good. Removed
  first, the old recording fails containment.

## Residual risk, stated

- **The Guard workflow and its scripts are PR-editable, so a commit could change them.** Accepted by the user
  for `baseline-label`: `ci-change` and `ship`'s full diff cover it. The same applies to the two eval jobs.
  `baseline-label` stays on `pull_request` by the user's choice.
- **`eval-gate-main` is not a boundary.** `uv sync --locked` runs on the PR's tree before the restore, and the
  PR's `services/rag`, `llm-replay` and `common` run in the same process as main's gate. A PR-edited
  `[build-system]` would run in that job, but clause 11 fails it in a job nothing accepts. What a lock-pinned
  dependency brings is not covered. `uv.lock` and those paths appear in `ship`'s full diff. Closing this means
  installing from main's manifests, a design change the user chose not to make. None of it reaches
  `eval-integrity-main`, which installs nothing. PR code in the index build can also write `$GITHUB_ENV` or leave
  a process running past the second assertion: nothing beyond this same residual.
- **Promotion cannot prove scores are real.** Forged answer scores fail `score_drift` in CI unless they come with
  a forged new cassette, and `eval-integrity` refuses only modified ones (0006, finding 11, still open).
- **Small retrieval drops have no such check.** `score_drift` reads answers only, so a candidate can lower
  `recall@5` or `mrr` within tolerance with nothing forged, and repeating that ratchets the bar down. **Until row #8
  measures whether a drift check is stable across machines, the tolerance is the only guard on small retrieval
  drops (5a F7).**
- **Reading the `baseline-change` PR is the check on both of those.**
- **CI cannot see who added a label.** `ship` checks the actor.
- **With `gate-change`, green means the PR's own gate passed**, not that the gate is intact. `ship` says so
  above its question.

## Unmeasured

- How `gh pr checks` treats an earlier failed Guard run on the same commit once a later one passes. If it keeps
  the `fail`, `ship` refuses, which fails safe. The first baseline PR, row #8, will show it. `gh pr create
  --label baseline-change` may avoid the failed run.
- That the Guard jobs behave on GitHub as they do here. This row's own PR is the first run.
- Whether a required check matches on the job name alone. From a reviewer's memory, unconfirmed: pin the source
  to GitHub Actions when you add it.

## The first ship

`tools/ship.sh` and the `Makefile` reach the clone only by runner-sync (0008, Limits), so this row's PR is
checked by the old `ship`. That version knows nothing of Guard and treats its jobs as extra checks that must
pass. It shows `evals/src` by name only, so that diff has to be read by hand.

After merge, two things are yours to install: the settings proposal, which gains a `denyWrite` entry for
`.git/config.worktree` and an `ask` rule, and `tools/pre-push.proposed.sh`, which gains the symlink check.
