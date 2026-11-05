# 0008: A row runs from approved plan to gate report

Date: 2026-11-05 · Row: AUTO-1 · Status: accepted by the user

## Context

The user relayed a prompt between every step of a row. Almost none of those prompts carried a decision.
The steps that do need the user are few, and most of them the session cannot perform anyway: it has no
GitHub credentials and cannot start Docker.

## Decision

1. **After plan approval the session carries on alone**, through the chain in `CLAUDE.md`, "Running a
   row", and stops only for the conditions listed there.
2. **The session may commit on the row's branch without being asked. It never pushes.**
3. **`advisor-reviewer` checks the process**, as `security-reviewer` checks the code, and also checks
   any revised plan before the user sees it.
4. **`advisor-reviewer` reviews only the output of `make review-diff`.** Its first two lines give the
   base and head SHA, so a stale or partial diff reads as `unconfirmed`. Those lines come from refs the
   session can write, and the session supplies the SHAs to compare, so they catch accidents only.
5. **The user merges with `make -C ~/.northwind/runner ship PR=<n> SHA=<reviewed>`.**
6. **Check before the push.** CI runs on `push`, so a session-written workflow runs with a token before
   `ship` is involved. `tools/pre-push.proposed.sh`, installed in `.git/hooks/pre-push`, refuses such a
   push unless `NW_ALLOW_CONTROL=1` is set. The sandbox cannot write `.git/hooks` (measured 2026-11-05 with
   `test -w`). See Limits: this is a local check. The user checks the repo settings themselves.

## Where this departs from the request

The user approved each of these:

- The git stop cites all five error strings, not only `Operation not permitted`. A failed switch can
  print one of the others and still exit 0.
- "Stop only" does not cancel the existing ask-first rules for human-reviewed eval labels and budget caps.
- Two failed attempts at the same failure is a stop, otherwise the chain has no end.
- `ship` asks before merging, with the control surface list directly above the question. When any control
  surface path changed, the answer is the PR number; otherwise `y`.

## What the security review changed

The review found that the design assumed too much around `ship`. The user's decisions:

- **Label actor (finding 1).** "Only the user can add a label" holds for the session, not for a workflow
  it wrote. `ship` reads the issue events and requires the latest add of each label to be by the account
  `gh api user` reports, with actor type `User`.
- **Reviewed SHA (findings 6 and 9).** `ship` takes the commit the reviewers read, and refuses if anything
  after it touches more than `PROGRESS.md` and `COVERAGE.md`. So a head that moved after `SHA` was chosen
  cannot merge. It does not bind a label to a head: `SHA` comes from the session's gate report, so a label
  added at an earlier head still passes at a later reviewed one. `ship` therefore shows the full diff of
  the label-gated paths above its question, and the user reads that.
- **Refused paths (finding 2).** `ship` and `runner-sync` refuse any `GNUmakefile` or `makefile` case
  variant, `.env*`, `.venv*`, any path the checked-out `.gitignore` ignores, any path outside printable
  ASCII, and any tree with paths that differ only by case. Measured: a committed `GNUmakefile` runs instead
  of `Makefile`, `git merge` overwrites an ignored file, and on this filesystem a path spelled with U+017F
  lands in `services/` unseen by any ASCII match. A name list will keep missing things; the durable fix is
  showing the full diff of everything outside an allow-list, logged as AUTO-2.
- **Control surface (finding 3).** Widened to what CI executes. CI still runs the PR's own gate code, so a
  green `eval-gate` does not prove the gate is intact. Running the gate from main's copy is open under
  row 5b. `tests/**` stays off the surface by the user's choice.
- **Spending window (finding 13).** `ship` and `runner-sync` refuse while the runner port answers.

## What the advisor review changed

`advisor-reviewer` read `45707cc..4fd3d77` and returned `findings`: three medium, three low, no high. The
user's triage is that none blocks this row. All six are in `docs/reviews/2026-11-05-auto-1-evidence.md`.
Two of them settle something here:

- **`.github` is not a refused path, on purpose.** The refusals in decision F2 are for paths the runner
  loads, so `tools/refused-paths.sh` leaves `.github/**` out, although the F1 list it refers to starts with
  it. `.github/**` is refused at push by the hook. In `ship`, workflows need the `ci-change` label. Other
  `.github` paths get no label and no control surface entry until AUTO-2 adds them.
- **The fixes for re-review F1 and F2 had no second `security-reviewer` pass.** The user's advisor read
  `tools/pre-push.proposed.sh`, `tools/refused-paths.sh`, `tools/ship.sh` and the `Makefile` changes at
  `5840f97`, and accepted the fixes in place of that pass. This is recorded from the user's triage. The
  `advisor-reviewer` subagent did not give this acceptance: its finding is that no reviewer had read them.

## How `ship` decides

- **Paths come from `git diff`, not from `gh`.** `gh`'s file list hides the old side of a rename.
  Main and the PR head are fetched together, because the clone's `origin/main` moves only at runner-sync.
  `--refmap=` keeps it there: `preflight()` in `runner.py` refuses a clone off `origin/main`.
- **Job outcomes come from `gh pr checks --json`.** Read from the `gh` source on 2026-11-05: `--json`
  returns before the failed and pending exit logic, and `--watch` cannot be combined with it. So
  `--watch` only waits. `python`, `eval-gate`, `e2e` and `portal` must exist in workflow `CI`, and every
  check reported, in any workflow, must have bucket `pass`.
- **`.github/workflows/**` needs the `ci-change` label.** `ci.yml` runs on `pull_request`, so a PR is
  tested by its own workflow and green CI proves nothing about that change.
- **`evals/baselines` and `known_failures.json` need `baseline-change`**, the label row 5b also checks
  in CI. Both labels must have been added by the user's account, as above.
- **Minimum `gh` is 2.103.3**, the oldest version whose help was read. It may be stricter than necessary:
  the release that added `gh pr checks --json` was not established. The script also probes for the flags.

## Limits

- The tests use a fake `gh`. They prove how `ship.sh` reacts, not what real `gh` returns. Until a real PR
  has shipped, that part is unverified, including the `workflow` field, the job names and the shape of
  the issue events.
- If `main` ever holds a refused path, for example one merged by hand, `runner-sync` refuses until the
  path is gone. That is deliberate: it stops the clone, not the repo.
- The pre-push hook is a local check, not a boundary. It asks the server for main, and turns off replace
  refs and grafts, but the sandbox can write `.git/objects`, so it cannot be a hard boundary against
  something that writes `.git`. `NW_ALLOW_CONTROL=1` and `--no-verify` also skip it. Only a server-side rule
  closes this, and the user is checking the repo settings.
- Labels and the checks of `ship` protect against mistakes and against sandbox-written changes. They do
  not protect against someone with the user's GitHub login.
- `tools/ship.sh` and the `ship` target reach the runner clone only by runner-sync, so a PR that adds or
  changes them is not checked by its own version.
