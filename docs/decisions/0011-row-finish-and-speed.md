# 0011: One finish command, routine rows, faster local runs, and the GOV-1 decisions

Date: 2026-11-07 · Row: AUTO-2 · Status: decided by the user at plan approval

Plan and the user's answers: `docs/reviews/2026-11-07-AUTO-2-plan.md`.

## GOV-1 decisions

The nine `needs the user` items in `docs/governance/threat-model.md`, "Owners", are now `decided`:

- **RAG-12 second-pass F9 to F13, F15:** closed as unrecoverable. Only the numbers survive, and 5a and 5b
  re-reviewed the gate.
- **5a finding 15:** closed, covered by 5b's `denyWrite`, the ask rules and `baseline-label`.
- **`allowLocalBinding` (F4):** accepted for the lab; revisit before non-lab use.
- **Shared caches (F6):** accepted for the lab.
- **`make enrich` ask rule:** fixed in AUTO-2, item 5.
- **Internal-only text to the provider:** not acceptable outside a lab without reviewing the provider's data
  terms.
- **PRD §7 boundary gap:** the PRD stays unchanged, and `threat-model.md`, Boundary 7, is the record.
- **Device bridge:** the advisor reads only connected folders, never `~/.northwind`, and writes only when asked.
  This is a stated policy. Nothing measures it, and it does not say what the bridge keeps.
- **`make eval-gate-stale-ok`:** the user approves it.

GOV-1's findings 8 and C, the tests with no failing run, are accepted.

Row 5b is `done` on the user's statement. This session has no GitHub access, so the records say "stated by the
user" for what happened there.

## `make finish`

One user-run command closes a row: it commits what `/req-done` wrote, pushes, opens the PR from a committed
body file, adds labels, and runs `ship`, which still asks. It runs from the clone, because it holds the user's
GitHub login, and reaches it only through `runner-sync`.

- **`ci-change` is added by `finish`, so that label is weaker.** `ship`'s account check (decision 0008) still
  passes, but it no longer shows that a person chose it. What is left is the merge question, with the full diff
  of `.github/workflows` above it. The user accepted this at plan approval.
- **`baseline-change` stays by hand**, because it guards a High residual, and **`gate-change` is never added**: it
  accepts a failure that has not happened yet, so adding it before CI would pre-waive main's gate.
- **`NW_ALLOW_CONTROL` is never set.** If the hook refuses, the user reads the paths and reruns with it.

## Routine rows

`CLAUDE.md`, "Running a row", defines them. On a routine row `advisor-reviewer` approves the plan in place of
stop 1. Stops 2, 3, 6, 7 and 10 are the user's four reasons to stop. Stops 4, 5, 8 and 9 stay, because a row
that hits one has stopped being routine.

The user decided at plan approval that a routine row may add cassettes through `make record` in a window they
opened, so stop 8 does not fire for that one case. What "through `make record`" excludes (a modified, deleted or
hand-written cassette, anything added under `cassettes/eval-answers/`, `make eval-live`, `make enrich`) is the
advisor's reading of those words, adopted here.

**Residual.** The session briefs the advisor, so this approval catches mistakes, not a session steering its own
review. The user's named scope is the real bound. An added cassette's response cannot be verified either: it is
written in a session-writable tree, and the ledger holds no response hash. So that exception runs, with no plan
approval, over the open forged-added-cassette residual (`docs/governance/threat-model.md`, Residuals). The rule
is one the session keeps; no check proves it. `ship` still lists the paths and asks for the PR number.

## Speed

`make test` skips `test_ship.py`, `test_pre_push.py`, `test_refused_paths.py` and `test_finish.py` only when
`tools/`, `tests/ship/`, `.github/` and `Makefile` match the merge base with main. GitHub CI's plain
`uv run pytest` still runs them: `addopts` and `ci.yml` do not change. `test_guard.py` stays in `make test`.

`make ci` runs once in the chain, after the commit, so the SHA it ran at is the SHA reviewed. `make eval-gate` is
out of the chain because `make ci` holds it. `/req-done` keeps its own `make ci`, by the user's choice, so a row
costs two runs; its step 3 reads the gate result from step 1. `make check` is for during a row and is never
evidence.

## What the security review changed

`security-reviewer` read the row at `e9ae0c4`: one critical and one high, both in `tools/finish.sh`.

- **Critical, confirmed by running it.** A gitlink in the index makes `git status` run a child git in that folder,
  with that folder's config, so a planted `core.fsmonitor` runs outside the sandbox. The session can do all of it: the
  sandbox lets it write a nested `.git` file (measured 2026-11-07) and the index. `finish` now refuses a gitlink before
  anything reads the working tree, and runs `status --ignore-submodules=all` with `core.fsmonitor=false`.
  `tools/refused-paths.sh` and the pre-push hook refuse a committed gitlink.
- **The same route is open for any `git status` outside the sandbox**, which includes the user's own terminal and may
  include an editor's git panel. `git config --global diff.ignoreSubmodules all` closes it (measured). That setting
  is the user's to make: the session cannot write it.
- **High.** `CHECKOUT` could be any directory, and git trusts that directory. `finish` now takes only
  `~/Projects/northwind-shop`, or a linked worktree beside it whose `.git` file points into its worktrees folder.
- **Routine rows exclude only hard rules 1 and 9, as the plan the user approved says.** Rules 6, 7 and 8 are not
  excluded, so a routine row can change agent tool scoping or the shopper access filter with no person reading the
  plan. The review rated this medium. It is left as approved, with the paths now named, for the user to decide.
- **Stop 1 applies to every row until the `advisor-reviewer` proposal is installed**, because the installed copy
  cannot check that a row is routine.
- **`SHIP_FILTER` reads the local `origin/main`, which the session can write.** That weakens local evidence only:
  GitHub's `CI / python` still runs the ship tests, and `ship` requires that job. `make test` prints a line when it
  skips them.
